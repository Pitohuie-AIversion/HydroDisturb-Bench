"""Unit tests for COMSOL Java API contract.

These offline tests verify error mapping, argument validation, baseline immutability,
and output schema contracts using controlled mocks without requiring a live COMSOL solver.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    ERR_API_UNSUPPORTED,
    ERR_COMSOL_CONNECTION_FAILED,
    ERR_INVALID_PARAMETER,
    ERR_INVALID_UNIT,
    ERR_MODEL_NOT_FOUND,
    ERR_MODEL_VALIDATION_FAILED,
    ERR_NODE_NOT_FOUND,
    ERR_SOLVER_FAILED,
    ApiUnsupportedError,
    ComsolConnectionError,
    ComsolError,
    InvalidParameterError,
    InvalidUnitError,
    ModelNotFoundError,
    ModelValidationError,
    NodeNotFoundError,
    SolverFailedError,
    _posix_rename_noreplace,
    _run_bridge,
    compute_file_hash,
    find_comsol_root,
    inspect_model,
    list_quarantine_records,
    map_error,
    publish_staging_file,
    quarantine_staging_file,
    recover_unclaimed_staging,
    run_study,
    task_staging_session,
    update_parameter,
)


def _make_fake_comsolbatch(tmp_path: Path) -> Path:
    """Helper to create dummy comsolbatch binary for both Windows and Linux testing."""
    p_win = tmp_path / "comsolbatch.exe"
    p_nix = tmp_path / "comsolbatch"
    p_win.write_bytes(b"fake binary")
    p_nix.write_bytes(b"fake binary")
    return p_win if sys.platform == "win32" else p_nix


def test_error_mapping():
    """Verify that all 8 standardized error codes map to typed exceptions."""
    assert isinstance(map_error(ERR_MODEL_NOT_FOUND, "msg"), ModelNotFoundError)
    assert isinstance(map_error(ERR_API_UNSUPPORTED, "msg"), ApiUnsupportedError)
    assert isinstance(map_error(ERR_NODE_NOT_FOUND, "msg"), NodeNotFoundError)
    assert isinstance(map_error(ERR_INVALID_PARAMETER, "msg"), InvalidParameterError)
    assert isinstance(map_error(ERR_INVALID_UNIT, "msg"), InvalidUnitError)
    assert isinstance(map_error(ERR_COMSOL_CONNECTION_FAILED, "msg"), ComsolConnectionError)
    assert isinstance(map_error(ERR_MODEL_VALIDATION_FAILED, "msg"), ModelValidationError)
    assert isinstance(map_error(ERR_SOLVER_FAILED, "msg"), SolverFailedError)
    assert isinstance(map_error("UNKNOWN_CODE", "msg"), ComsolError)


def test_comsol_not_found_raises_connection_error(monkeypatch):
    """When COMSOL cannot be located, ComsolConnectionError must be raised."""
    monkeypatch.setenv("COMSOL_ROOT", "")
    monkeypatch.setenv("COMSOL_DIR", "")
    with patch("sys.platform", "linux"):
        with patch.object(Path, "is_dir", return_value=False):
            with pytest.raises(
                ComsolConnectionError, match="COMSOL installation root could not be located"
            ):
                find_comsol_root()


def test_nonexistent_model_path_rejected(tmp_path):
    """Missing model files must be rejected immediately with ModelNotFoundError."""
    missing = tmp_path / "nonexistent.mph"
    out = tmp_path / "out.mph"

    with pytest.raises(ModelNotFoundError):
        inspect_model(missing)

    with pytest.raises(ModelNotFoundError):
        update_parameter(missing, out, "U_mean", "1.0[m/s]")

    with pytest.raises(ModelNotFoundError):
        run_study(missing, out, "std1")


def test_refuse_to_overwrite_original_model(tmp_path):
    """Attempting to output to the same path as the source must raise ModelValidationError."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"mock mph content")

    with pytest.raises(ModelValidationError, match="Refusing to overwrite original baseline model"):
        update_parameter(base, base, "U_mean", "1.0[m/s]")

    with pytest.raises(ModelValidationError, match="Refusing to overwrite baseline model"):
        run_study(base, base, "std1")


def test_invalid_parameter_arguments_rejected(tmp_path):
    """Empty or whitespace parameter names/expressions must be rejected."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"mock mph content")
    out = tmp_path / "out.mph"

    with pytest.raises(InvalidParameterError, match="name cannot be empty"):
        update_parameter(base, out, "", "1.0[m/s]")

    with pytest.raises(InvalidParameterError, match="expression cannot be empty"):
        update_parameter(base, out, "U_mean", "   ")


def test_bridge_error_propagation(tmp_path):
    """Errors returned in JSON from the Java process must be mapped and raised."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"content")
    fake_exe = _make_fake_comsolbatch(tmp_path)

    err_json = json.dumps(
        {
            "status": "ERROR",
            "error_code": ERR_INVALID_UNIT,
            "message": "Unknown unit: m/s99",
            "details": "Unit parser error trace",
        }
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = f"<<<JSON_START>>>\n{err_json}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(InvalidUnitError, match="Unknown unit"):
                        _run_bridge(["test"])


def test_bridge_node_not_found_propagation(tmp_path):
    """NODE_NOT_FOUND error from Java must raise NodeNotFoundError."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"content")
    fake_exe = _make_fake_comsolbatch(tmp_path)

    err_json = json.dumps(
        {
            "status": "ERROR",
            "error_code": ERR_NODE_NOT_FOUND,
            "message": "Parameter non_existent not found",
            "details": "Available: [H, L]",
        }
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = f"<<<JSON_START>>>\n{err_json}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(NodeNotFoundError, match="Parameter non_existent not found"):
                        _run_bridge(["test"])


def test_inspection_schema_contract(tmp_path):
    """Successful inspect_model must produce compliant dictionary schema."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"base data")
    fake_exe = _make_fake_comsolbatch(tmp_path)

    inspection_payload = {
        "status": "SUCCESS",
        "operation": "inspect_model",
        "model_path": str(base),
        "model_tag": "Model",
        "comsol_version": "COMSOL Multiphysics 6.4",
        "parameters": {
            "U_mean": {"expr": "1[m/s]", "value": 1.0, "unit": "m/s", "description": "Inflow speed"}
        },
        "components": [
            {
                "tag": "comp1",
                "label": "Component 1",
                "geometries": [{"tag": "geom1", "sdim": 2, "features": ["r1"]}],
                "meshes": [
                    {
                        "tag": "mesh1",
                        "sdim": 2,
                        "is_complete": True,
                        "num_elem": 100,
                        "num_vertex": 50,
                        "features": ["size"],
                    }
                ],
                "physics": [
                    {"tag": "spf", "label": "Laminar Flow", "type": "LaminarFlow", "features": []}
                ],
                "materials": [{"tag": "mat1", "label": "Water"}],
            }
        ],
        "studies": [
            {
                "tag": "std1",
                "label": "Study 1",
                "features": [{"tag": "time", "label": "Time Dependent", "type": "Transient"}],
            }
        ],
        "solvers": [{"tag": "sol1", "label": "Solution 1", "features": ["t1"]}],
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = f"<<<JSON_START>>>\n{json.dumps(inspection_payload)}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    res = inspect_model(base)
                    assert res["status"] == "SUCCESS"
                    assert res["model_tag"] == "Model"
                    assert "parameters" in res
                    assert "U_mean" in res["parameters"]
                    assert "components" in res
                    assert len(res["components"]) == 1
                    assert res["components"][0]["tag"] == "comp1"
                    assert "studies" in res
                    assert "solvers" in res
                    assert "sha256" in res
                    assert res["sha256"] == compute_file_hash(base)


def test_baseline_integrity_check_failure(tmp_path):
    """If the baseline model is modified during an operation, a validation error must trigger."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"initial data")
    out = tmp_path / "out.mph"
    fake_exe = _make_fake_comsolbatch(tmp_path)

    def side_effect_modify(cmd, *args, **kwargs):
        # Simulate tool corrupting/modifying the source file
        base.write_bytes(b"tampered data")
        staging_file = Path(cmd[6])
        staging_file.write_bytes(b"valid non-empty staging output")
        res = MagicMock()
        res.returncode = 0
        payload = json.dumps(
            {
                "status": "SUCCESS",
                "operation": "update_parameter",
                "param_name": "U_mean",
                "old_expr": "1",
                "old_value": 1.0,
                "old_unit": "",
                "new_expr": "2",
                "readback_value": 2.0,
                "readback_unit": "",
                "source_model": str(base),
                "output_model": str(out),
            }
        )
        res.stdout = f"<<<JSON_START>>>\n{payload}\n<<<JSON_END>>>"
        return res

    with patch("subprocess.run", side_effect=side_effect_modify):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(
                        ModelValidationError,
                        match="Safety violation: original model file was modified",
                    ):
                        update_parameter(base, out, "U_mean", "2.0")


def test_subprocess_nonzero_exit_code_fails_closed(tmp_path):
    """If comsolbatch returns non-zero without valid SUCCESS JSON, ComsolConnectionError must be raised."""
    fake_exe = _make_fake_comsolbatch(tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = "Fatal Java Virtual Machine error"
    mock_proc.stderr = "OutOfMemoryError"

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(ComsolConnectionError, match="exit code 1"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_invalid_json_fails_closed(tmp_path):
    """If ComsolBridge prints invalid/truncated JSON within sentinels, fail closed."""
    fake_exe = _make_fake_comsolbatch(tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "<<<JSON_START>>>\n{truncated json:\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(ComsolConnectionError, match="Failed to decode JSON"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_preexisting_output_file_strictly_preserved_on_failure(tmp_path):
    """If an operation fails, any pre-existing valid file at output_path must be completely preserved!"""
    base = tmp_path / "base.mph"
    base.write_bytes(b"initial baseline data")
    out = tmp_path / "valid_prior_result.mph"
    out.write_bytes(b"crucial valid pre-existing result")
    prior_content = out.read_bytes()
    fake_exe = _make_fake_comsolbatch(tmp_path)

    def side_effect_fail(cmd_args, **kwargs):
        # Even if the subprocess wrote something to staging or crashed
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '<<<JSON_START>>>\n{"status": "ERROR", "error_code": "SOLVER_FAILED", "message": "divergence"}\n<<<JSON_END>>>'
        return mock_proc

    with patch("subprocess.run", side_effect=side_effect_fail):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(SolverFailedError):
                        update_parameter(base, out, "U_mean", "2.0")

    # Assert pre-existing file at output_path was NOT deleted and NOT altered!
    assert out.is_file(), "Pre-existing output file was erroneously deleted on failure!"
    assert out.read_bytes() == prior_content, "Pre-existing output file was corrupted on failure!"


def test_comsol_midrun_abort_quarantines_and_does_not_publish(tmp_path):
    """If COMSOL aborts mid-run leaving a partial staging file, it is quarantined and not published."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"initial baseline data")
    out = tmp_path / "target_output.mph"
    fake_exe = _make_fake_comsolbatch(tmp_path)

    def side_effect_abort_during_write(cmd_args, **kwargs):
        # The staging path is passed as positional argument 6 to comsolbatch
        staging_file = Path(cmd_args[6])
        staging_file.write_bytes(b"partial corrupted mph bytes")
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "Fatal error inside COMSOL engine"
        mock_proc.stderr = "Native crash"
        return mock_proc

    with patch("subprocess.run", side_effect=side_effect_abort_during_write):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(ComsolConnectionError):
                        update_parameter(base, out, "U_mean", "2.0")

    # Final output must NOT exist
    assert not out.exists(), "Corrupted partial file was published to output path!"
    # Staging file must have been moved to quarantine
    quarantine_files = list((out.parent / "quarantine").glob("failed_*"))
    assert len(quarantine_files) == 1, "Failed artifact was not preserved in quarantine!"
    assert quarantine_files[0].read_bytes() == b"partial corrupted mph bytes"


def test_symlink_output_path_rejected(tmp_path):
    """Output paths pointing to symbolic links must be rejected for safety."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"base")
    real_out = tmp_path / "real_file.mph"
    real_out.write_bytes(b"real")
    sym_out = tmp_path / "sym_link.mph"

    with patch.object(Path, "is_symlink", return_value=True):
        with pytest.raises(ModelValidationError, match="symbolic link"):
            update_parameter(base, sym_out, "U_mean", "1.5")


def test_non_dict_json_fails_closed(tmp_path):
    """If ComsolBridge prints a valid JSON array or primitive instead of object, fail closed."""
    fake_exe = _make_fake_comsolbatch(tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "<<<JSON_START>>>\n[1, 2, 3]\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(
                        ComsolConnectionError, match="expected JSON object, got list"
                    ):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_schema_missing_fields_fails_closed(tmp_path):
    """If ComsolBridge prints SUCCESS but misses required schema fields, fail closed."""
    fake_exe = _make_fake_comsolbatch(tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    # Missing api_specification
    mock_proc.stdout = (
        '<<<JSON_START>>>\n{"status": "SUCCESS", "operation": "inspect_api"}\n<<<JSON_END>>>'
    )
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(ComsolConnectionError, match="Schema violation"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_preexisting_final_file_blocks_successful_run_when_overwrite_forbidden(tmp_path):
    """When allow_overwrite=False (default), existing target file blocks publish and preserves staging artifact in quarantine."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"baseline model data")
    out = tmp_path / "target_existing.mph"
    out.write_bytes(b"prior valuable result that must not be overwritten")
    prior_content = out.read_bytes()

    fake_exe = _make_fake_comsolbatch(tmp_path)

    def side_effect_success(cmd_args, **kwargs):
        # Position 6 in comsolbatch invocation is the staging file
        staging_file = Path(cmd_args[6])
        staging_file.write_bytes(b"newly computed successful result")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '<<<JSON_START>>>\n{"status": "SUCCESS", "operation": "update_parameter", "param_name": "U_mean", "new_expr": "2.0", "readback_value": 2.0}\n<<<JSON_END>>>'
        mock_proc.stderr = ""
        return mock_proc

    # 1. Default allow_overwrite=False must raise FileExistsError
    with patch("subprocess.run", side_effect=side_effect_success):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(FileExistsError, match="overwrite is forbidden"):
                        update_parameter(base, out, "U_mean", "2.0")

    # Original destination file MUST be completely unaltered
    assert out.is_file(), "Original file disappeared!"
    assert out.read_bytes() == prior_content, "Original file was corrupted or overwritten!"

    # Staging directory must be clean (no dangling unmanaged files)
    staging_files = list((out.parent / ".staging").glob("stage_*"))
    assert len(staging_files) == 0, f"Dangling staging files left in .staging: {staging_files}"

    # Quarantined conflict artifact MUST exist with newly computed bytes
    conflict_files = list((out.parent / "quarantine").glob("conflict_*"))
    assert len(conflict_files) == 1, "Conflict staging artifact was not preserved in quarantine!"
    assert conflict_files[0].read_bytes() == b"newly computed successful result"

    # 2. When allow_overwrite=True is explicitly passed, it should replace
    with patch("subprocess.run", side_effect=side_effect_success):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    res = update_parameter(base, out, "U_mean", "2.0", allow_overwrite=True)

    assert res.get("status") == "SUCCESS"
    assert out.read_bytes() == b"newly computed successful result"


def test_concurrent_publish_race_same_target_only_one_succeeds(tmp_path):
    """When two concurrent processes race to publish to the same destination, exactly one succeeds."""
    out = tmp_path / "race_target.mph"
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    stage_a = staging_dir / "stage_taskA_race_target.mph"
    stage_a.write_bytes(b"Task A completed model bytes")

    stage_b = staging_dir / "stage_taskB_race_target.mph"
    stage_b.write_bytes(b"Task B completed model bytes")

    results = []

    def run_publish(stage_path: Path, task_id: str):
        try:
            publish_staging_file(
                staging_path=stage_path,
                output_path=out,
                stage_id=task_id,
                allow_overwrite=False,
            )
            return (task_id, "SUCCESS", None)
        except Exception as e:
            return (task_id, "ERROR", e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futs = [
            executor.submit(run_publish, stage_a, "taskA"),
            executor.submit(run_publish, stage_b, "taskB"),
        ]
        results = [f.result() for f in futs]

    successes = [r for r in results if r[1] == "SUCCESS"]
    failures = [r for r in results if r[1] == "ERROR"]

    # Exactly one succeeded and one failed closed
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}: {results}"
    assert isinstance(failures[0][2], FileExistsError)

    # Destination file exists and contains full uncorrupted content of winner
    assert out.is_file()
    winner_id = successes[0][0]
    expected_bytes = (
        b"Task A completed model bytes" if winner_id == "taskA" else b"Task B completed model bytes"
    )
    assert out.read_bytes() == expected_bytes

    # Loser's staging file was safely moved to quarantine as conflict_*
    loser_id = failures[0][0]
    quarantine_files = list((tmp_path / "quarantine").glob(f"conflict_{loser_id}_*"))
    assert len(quarantine_files) == 1
    loser_bytes = (
        b"Task B completed model bytes" if winner_id == "taskA" else b"Task A completed model bytes"
    )
    assert quarantine_files[0].read_bytes() == loser_bytes

    # Staging directory is clean
    assert not stage_a.exists()
    assert not stage_b.exists()


def test_quarantine_listing_and_recovery(tmp_path):
    """Verify list_quarantine_records and recover_unclaimed_staging utilities."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = staging_dir / "stage_abandoned_model.mph"
    orphan_file.write_bytes(b"abandoned staging file")

    # Recover unclaimed staging
    recovered = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(recovered) == 1
    assert recovered[0].name.startswith("orphan_stage_abandoned_model")
    assert not orphan_file.exists()

    # List quarantine records
    records = list_quarantine_records(tmp_path)
    assert len(records) == 1
    assert records[0]["name"].startswith("orphan_stage_abandoned_model")
    assert records[0]["size_bytes"] == len(b"abandoned staging file")


def test_quarantine_failure_preserves_staging_file_in_place(tmp_path):
    """When quarantine directory transfer fails (e.g. disk/permission error), staging file must be preserved in place, never unlinked."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "stage_test_data.mph"
    staging_file.write_bytes(b"valuable simulation artifact")
    out = tmp_path / "final.mph"

    # Simulate quarantine filesystem failure (e.g. permission denied)
    with patch(
        "hydro_disturb_bench.comsol_java.bridge._safe_quarantine_move",
        side_effect=OSError("Quarantine storage unavailable"),
    ):
        res = quarantine_staging_file(staging_file, out, "test_id", reason="failed")

    # Return value is None indicating transfer failure
    assert res is None

    # Staging file MUST still exist in place with full contents (never unlinked)
    assert staging_file.is_file(), "Staging file was erroneously deleted on quarantine failure!"
    assert staging_file.read_bytes() == b"valuable simulation artifact"


def test_active_task_not_quarantined_by_recovery(tmp_path):
    """An active task currently computing/holding a staging lease lock must NEVER be touched by recovery."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    active_stage = staging_dir / "stage_active1_final.mph"
    active_stage.write_bytes(b"actively computing simulation data")

    orphan_stage = staging_dir / "stage_abandoned2_final.mph"
    orphan_stage.write_bytes(b"abandoned orphan data")

    # Active task holds a task lease session
    with task_staging_session(active_stage, "active1"):
        # Another process runs recover_unclaimed_staging
        recovered, skipped = recover_unclaimed_staging(
            tmp_path, min_age_sec=0.0, return_skipped=True
        )

        # Only the abandoned orphan was recovered
        assert len(recovered) == 1
        assert recovered[0].name == "orphan_stage_abandoned2_final.mph"

        # The active task was skipped and reported
        assert len(skipped) == 1
        assert skipped[0]["stage_id"] == "active1"
        assert skipped[0]["reason"] == "active_task_lease_held"

        # The active task's staging file MUST remain completely intact in .staging
        assert active_stage.is_file(), "Active task file was erroneously moved to quarantine!"
        assert active_stage.read_bytes() == b"actively computing simulation data"

    # After active task finishes, simulate it was cleanly published or cleaned up
    active_stage.unlink()
    orphan_stage2 = staging_dir / "stage_abandoned3_final.mph"
    orphan_stage2.write_bytes(b"more orphan data")
    recovered2 = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(recovered2) == 1
    assert recovered2[0].name == "orphan_stage_abandoned3_final.mph"


def test_source_model_mutation_fails_before_publish_and_quarantines(tmp_path):
    """If source model is modified during computation, fail closed BEFORE publishing and quarantine staging artifact."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"trusted baseline model")
    out = tmp_path / "final_output.mph"
    fake_exe = _make_fake_comsolbatch(tmp_path)

    def side_effect_corrupt_source(cmd_args, **kwargs):
        # Position 6 in comsolbatch invocation is the staging file
        staging_file = Path(cmd_args[6])
        staging_file.write_bytes(b"computed result from corrupted source")
        # Simulate unauthorized modification of source model during computation
        base.write_bytes(b"corrupted or modified baseline content")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '<<<JSON_START>>>\n{"status": "SUCCESS", "operation": "update_parameter", "param_name": "U_mean", "new_expr": "2.0", "readback_value": 2.0}\n<<<JSON_END>>>'
        mock_proc.stderr = ""
        return mock_proc

    with patch("subprocess.run", side_effect=side_effect_corrupt_source):
        with patch(
            "hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe
        ):
            with patch(
                "hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path
            ):
                with patch(
                    "hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir",
                    return_value=tmp_path,
                ):
                    with pytest.raises(
                        ModelValidationError, match="original model file was modified"
                    ):
                        update_parameter(base, out, "U_mean", "2.0")

    # Destination MUST NOT have been published!
    assert not out.exists(), "Final output file was published despite baseline corruption!"

    # Staging file was safely quarantined as failed_*
    quarantine_files = list((out.parent / "quarantine").glob("failed_*"))
    assert len(quarantine_files) == 1, "Failed artifact was not preserved in quarantine!"
    assert quarantine_files[0].read_bytes() == b"computed result from corrupted source"


def test_multiprocess_publish_race_same_target_only_one_succeeds(tmp_path):
    """Verify that multiple independent OS processes racing to publish to the same destination are synchronized by the OS file lock."""
    out = tmp_path / "mp_race_target.mph"
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    stage_a = staging_dir / "stage_procA_mp_race_target.mph"
    stage_a.write_bytes(b"Process A output model bytes")

    stage_b = staging_dir / "stage_procB_mp_race_target.mph"
    stage_b.write_bytes(b"Process B output model bytes")

    worker_code = """
import sys, pathlib
from hydro_disturb_bench.comsol_java.bridge import publish_staging_file

stage = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
task_id = sys.argv[3]
try:
    publish_staging_file(stage, out, task_id, allow_overwrite=False)
    sys.exit(0)
except FileExistsError:
    sys.exit(2)
"""

    sub_env = {**os.environ, "PYTHONPATH": "src"}
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(stage_a), str(out), "procA"], env=sub_env
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(stage_b), str(out), "procB"], env=sub_env
    )

    rc1 = p1.wait()
    rc2 = p2.wait()

    # Exactly one process succeeded (rc=0) and one process was blocked and raised FileExistsError (rc=2)
    assert sorted([rc1, rc2]) == [0, 2], f"Expected [0, 2] from racing processes, got {[rc1, rc2]}"

    # Output file exists and is intact
    assert out.is_file()
    assert out.read_bytes() in (b"Process A output model bytes", b"Process B output model bytes")

    # The loser's staging file was safely moved to quarantine as conflict_*
    quarantine_files = list((tmp_path / "quarantine").glob("conflict_proc*"))
    assert len(quarantine_files) == 1

    # Neither staging file remains in .staging
    assert not stage_a.exists()
    assert not stage_b.exists()


def test_quarantine_collision_never_overwrites_existing_artifact(tmp_path):
    """When multiple quarantine transfers collide on identical target filenames, neither is overwritten."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "colliding_output.mph"

    stage1 = staging_dir / "stage_one.mph"
    stage1.write_bytes(b"Artifact 1 contents that must be preserved")

    stage2 = staging_dir / "stage_two.mph"
    stage2.write_bytes(b"Artifact 2 contents that must not overwrite artifact 1")

    # Quarantine stage1
    q1 = quarantine_staging_file(stage1, out, "shared_stage_id", reason="failed")
    assert q1 is not None and q1.is_file()
    assert q1.read_bytes() == b"Artifact 1 contents that must be preserved"

    # Quarantine stage2 with identical stage_id, reason, and output_path
    q2 = quarantine_staging_file(stage2, out, "shared_stage_id", reason="failed")
    assert q2 is not None and q2.is_file()
    assert q2.read_bytes() == b"Artifact 2 contents that must not overwrite artifact 1"

    # Crucial assertion: q1 and q2 are distinct files; q1 was NOT overwritten
    assert q1 != q2
    assert q1.is_file() and q1.read_bytes() == b"Artifact 1 contents that must be preserved"

    # Both records appear in quarantine listing
    records = list_quarantine_records(tmp_path)
    assert len(records) == 2


def test_fresh_unleased_file_retained_by_recovery(tmp_path):
    """A fresh staging file without a lease file (age < min_age_sec) must be preserved in .staging."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    unleased_file = staging_dir / "stage_fresh_unleased_model.mph"
    unleased_file.write_bytes(b"active computation data in progress")

    # When min_age_sec is large, it must be skipped and retained in staging
    recovered, skipped = recover_unclaimed_staging(tmp_path, min_age_sec=60.0, return_skipped=True)
    assert len(recovered) == 0
    assert len(skipped) == 1
    assert "fresh_unleased_file" in skipped[0]["reason"]
    assert unleased_file.is_file(), "Fresh unleased file was prematurely moved to quarantine!"
    assert unleased_file.read_bytes() == b"active computation data in progress"

    # When min_age_sec is 0 (confirmed orphan after waiting), it is recovered
    recovered2 = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(recovered2) == 1
    assert recovered2[0].name.startswith("orphan_stage_fresh_unleased_model.mph")
    assert not unleased_file.exists()


def test_recovery_reentrance_is_idempotent(tmp_path):
    """Repeated calls to recover_unclaimed_staging must be safe, idempotent, and non-destructive."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    orphan1 = staging_dir / "stage_task1_output.mph"
    orphan1.write_bytes(b"orphan run 1")

    orphan2 = staging_dir / "stage_task2_output.mph"
    orphan2.write_bytes(b"orphan run 2")

    # First recovery
    rec1 = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(rec1) == 2

    # Second recovery immediately (re-entry): staging is empty, no-op
    rec2 = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(rec2) == 0

    # Quarantine records remain intact
    records = list_quarantine_records(tmp_path)
    assert len(records) == 2

    # Add a third orphan and run recovery again
    orphan3 = staging_dir / "stage_task3_output.mph"
    orphan3.write_bytes(b"orphan run 3")

    rec3 = recover_unclaimed_staging(tmp_path, min_age_sec=0.0)
    assert len(rec3) == 1

    records2 = list_quarantine_records(tmp_path)
    assert len(records2) == 3


def test_multiprocess_quarantine_race_no_overwrite(tmp_path):
    """Multiple processes racing to quarantine files to the same target name do not overwrite each other."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "shared_race_out.mph"

    stage_a = staging_dir / "stage_procA.mph"
    stage_a.write_bytes(b"Quarantine payload from process A")

    stage_b = staging_dir / "stage_procB.mph"
    stage_b.write_bytes(b"Quarantine payload from process B")

    worker_code = """
import sys, pathlib
from hydro_disturb_bench.comsol_java.bridge import quarantine_staging_file

stage = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
task_id = sys.argv[3]
res = quarantine_staging_file(stage, out, task_id, reason="conflict")
if res is not None:
    sys.exit(0)
else:
    sys.exit(1)
"""

    sub_env = {**os.environ, "PYTHONPATH": "src"}
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(stage_a), str(out), "race_task"], env=sub_env
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(stage_b), str(out), "race_task"], env=sub_env
    )

    rc1 = p1.wait()
    rc2 = p2.wait()

    assert rc1 == 0 and rc2 == 0

    # Both payloads must exist in quarantine under conflict_race_task_shared_race_out*
    q_files = list((tmp_path / "quarantine").glob("conflict_race_task_*"))
    assert len(q_files) == 2

    contents = {f.read_bytes() for f in q_files}
    assert b"Quarantine payload from process A" in contents
    assert b"Quarantine payload from process B" in contents


def test_publish_failure_preserves_staging_file(tmp_path):
    """When atomic publish encounters an unexpected error, the staging file is preserved in place."""
    staging_dir = tmp_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage = staging_dir / "stage_valuable.mph"
    stage.write_bytes(b"precious numerical model")
    out = tmp_path / "final_model.mph"

    with patch(
        "hydro_disturb_bench.comsol_java.bridge._atomic_publish_new_file",
        side_effect=OSError("Disk write error"),
    ):
        with pytest.raises(OSError, match="Disk write error"):
            publish_staging_file(stage, out, "valuable_task", allow_overwrite=False)

    # Staging file MUST still exist in staging, completely preserved!
    assert stage.is_file()
    assert stage.read_bytes() == b"precious numerical model"
    assert not out.exists()


def test_posix_atomic_no_overwrite_fails_closed(tmp_path):
    """Verify _posix_rename_noreplace fails closed and strictly avoids replace() TOCTOU races."""
    stage = tmp_path / "stage.mph"
    stage.write_bytes(b"new stage data")
    dest = tmp_path / "dest.mph"
    dest.write_bytes(b"existing destination data")

    # 1. When destination already exists, fails closed with FileExistsError
    with pytest.raises(FileExistsError, match="already exists"):
        _posix_rename_noreplace(stage, dest, None)

    # Destination data is unchanged; stage data is intact
    assert dest.read_bytes() == b"existing destination data"
    assert stage.read_bytes() == b"new stage data"

    # 2. When destination does not exist, but kernel syscall fails/unsupported, fails closed
    dest_nonexistent = tmp_path / "nonexistent.mph"
    with patch("ctypes.CDLL") as mock_cdll:
        mock_libc = MagicMock()
        mock_libc.syscall.return_value = -1
        mock_cdll.return_value = mock_libc
        with patch("ctypes.get_errno", return_value=38):  # ENOSYS (38)
            with pytest.raises(
                FileExistsError, match="Atomic no-overwrite publish failed or is unsupported"
            ):
                _posix_rename_noreplace(stage, dest_nonexistent, None)

    # Must NOT have created or replaced dest_nonexistent!
    assert not dest_nonexistent.exists()
    assert stage.is_file()
    assert stage.read_bytes() == b"new stage data"


# Native batch startup recovery must never retry an already executing command.
@pytest.mark.parametrize("failure_kind", ["unknown", "native_name_only", "partial_json", "solver_error"])
def test_standalone_fallback_does_not_retry_command_failures(tmp_path, monkeypatch, failure_kind):
    from hydro_disturb_bench.comsol_java import bridge

    _make_fake_comsolbatch(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"mock compiled class")
    native = "ERROR: Failed to start COMSOL\ncom/comsol/nativejni/FlNativeException\n"
    outputs = {
        "unknown": "Unidentified process failure",
        "native_name_only": "FlNativeException while executing model operation",
        "partial_json": native + "<<<JSON_START>>>\n{",
        "solver_error": native + "<<<JSON_START>>>\n" + json.dumps({
            "status": "ERROR", "error_code": "SOLVER_FAILED", "message": "Solver diverged"
        }) + "\n<<<JSON_END>>>",
    }
    proc = subprocess.CompletedProcess([], 1, stdout=outputs[failure_kind], stderr="")
    monkeypatch.setattr(bridge, "get_comsol_bin_dir", lambda root: tmp_path)
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda root: class_file)
    monkeypatch.setattr(bridge.subprocess, "run", MagicMock(return_value=proc))
    fallback = MagicMock(side_effect=AssertionError("A command failure must not be retried"))
    monkeypatch.setattr(bridge, "_run_standalone", fallback)
    # Force the Windows decision branch without constructing platform-specific paths.
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    expected_error = SolverFailedError if failure_kind == "solver_error" else ComsolConnectionError
    with pytest.raises(expected_error):
        bridge._run_bridge(["run-study", "source.mph", "output.mph", "std1"], root=tmp_path)
    fallback.assert_not_called()
    bridge.subprocess.run.assert_called_once()


def test_standalone_fallback_recovers_only_identified_batch_startup(tmp_path, monkeypatch):
    from hydro_disturb_bench.comsol_java import bridge

    _make_fake_comsolbatch(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"mock compiled class")
    startup = subprocess.CompletedProcess([], 0,
        stdout="ERROR: Failed to start COMSOL\ncom/comsol/nativejni/FlNativeException\n", stderr="native detail")
    payload = {"status": "SUCCESS", "operation": "inspect_api", "comsol_version": "6.4", "api_specification": {}}
    recovered = subprocess.CompletedProcess([], 0,
        stdout="<<<JSON_START>>>\n" + json.dumps(payload) + "\n<<<JSON_END>>>", stderr="")
    monkeypatch.setattr(bridge, "get_comsol_bin_dir", lambda root: tmp_path)
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda root: class_file)
    monkeypatch.setattr(bridge.subprocess, "run", MagicMock(return_value=startup))
    fallback = MagicMock(return_value=recovered)
    monkeypatch.setattr(bridge, "_run_standalone", fallback)
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    result = bridge._run_bridge(["inspect-api"], root=tmp_path)
    assert result["status"] == "SUCCESS"
    assert result["launch_mode"] == "standalone_java_api"
    fallback.assert_called_once_with(tmp_path, class_file, ["inspect-api"])
    assert result["batch_startup_failure"]["stdout"] == startup.stdout


def test_standalone_launcher_preserves_unicode_arguments(tmp_path, monkeypatch):
    import base64

    from hydro_disturb_bench.comsol_java import bridge

    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"mock compiled class")
    installation = tmp_path / "COMSOL"
    arguments = ["inspect-probe-and-solver", "D:/研究/📁/已有解.mph", "0.15", "0.12", "D:/新证据/尾流"]
    runner = MagicMock(side_effect=[
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="real command output", stderr=""),
    ])
    monkeypatch.setattr(bridge.subprocess, "run", runner)
    result = bridge._run_standalone(installation, class_file, arguments)
    assert result.stdout == "real command output"
    assert runner.call_count == 2
    launch_args = runner.call_args_list[1].args[0]
    encoded = launch_args[launch_args.index("ComsolStandalone") + 1:]
    decoded = [base64.b64decode(value).decode("utf-8") for value in encoded]
    assert decoded == [str(installation.resolve()), *arguments]
    assert all(value.isascii() for value in encoded)
    assert all(arg.isascii() for arg in launch_args)
    # A fresh temporary class directory must not outlive the single JVM call.
    assert not runner.call_args_list[1].kwargs["cwd"].exists()


@pytest.mark.parametrize(
    "renderer_lines,verified,directory_matches",
    [
        ("COMSOL_GRAPHICS_RENDERER_READBACK=sw\n", True, True),
        ("", False, True),
        ("COMSOL_GRAPHICS_RENDERER_READBACK=ogl\n", False, True),
        ("COMSOL_GRAPHICS_RENDERER_READBACK=sw\nCOMSOL_GRAPHICS_RENDERER_READBACK=sw\n", False, True),
        ("COMSOL_GRAPHICS_RENDERER_READBACK=sw\n", False, False),
    ],
    ids=["actual_software_readback", "missing_readback", "wrong_renderer", "ambiguous_readback", "unbound_preferences_directory"],
)
def test_standalone_image_export_requires_one_software_renderer_readback(tmp_path, monkeypatch, renderer_lines, verified, directory_matches):
    import base64
    from hydro_disturb_bench.comsol_java import bridge

    _make_fake_comsolbatch(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"mock compiled class")
    startup = subprocess.CompletedProcess([], 0,
        stdout="ERROR: Failed to start COMSOL\ncom/comsol/nativejni/FlNativeException\n", stderr="")
    payload = {"status": "SUCCESS", "operation": "export_s1_diagnostics"}
    preference_dir = str(tmp_path / "isolated_preferences")
    directory_line = "COMSOL_GRAPHICS_PREFSDIR_READBACK=" + base64.b64encode(preference_dir.encode("utf-8")).decode("ascii") + "\n"
    exported = subprocess.CompletedProcess([], 0,
        stdout=renderer_lines + directory_line + "<<<JSON_START>>>\n" + json.dumps(payload) + "\n<<<JSON_END>>>", stderr="")
    exported.graphics_preferences_dir = preference_dir if directory_matches else str(tmp_path / "other_preferences")
    exported.graphics_preference_seed_hashes = {"comsol.prefs": "a" * 64, "comsolserver.prefs": "b" * 64}
    monkeypatch.setattr(bridge, "get_comsol_bin_dir", lambda root: tmp_path)
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda root: class_file)
    monkeypatch.setattr(bridge.subprocess, "run", MagicMock(return_value=startup))
    fallback = MagicMock(return_value=exported)
    monkeypatch.setattr(bridge, "_run_standalone", fallback)
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    arguments = ["export-s1-diagnostics", "saved_m1.mph", "new_export"]

    if verified:
        result = bridge._run_bridge(arguments, root=tmp_path)
        assert result["graphics_renderer"] == {
            "source": "ModelUtil.getPreference", "preference": "graphics.rendering.3drend",
            "value": "sw", "scope": "export_process_only",
            "preferences_directory": preference_dir,
            "preferences_directory_source": "pre-init cs.prefsdir system property readback matched to launcher",
            "seed_preferences_sha256": exported.graphics_preference_seed_hashes,
        }
    else:
        with pytest.raises(ComsolConnectionError, match="software-renderer" if directory_matches else "preference directory"):
            bridge._run_bridge(arguments, root=tmp_path)
    fallback.assert_called_once_with(tmp_path, class_file, arguments)


@pytest.mark.parametrize("operation", ["export-s1-diagnostics", "inspect-api"])
def test_standalone_software_graphics_options_are_preinit_and_export_only(tmp_path, monkeypatch, operation):
    import hashlib
    from hydro_disturb_bench.comsol_java import bridge

    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"mock compiled class")
    installation = tmp_path / "COMSOL"
    ini = installation / "bin" / "win64" / "comsolbatch.ini"
    ini.parent.mkdir(parents=True)
    ini.write_text("-Dcs.prefsdir=@user.home/.comsol/v64/\n", encoding="utf-8")
    user_home = tmp_path / "synthetic_user"
    seed_dir = user_home / ".comsol" / "v64"
    seed_dir.mkdir(parents=True)
    seeds = {"comsol.prefs": b"client profile resource paths", "comsolserver.prefs": b"server profile resource paths"}
    for name, contents in seeds.items():
        (seed_dir / name).write_bytes(contents)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    launch = {}

    def process(command, **kwargs):
        if "ComsolStandalone" in command:
            launch["command"] = command
            launch["work"] = Path(kwargs["cwd"])
            preference_args = [arg for arg in command if arg.startswith("-Dcs.prefsdir=")]
            if operation == "export-s1-diagnostics":
                assert len(preference_args) == 1
                preferences = Path(preference_args[0].split("=", 1)[1])
                assert preferences.is_dir()
                assert preferences.parent == launch["work"]
                assert command.index("-Dcs.3drend=sw") < command.index("ComsolStandalone")
                assert command.index(preference_args[0]) < command.index("ComsolStandalone")
                for name, contents in seeds.items():
                    assert (preferences / name).read_bytes() == contents
                    (preferences / name).write_bytes(b"simulated export-process preference autosave")
                launch["preferences"] = preferences
            else:
                assert not preference_args
                assert not any(arg.startswith("-Dcs.3drend=") for arg in command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", process)
    result = bridge._run_standalone(installation, class_file, [operation])

    assert not launch["work"].exists()
    if operation == "export-s1-diagnostics":
        assert result.graphics_preferences_dir == str(launch["preferences"])
        assert result.graphics_preference_seed_hashes == {name: hashlib.sha256(contents).hexdigest() for name, contents in seeds.items()}
        assert not launch["preferences"].exists()
    else:
        assert not hasattr(result, "graphics_preferences_dir")
    assert {name: (seed_dir / name).read_bytes() for name in seeds} == seeds
