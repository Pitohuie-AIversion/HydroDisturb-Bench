"""Real COMSOL Multiphysics Integration Tests.

These tests execute against the locally installed COMSOL Multiphysics 6.4
environment and the real benchmark model (case/cylinder_flow.mph).

They are marked with @pytest.mark.integration so they can be isolated
from offline unit and contract tests:
    python -m pytest -q -m integration tests/test_comsol_integration.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    ComsolConnectionError,
    compute_file_hash,
    find_comsol_root,
    inspect_api,
    inspect_model,
    run_study,
    update_parameter,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL_PATH = WORKSPACE_DIR / "case" / "cylinder_flow.mph"


def check_comsol_environment():
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not check_comsol_environment(),
        reason="Real COMSOL installation not found on this machine",
    ),
    pytest.mark.skipif(
        not BASELINE_MODEL_PATH.is_file(),
        reason=f"Baseline model not found at {BASELINE_MODEL_PATH}",
    ),
]


def test_real_comsol_inspect_api():
    """Verify inspection of official COMSOL installation and Java API capabilities."""
    res = inspect_api()
    assert res.get("status") == "SUCCESS"
    assert "comsol_version" in res
    assert "6.4" in res["comsol_version"]

    spec = res.get("api_specification", {})
    assert spec.get("entry_point") == "com.comsol.model.util.ModelUtil"
    methods = spec.get("supported_methods", [])
    assert len(methods) >= 8

    # Verify key methods are present in the specification
    method_names = [m["method"] for m in methods]
    assert any("ModelUtil.loadCopy" in m for m in method_names)
    assert any("model.param().set" in m for m in method_names)
    assert any("model.study" in m for m in method_names)


def test_real_comsol_inspect_model():
    """Verify read-only inspection of real cylinder_flow.mph and file immutability."""
    hash_initial = compute_file_hash(BASELINE_MODEL_PATH)

    res = inspect_model(BASELINE_MODEL_PATH)
    assert res.get("status") == "SUCCESS"
    assert res.get("model_tag") == "Model"

    # Check components and nodes
    components = res.get("components", [])
    assert len(components) >= 1
    comp = components[0]
    assert comp.get("tag") == "comp1"

    # Geometries and dimensions
    geometries = comp.get("geometries", [])
    assert len(geometries) >= 1
    assert geometries[0].get("tag") == "geom1"
    assert geometries[0].get("sdim") == 2

    # Meshes
    meshes = comp.get("meshes", [])
    assert len(meshes) >= 1
    assert meshes[0].get("tag") == "mesh1"

    # Physics
    physics = comp.get("physics", [])
    assert len(physics) >= 1
    physics_tags = [p["tag"] for p in physics]
    assert "spf" in physics_tags

    # Studies & Solvers
    studies = res.get("studies", [])
    assert len(studies) >= 1
    assert studies[0].get("tag") == "std1"

    solvers = res.get("solvers", [])
    assert len(solvers) >= 1
    assert solvers[0].get("tag") == "sol1"

    # Parameters
    params = res.get("parameters", {})
    assert "U_mean" in params
    assert params["U_mean"]["expr"] == "1[m/s]"
    assert params["U_mean"]["value"] == 1.0
    assert params["U_mean"]["unit"] == "m/s"
    assert "H" in params
    assert "L" in params

    # Immutability verification
    hash_final = compute_file_hash(BASELINE_MODEL_PATH)
    assert hash_initial == hash_final, "Baseline model hash altered during inspection!"


def test_real_comsol_update_parameter_and_readback(tmp_path: Path):
    """Verify modifying a parameter on a working copy and reading back the value."""
    hash_initial = compute_file_hash(BASELINE_MODEL_PATH)
    target_copy = tmp_path / "cylinder_copy_param.mph"

    res = update_parameter(
        model_path=BASELINE_MODEL_PATH,
        output_path=target_copy,
        param_name="U_mean",
        param_expr="1.15[m/s]",
        descr="Test modified inlet velocity",
    )

    assert res.get("status") == "SUCCESS"
    assert res.get("param_name") == "U_mean"
    assert res.get("old_expr") == "1[m/s]"
    assert res.get("new_expr") == "1.15[m/s]"
    assert res.get("readback_value") == 1.15
    assert res.get("readback_unit") == "m/s"
    assert target_copy.is_file()
    assert target_copy.stat().st_size > 0

    # Verify baseline model unchanged
    hash_final = compute_file_hash(BASELINE_MODEL_PATH)
    assert hash_initial == hash_final, "Baseline model hash altered during update_parameter!"

    # Deep verification: inspect the new file and check U_mean
    copy_inspection = inspect_model(target_copy)
    assert copy_inspection["parameters"]["U_mean"]["expr"] == "1.15[m/s]"
    assert copy_inspection["parameters"]["U_mean"]["value"] == 1.15


def test_real_comsol_run_study_and_solution_check(tmp_path: Path):
    """Verify solving a study on a working copy generates real solution nodes and logs."""
    hash_initial = compute_file_hash(BASELINE_MODEL_PATH)
    target_solved = tmp_path / "cylinder_test_solved.mph"

    # Solve a short time range to verify transient solver
    res = run_study(
        model_path=BASELINE_MODEL_PATH,
        output_path=target_solved,
        study_tag="std1",
        tlist_override="range(0,0.05,0.1)",
    )

    assert res.get("status") == "SUCCESS"
    assert res.get("study_tag") == "std1"
    assert res.get("elapsed_ms", 0) > 0
    assert target_solved.is_file()
    # Solved model should be significantly larger due to solution dataset
    assert target_solved.stat().st_size > BASELINE_MODEL_PATH.stat().st_size

    # Verify log output was captured
    log_text = res.get("log", "")
    assert len(log_text) > 0

    # Verify numerical solution validity (degrees of freedom, time steps, no NaN/Inf)
    stats = res.get("solution_stats", {})
    assert stats.get("num_dofs", 0) > 0
    assert stats.get("num_timesteps", 0) >= 3
    assert stats.get("has_nan_or_inf") is False
    assert stats.get("has_physical_field") is True

    # Verify baseline model unchanged
    hash_final = compute_file_hash(BASELINE_MODEL_PATH)
    assert hash_initial == hash_final, "Baseline model hash altered during run_study!"
