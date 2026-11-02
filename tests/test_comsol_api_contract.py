"""Unit tests for COMSOL Java API contract.

These offline tests verify error mapping, argument validation, baseline immutability,
and output schema contracts using controlled mocks without requiring a live COMSOL solver.
"""
from __future__ import annotations

import json
from pathlib import Path
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

    def side_effect_modify(*args, **kwargs):
        # Simulate tool corrupting/modifying the source file
        base.write_bytes(b"tampered data")
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
