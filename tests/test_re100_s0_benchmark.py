"""Stage S0 Targeted Tests: Re=100 2D Low-Blockage Cylinder Flow Benchmark.

Validates:
1. Physical parameters & Re calculation consistency (20°C freshwater, D=0.01m, Re=100, U_inf=0.010058m/s).
2. Domain dimensions & low-blockage requirement (40D x 24D, blockage ~4.17% < 5%).
3. Geometry & boundary selection integrity (inlet 1, lateral symmetry 2 & 3, outlet 4, cylinder no-slip 5,6,7,8).
4. Mesh quality & boundary layer inflation on cylinder.
5. Baseline model immutability (case/cylinder_flow.mph hash preserved).
6. Model copy isolation (models/cylinder_2d/re100_benchmark.mph independent).
7. Refusal to overwrite baseline template.
8. Real transient short solve readability (DOF count, timesteps, finite fields, no NaN/Inf).
9. Failure safety (solver error does not publish corrupted files).
"""
from __future__ import annotations

import math
from pathlib import Path
import pytest

from hydro_disturb_bench.comsol_java import (
    ComsolConnectionError,
    ModelValidationError,
    NodeNotFoundError,
    build_re100_benchmark,
    compute_file_hash,
    export_solution_data,
    find_comsol_root,
    inspect_model,
    run_study,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL = WORKSPACE_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = WORKSPACE_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
BASELINE_SHA256 = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"


def comsol_available() -> bool:
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not comsol_available(),
        reason="Real COMSOL installation not found on this machine",
    ),
    pytest.mark.skipif(
        not BASELINE_MODEL.is_file(),
        reason=f"Baseline model not found at {BASELINE_MODEL}",
    ),
]


def test_re100_parameter_definitions_and_re_consistency():
    """Verify physical properties, formulas, and Re calculation consistency."""
    # Ensure benchmark model exists
    if not BENCHMARK_MODEL.is_file():
        build_re100_benchmark(BASELINE_MODEL, BENCHMARK_MODEL, allow_overwrite=True)

    insp = inspect_model(BENCHMARK_MODEL)
    params = insp.get("parameters", {})

    # 1. Fluid properties: 20 degC freshwater
    rho = params["rho_f"]["value"]
    mu = params["mu_f"]["value"]
    assert math.isclose(rho, 998.2, rel_tol=1e-5), f"Density mismatch: expected 998.2, got {rho}"
    assert math.isclose(mu, 1.004e-3, rel_tol=1e-5), f"Viscosity mismatch: expected 1.004e-3, got {mu}"

    # 2. Geometry dimensions
    D = params["D"]["value"]
    R = params["R"]["value"]
    L = params["L"]["value"]
    H = params["H"]["value"]
    x_c = params["x_c"]["value"]
    y_c = params["y_c"]["value"]

    assert math.isclose(D, 0.01, rel_tol=1e-5), f"Diameter mismatch: expected 0.01m, got {D}"
    assert math.isclose(R, 0.005, rel_tol=1e-5), f"Radius mismatch: expected 0.005m, got {R}"
    assert math.isclose(L, 0.40, rel_tol=1e-5), f"Length mismatch: expected 0.40m (40D), got {L}"
    assert math.isclose(H, 0.24, rel_tol=1e-5), f"Height mismatch: expected 0.24m (24D), got {H}"
    assert math.isclose(x_c, 0.10, rel_tol=1e-5), f"Cylinder X mismatch: expected 0.10m (10D upstream), got {x_c}"
    assert math.isclose(y_c, 0.12, rel_tol=1e-5), f"Cylinder Y mismatch: expected 0.12m (12D lateral), got {y_c}"

    # 3. Blockage ratio strictly below 5%
    blockage = D / H
    assert blockage < 0.05, f"Blockage ratio too high: {blockage:.4f} >= 5%"
    assert math.isclose(blockage, 1.0 / 24.0, rel_tol=1e-4)

    # 4. Target Reynolds number and Inflow Velocity
    Re = params["Re"]["value"]
    U_inf = params["U_inf"]["value"]
    assert math.isclose(Re, 100.0, rel_tol=1e-5)

    expected_U_inf = Re * mu / (rho * D)
    assert math.isclose(U_inf, expected_U_inf, rel_tol=1e-5), (
        f"Inflow velocity mismatch: formula gives {expected_U_inf:.8f}, model has {U_inf:.8f}"
    )


def test_re100_geometry_and_boundary_selections_integrity():
    """Verify geometry topology and complete boundary selection mapping."""
    insp = inspect_model(BENCHMARK_MODEL)
    comps = insp.get("components", [])
    assert len(comps) >= 1
    comp = comps[0]

    # Geometries
    geoms = comp.get("geometries", [])
    assert len(geoms) >= 1
    assert geoms[0]["sdim"] == 2
    assert "dif1" in geoms[0]["features"]

    # Physics boundaries in spf
    physics = comp.get("physics", [])
    spf = next((p for p in physics if p["tag"] == "spf"), None)
    assert spf is not None, "spf physics interface missing!"

    feats = {f["tag"]: f for f in spf.get("features", [])}
    assert "inl1" in feats, "Inlet inl1 missing"
    assert "out1" in feats, "Outlet out1 missing"
    assert "wallbc1" in feats, "Wall wallbc1 missing"
    assert "sym1" in feats, "Symmetry sym1 missing"

    # Inlet: Boundary 1
    assert feats["inl1"]["selection_entities"] == [1]

    # Lateral Symmetry: Boundaries 2 and 3
    assert sorted(feats["sym1"]["selection_entities"]) == [2, 3]

    # Outlet: Boundary 4
    assert feats["out1"]["selection_entities"] == [4]

    # Cylinder No-Slip: Boundaries 5, 6, 7, 8
    assert sorted(feats["wallbc1"]["selection_entities"]) == [5, 6, 7, 8]


def test_re100_baseline_immutability_and_model_isolation():
    """Verify baseline model SHA-256 hash is strictly preserved and models are separate."""
    hash_current = compute_file_hash(BASELINE_MODEL)
    assert hash_current == BASELINE_SHA256, (
        f"Safety violation: baseline model hash changed! Expected {BASELINE_SHA256}, got {hash_current}"
    )

    # Benchmark model must exist and have a distinct hash
    assert BENCHMARK_MODEL.is_file()
    bench_hash = compute_file_hash(BENCHMARK_MODEL)
    assert bench_hash != BASELINE_SHA256, "Benchmark model has same hash as baseline template!"


def test_re100_refuses_to_overwrite_baseline():
    """Verify safety lock refusing to overwrite source baseline model."""
    with pytest.raises(ModelValidationError, match="Refusing to overwrite baseline model"):
        build_re100_benchmark(BASELINE_MODEL, BASELINE_MODEL, allow_overwrite=True)


def test_re100_mesh_properties():
    """Verify mesh completeness and boundary layer inflation on cylinder."""
    insp = inspect_model(BENCHMARK_MODEL)
    mesh = insp["components"][0]["meshes"][0]
    assert mesh["is_complete"] is True
    assert mesh["num_elem"] >= 7000
    assert mesh["num_vertex"] >= 3500
    assert "bl1" in mesh["features"]
    assert "size1" in mesh["features"]


@pytest.mark.integration
def test_re100_short_solve_and_data_export(tmp_path: Path):
    """Verify short transient solve generates valid non-NaN solution and exports readable fields."""
    test_solved_mph = tmp_path / "re100_short_solved.mph"
    test_data_txt = tmp_path / "flow_data.txt"

    # 1. Run short transient solve (3 output steps: 0.0, 0.05, 0.10)
    res = run_study(
        model_path=BENCHMARK_MODEL,
        output_path=test_solved_mph,
        study_tag="std1",
        tlist_override="range(0, 0.05, 0.10)",
    )
    assert res.get("status") == "SUCCESS"
    assert test_solved_mph.is_file()

    stats = res.get("solution_stats", {})
    assert stats.get("num_dofs", 0) > 10000
    assert stats.get("num_timesteps", 0) >= 3
    assert stats.get("has_nan_or_inf") is False
    assert stats.get("has_physical_field") is True

    # 2. Export solution data to text format
    exp_res = export_solution_data(
        model_path=test_solved_mph,
        output_txt_path=test_data_txt,
    )
    assert exp_res.get("status") == "SUCCESS"
    assert test_data_txt.is_file()
    assert test_data_txt.stat().st_size > 1000

    # 3. Read first rows and verify non-empty fields
    with open(test_data_txt, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(4096)
    assert "% x" in content
    assert "spf.U" in content
    assert "vx-uy" in content
    assert "NaN" not in content
    assert "Infinity" not in content


def test_re100_abnormal_study_failure_does_not_publish(tmp_path: Path):
    """Verify abnormal study tag fails safely without publishing corrupt results."""
    bad_output = tmp_path / "should_not_exist.mph"
    with pytest.raises(NodeNotFoundError):
        run_study(
            model_path=BENCHMARK_MODEL,
            output_path=bad_output,
            study_tag="nonexistent_study_tag",
        )
    assert not bad_output.exists(), "Corrupted output was published after failed solve!"
