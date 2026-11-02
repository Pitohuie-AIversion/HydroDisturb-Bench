"""Unit tests for COMSOL Java API contract.

These offline tests verify error mapping, argument validation, baseline immutability,
and output schema contracts using controlled mocks without requiring a live COMSOL solver.
"""
from __future__ import annotations

import json
from pathlib import Path
import concurrent.futures
from unittest.mock import patch, MagicMock
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
    compute_file_hash,
    find_comsol_root,
    inspect_model,
    map_error,
    run_study,
    update_parameter,
    _run_bridge,
    publish_staging_file,
    list_quarantine_records,
    recover_unclaimed_staging,
)


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
            with pytest.raises(ComsolConnectionError, match="COMSOL installation root could not be located"):
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
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"fake binary")

    err_json = json.dumps({
        "status": "ERROR",
        "error_code": ERR_INVALID_UNIT,
        "message": "Unknown unit: m/s99",
        "details": "Unit parser error trace"
    })
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = f"<<<JSON_START>>>\n{err_json}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(InvalidUnitError, match="Unknown unit"):
                        _run_bridge(["test"])


def test_bridge_node_not_found_propagation(tmp_path):
    """NODE_NOT_FOUND error from Java must raise NodeNotFoundError."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"content")
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"fake binary")

    err_json = json.dumps({
        "status": "ERROR",
        "error_code": ERR_NODE_NOT_FOUND,
        "message": "Parameter non_existent not found",
        "details": "Available: [H, L]"
    })
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = f"<<<JSON_START>>>\n{err_json}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(NodeNotFoundError, match="Parameter non_existent not found"):
                        _run_bridge(["test"])


def test_inspection_schema_contract(tmp_path):
    """Successful inspect_model must produce compliant dictionary schema."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"base data")
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"fake binary")

    inspection_payload = {
        "status": "SUCCESS",
        "operation": "inspect_model",
        "model_path": str(base),
        "model_tag": "Model",
        "comsol_version": "COMSOL Multiphysics 6.4",
        "parameters": {
            "U_mean": {
                "expr": "1[m/s]",
                "value": 1.0,
                "unit": "m/s",
                "description": "Inflow speed"
            }
        },
        "components": [
            {
                "tag": "comp1",
                "label": "Component 1",
                "geometries": [{"tag": "geom1", "sdim": 2, "features": ["r1"]}],
                "meshes": [{"tag": "mesh1", "sdim": 2, "is_complete": True, "num_elem": 100, "num_vertex": 50, "features": ["size"]}],
                "physics": [{"tag": "spf", "label": "Laminar Flow", "type": "LaminarFlow", "features": []}],
                "materials": [{"tag": "mat1", "label": "Water"}]
            }
        ],
        "studies": [
            {
                "tag": "std1",
                "label": "Study 1",
                "features": [{"tag": "time", "label": "Time Dependent", "type": "Transient"}]
            }
        ],
        "solvers": [
            {
                "tag": "sol1",
                "label": "Solution 1",
                "features": ["t1"]
            }
        ]
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = f"<<<JSON_START>>>\n{json.dumps(inspection_payload)}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
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
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"fake binary")

    def side_effect_modify(cmd, *args, **kwargs):
        # Simulate tool corrupting/modifying the source file
        base.write_bytes(b"tampered data")
        staging_file = Path(cmd[6])
        staging_file.write_bytes(b"valid non-empty staging output")
        res = MagicMock()
        res.returncode = 0
        payload = json.dumps({
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
            "output_model": str(out)
        })
        res.stdout = f"<<<JSON_START>>>\n{payload}\n<<<JSON_END>>>"
        return res

    with patch("subprocess.run", side_effect=side_effect_modify):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(ModelValidationError, match="Safety violation: original model file was modified"):
                        update_parameter(base, out, "U_mean", "2.0")


def test_subprocess_nonzero_exit_code_fails_closed(tmp_path):
    """If comsolbatch returns non-zero without valid SUCCESS JSON, ComsolConnectionError must be raised."""
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = "Fatal Java Virtual Machine error"
    mock_proc.stderr = "OutOfMemoryError"

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(ComsolConnectionError, match="exit code 1"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_invalid_json_fails_closed(tmp_path):
    """If ComsolBridge prints invalid/truncated JSON within sentinels, fail closed."""
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "<<<JSON_START>>>\n{truncated json:\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(ComsolConnectionError, match="Failed to decode JSON"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_preexisting_output_file_strictly_preserved_on_failure(tmp_path):
    """If an operation fails, any pre-existing valid file at output_path must be completely preserved!"""
    base = tmp_path / "base.mph"
    base.write_bytes(b"initial baseline data")
    out = tmp_path / "valid_prior_result.mph"
    out.write_bytes(b"crucial valid pre-existing result")
    prior_content = out.read_bytes()
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")

    def side_effect_fail(cmd_args, **kwargs):
        # Even if the subprocess wrote something to staging or crashed
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "<<<JSON_START>>>\n{\"status\": \"ERROR\", \"error_code\": \"SOLVER_FAILED\", \"message\": \"divergence\"}\n<<<JSON_END>>>"
        return mock_proc

    with patch("subprocess.run", side_effect=side_effect_fail):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
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
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")

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
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=base):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
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
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "<<<JSON_START>>>\n[1, 2, 3]\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(ComsolConnectionError, match="expected JSON object, got list"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_schema_missing_fields_fails_closed(tmp_path):
    """If ComsolBridge prints SUCCESS but misses required schema fields, fail closed."""
    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    # Missing api_specification
    mock_proc.stdout = "<<<JSON_START>>>\n{\"status\": \"SUCCESS\", \"operation\": \"inspect_api\"}\n<<<JSON_END>>>"
    mock_proc.stderr = ""

    with patch("subprocess.run", return_value=mock_proc):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
                    with pytest.raises(ComsolConnectionError, match="Schema violation"):
                        _run_bridge(["inspect-api"], root=tmp_path)


def test_preexisting_final_file_blocks_successful_run_when_overwrite_forbidden(tmp_path):
    """When allow_overwrite=False (default), existing target file blocks publish and preserves staging artifact in quarantine."""
    base = tmp_path / "base.mph"
    base.write_bytes(b"baseline model data")
    out = tmp_path / "target_existing.mph"
    out.write_bytes(b"prior valuable result that must not be overwritten")
    prior_content = out.read_bytes()

    fake_exe = tmp_path / "comsolbatch.exe"
    fake_exe.write_bytes(b"")

    def side_effect_success(cmd_args, **kwargs):
        # Position 6 in comsolbatch invocation is the staging file
        staging_file = Path(cmd_args[6])
        staging_file.write_bytes(b"newly computed successful result")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "<<<JSON_START>>>\n{\"status\": \"SUCCESS\", \"operation\": \"update_parameter\", \"param_name\": \"U_mean\", \"new_expr\": \"2.0\", \"readback_value\": 2.0}\n<<<JSON_END>>>"
        mock_proc.stderr = ""
        return mock_proc

    # 1. Default allow_overwrite=False must raise FileExistsError
    with patch("subprocess.run", side_effect=side_effect_success):
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
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
        with patch("hydro_disturb_bench.comsol_java.bridge.ensure_bridge_compiled", return_value=fake_exe):
            with patch("hydro_disturb_bench.comsol_java.bridge.find_comsol_root", return_value=tmp_path):
                with patch("hydro_disturb_bench.comsol_java.bridge.get_comsol_bin_dir", return_value=tmp_path):
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
    expected_bytes = b"Task A completed model bytes" if winner_id == "taskA" else b"Task B completed model bytes"
    assert out.read_bytes() == expected_bytes

    # Loser's staging file was safely moved to quarantine as conflict_*
    loser_id = failures[0][0]
    quarantine_files = list((tmp_path / "quarantine").glob(f"conflict_{loser_id}_*"))
    assert len(quarantine_files) == 1
    loser_bytes = b"Task B completed model bytes" if winner_id == "taskA" else b"Task A completed model bytes"
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
    recovered = recover_unclaimed_staging(tmp_path)
    assert len(recovered) == 1
    assert recovered[0].name == "orphan_stage_abandoned_model.mph"
    assert not orphan_file.exists()

    # List quarantine records
    records = list_quarantine_records(tmp_path)
    assert len(records) == 1
    assert records[0]["name"] == "orphan_stage_abandoned_model.mph"
    assert records[0]["size_bytes"] == len(b"abandoned staging file")
