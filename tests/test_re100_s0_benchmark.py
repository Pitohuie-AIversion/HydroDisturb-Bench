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

import concurrent.futures
import math
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from hydro_disturb_bench.comsol_java import (
    ComsolConnectionError,
    ModelValidationError,
    NodeNotFoundError,
    SolverFailedError,
    build_re100_benchmark,
    compute_file_hash,
    export_solution_data,
    find_comsol_root,
    inspect_model,
    run_study,
)
from hydro_disturb_bench.io import parse_comsol_txt

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
    pytest.mark.integration,
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
    assert math.isclose(mu, 1.004e-3, rel_tol=1e-5), (
        f"Viscosity mismatch: expected 1.004e-3, got {mu}"
    )

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
    assert math.isclose(x_c, 0.10, rel_tol=1e-5), (
        f"Cylinder X mismatch: expected 0.10m (10D upstream), got {x_c}"
    )
    assert math.isclose(y_c, 0.12, rel_tol=1e-5), (
        f"Cylinder Y mismatch: expected 0.12m (12D lateral), got {y_c}"
    )

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
    with open(test_data_txt, encoding="utf-8", errors="replace") as f:
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


def _make_valid_staging_content(t: float = 0.0, u_val: float = 0.01) -> str:
    """Generate minimal valid COMSOL Data export text with all required expressions and units."""
    return (
        "% Model: test.mph\n"
        "% Dimension: 2\n"
        "% Nodes: 1\n"
        "% Expressions: 5\n"
        "% Length unit: m\n"
        f"% x y u (m/s) @ t={t} v (m/s) @ t={t} p (Pa) @ t={t} vx-uy (1/s) @ t={t} spf.U (m/s) @ t={t}\n"
        f"0.1 0.2 {u_val} 0.0 0.5 1.0 {u_val}\n"
    )


def test_export_solution_data_refuses_to_overwrite_existing(tmp_path: Path):
    """export_solution_data must refuse to overwrite existing target file by default and quarantine conflict."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "target_existing.txt"
    out_txt.write_text("prior valuable content that must not be overwritten", encoding="utf-8")
    prior_content = out_txt.read_text(encoding="utf-8")

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        staging_file.write_text(_make_valid_staging_content(), encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(FileExistsError, match="overwrite is forbidden"):
            export_solution_data(dummy_model, out_txt, allow_overwrite=False)

    assert out_txt.read_text(encoding="utf-8") == prior_content
    conflicts = list((tmp_path / "quarantine").glob("conflict_*target_existing.txt"))
    assert len(conflicts) == 1, f"Expected 1 conflict file in quarantine, found: {conflicts}"
    assert "u (m/s) @ t=0.0" in conflicts[0].read_text(encoding="utf-8")


def test_export_solution_data_abort_does_not_publish_and_quarantines(tmp_path: Path):
    """When export fails mid-run, incomplete staging file is quarantined as failed and destination is not published."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "aborted_flow.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        staging_file.write_text("% partial incomplete export\n", encoding="utf-8")
        raise SolverFailedError("COMSOL export crashed mid-run")

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(SolverFailedError):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists(), "Target file was published despite export crash!"
    failed_quarantine = list((tmp_path / "quarantine").glob("failed_*aborted_flow.txt"))
    assert len(failed_quarantine) == 1
    assert "partial incomplete export" in failed_quarantine[0].read_text(encoding="utf-8")


def test_export_solution_data_single_variable_staging_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data must refuse to publish when staging file only contains a single variable."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "single_var_flow.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Only 'u' exported, missing v, p, vx-uy, spf.U
        staging_file.write_text(
            "% Length unit: m\n% x y u (m/s) @ t=0.0\n0.1 0.2 0.05\n", encoding="utf-8"
        )
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="missing required expressions"):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists(), "File was published despite missing required variables!"
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*single_var_flow.txt"))
    assert len(quarantined) == 1
    assert "u (m/s) @ t=0.0" in quarantined[0].read_text(encoding="utf-8")


def test_export_solution_data_incomplete_times_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data rejects exports where a time step is missing required expressions."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "incomplete_times.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # t=0.0 complete, but t=0.1 only has u
        header = (
            "% Length unit: m\n"
            "% x y "
            "u (m/s) @ t=0.0 v (m/s) @ t=0.0 p (Pa) @ t=0.0 vx-uy (1/s) @ t=0.0 spf.U (m/s) @ t=0.0 "
            "u (m/s) @ t=0.1\n"
        )
        data = "0.1 0.2 0.01 0.0 0.5 1.0 0.01 0.02\n"
        staging_file.write_text(header + data, encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="missing required expressions"):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists()
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*incomplete_times.txt"))
    assert len(quarantined) == 1


def test_export_solution_data_duplicate_column_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data rejects exports containing duplicate expression-time columns."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "duplicate_cols.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Duplicate u (m/s) @ t=0.0
        header = (
            "% Length unit: m\n"
            "% x y "
            "u (m/s) @ t=0.0 u (m/s) @ t=0.0 v (m/s) @ t=0.0 p (Pa) @ t=0.0 vx-uy (1/s) @ t=0.0 spf.U (m/s) @ t=0.0\n"
        )
        data = "0.1 0.2 0.01 0.01 0.0 0.5 1.0 0.01\n"
        staging_file.write_text(header + data, encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="Duplicate expression-time column"):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists()
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*duplicate_cols.txt"))
    assert len(quarantined) == 1


def test_export_solution_data_wrong_unit_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data rejects exports with incorrect physical units (e.g., bar instead of Pa)."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "wrong_unit.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Wrong unit: p has bar instead of Pa
        header = "% Length unit: m\n% x y u (m/s) @ t=0.0 v (m/s) @ t=0.0 p (bar) @ t=0.0 vx-uy (1/s) @ t=0.0 spf.U (m/s) @ t=0.0\n"
        data = "0.1 0.2 0.01 0.0 0.5 1.0 0.01\n"
        staging_file.write_text(header + data, encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="Unit mismatch for expression 'p'"):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists()
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*wrong_unit.txt"))
    assert len(quarantined) == 1


def test_export_solution_data_missing_length_unit_metadata_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data strictly rejects exports missing coordinate unit metadata (% Length unit: m)."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "missing_unit.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Omit % Length unit: line entirely
        header = (
            "% Model: test.mph\n"
            "% Dimension: 2\n"
            "% x y u (m/s) @ t=0.0 v (m/s) @ t=0.0 p (Pa) @ t=0.0 vx-uy (1/s) @ t=0.0 spf.U (m/s) @ t=0.0\n"
        )
        data = "0.1 0.2 0.01 0.0 0.5 1.0 0.01\n"
        staging_file.write_text(header + data, encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="Missing coordinate unit metadata"):
            export_solution_data(dummy_model, out_txt)

    assert not out_txt.exists()
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*missing_unit.txt"))
    assert len(quarantined) == 1
    assert "Missing coordinate unit metadata" in quarantined[0].name or quarantined[0].is_file()


def test_export_solution_data_mismatched_expected_times_rejected_and_quarantined(tmp_path: Path):
    """export_solution_data strictly rejects exports when time points do not match expected_times."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "mismatched_times.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Only has t=0.0, but caller will expect [0.0, 0.5, 1.0]
        staging_file.write_text(_make_valid_staging_content(t=0.0, u_val=0.01), encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="do not match expected time points"):
            export_solution_data(dummy_model, out_txt, expected_times=[0.0, 0.5, 1.0])

    assert not out_txt.exists()
    quarantined = list((tmp_path / "quarantine").glob("invalid_data_*mismatched_times.txt"))
    assert len(quarantined) == 1


def test_export_solution_data_valid_contract_publishes_successfully(tmp_path: Path):
    """export_solution_data successfully publishes and returns validation summary when contract is satisfied."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "valid_flow.txt"

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        staging_file.write_text(_make_valid_staging_content(t=0.0, u_val=0.015), encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        res = export_solution_data(
            dummy_model, out_txt, allow_overwrite=False, expected_times=[0.0]
        )

    assert out_txt.is_file()
    assert res["status"] == "SUCCESS"
    assert "validation" in res
    assert res["validation"]["num_rows"] == 1
    assert res["validation"]["num_columns"] == 7
    assert res["validation"]["num_times"] == 1
    assert res["validation"]["field_ranges"]["u"] == [0.015, 0.015]


def test_export_solution_data_invalid_export_leaves_preexisting_final_untouched(tmp_path: Path):
    """Pre-existing final file remains strictly unaltered when a subsequent export attempt has invalid data."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    final_txt = tmp_path / "existing_valuable.txt"
    final_txt.write_text("prior golden data: MUST NOT BE TOUCHED", encoding="utf-8")
    prior_content = final_txt.read_text(encoding="utf-8")

    def mock_run_bridge(args, root=None):
        staging_file = Path(args[2])
        # Invalid data: NaN inside field column
        header = "% Length unit: m\n% x y u (m/s) @ t=0.0 v (m/s) @ t=0.0 p (Pa) @ t=0.0 vx-uy (1/s) @ t=0.0 spf.U (m/s) @ t=0.0\n"
        data = "0.1 0.2 NaN 0.0 0.5 1.0 0.01\n"
        staging_file.write_text(header + data, encoding="utf-8")
        return {"status": "SUCCESS", "operation": "export_solution_data"}

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with pytest.raises(ModelValidationError, match="NaN or Inf"):
            export_solution_data(dummy_model, final_txt, allow_overwrite=True)

    # Destination was NOT overwritten even with allow_overwrite=True
    assert final_txt.read_text(encoding="utf-8") == prior_content


def test_export_solution_data_concurrent_race_only_one_succeeds(tmp_path: Path):
    """Two concurrent processes trying to export to the same target: one succeeds, one raises FileExistsError."""
    dummy_model = tmp_path / "model.mph"
    dummy_model.write_bytes(b"dummy model data")
    out_txt = tmp_path / "concurrent_flow.txt"

    def mock_run_bridge(args, root=None):
        if len(args) >= 3 and args[0] == "export-solution-data":
            staging_file = Path(args[2])
            staging_file.write_text(
                _make_valid_staging_content(t=0.0, u_val=time.time()), encoding="utf-8"
            )
            time.sleep(0.05)
            return {"status": "SUCCESS", "operation": "export_solution_data"}
        raise ValueError(f"Unexpected mock_run_bridge invocation: {args}")

    def run_worker(worker_id):
        try:
            export_solution_data(dummy_model, out_txt, allow_overwrite=False)
            return ("SUCCESS", worker_id)
        except FileExistsError:
            return ("CONFLICT", worker_id)

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run_bridge):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(run_worker, 1)
            f2 = executor.submit(run_worker, 2)
            results = [f1.result(), f2.result()]

    statuses = [r[0] for r in results]
    assert "SUCCESS" in statuses
    assert "CONFLICT" in statuses
    assert out_txt.is_file()


def test_inspect_model_reports_live_geometry_distinct_for_different_models():
    """inspect_model must dynamically query live COMSOL geometry and not emit a hardcoded coordinate map."""
    base_insp = inspect_model(BASELINE_MODEL)
    re100_insp = inspect_model(BENCHMARK_MODEL)

    # No hardcoded boundary_geometry_map
    assert "boundary_geometry_map" not in base_insp
    assert "boundary_geometry_map" not in re100_insp

    base_geom = base_insp["components"][0]["geometries"][0]["boundary_geometry"]
    re100_geom = re100_insp["components"][0]["geometries"][0]["boundary_geometry"]

    assert base_geom["available"] is True
    assert re100_geom["available"] is True

    # Real geometry bounding boxes must strictly differ (baseline is 2.2m x 0.41m, Re100 is 0.40m x 0.24m)
    assert base_geom["bounding_box"] != re100_geom["bounding_box"]
    assert math.isclose(base_geom["bounding_box"][1], 2.2, rel_tol=1e-4)
    assert math.isclose(re100_geom["bounding_box"][1], 0.40, rel_tol=1e-4)


def test_re100_boundary_geometry_matches_physical_spec():
    """Verify live boundary geometry evidence in Re=100 model exactly matches physics spec."""
    insp = inspect_model(BENCHMARK_MODEL)
    bg = insp["components"][0]["geometries"][0]["boundary_geometry"]
    assert bg["available"] is True
    assert bg["n_boundaries"] == 8

    # Bounding box: [0, 0.40, 0, 0.24]
    bb = bg["bounding_box"]
    assert math.isclose(bb[0], 0.0, abs_tol=1e-6)
    assert math.isclose(bb[1], 0.40, rel_tol=1e-5)
    assert math.isclose(bb[2], 0.0, abs_tol=1e-6)
    assert math.isclose(bb[3], 0.24, rel_tol=1e-5)

    b_by_id = {b["id"]: b for b in bg["boundaries"]}

    # Inlet: Boundary 1 is at x=0, y in [0, 0.24]
    b1 = b_by_id[1]
    assert math.isclose(b1["x_range"][0], 0.0, abs_tol=1e-6)
    assert math.isclose(b1["x_range"][1], 0.0, abs_tol=1e-6)
    assert math.isclose(b1["length_polyline"], 0.24, rel_tol=1e-4)

    # Lateral symmetry: Boundary 2 (bottom y=0) and 3 (top y=0.24)
    b2 = b_by_id[2]
    assert math.isclose(b2["y_range"][0], 0.0, abs_tol=1e-6)
    assert math.isclose(b2["length_polyline"], 0.40, rel_tol=1e-4)

    b3 = b_by_id[3]
    assert math.isclose(b3["y_range"][0], 0.24, rel_tol=1e-4)
    assert math.isclose(b3["length_polyline"], 0.40, rel_tol=1e-4)

    # Outlet: Boundary 4 is at x=0.40, y in [0, 0.24]
    b4 = b_by_id[4]
    assert math.isclose(b4["x_range"][0], 0.40, rel_tol=1e-4)
    assert math.isclose(b4["length_polyline"], 0.24, rel_tol=1e-4)

    # Cylinder: Boundaries 5, 6, 7, 8 are circular arcs of radius R=0.005 at (0.10, 0.12)
    for cid in [5, 6, 7, 8]:
        bc = b_by_id[cid]
        for p in bc["samples"]:
            dist = math.hypot(p[0] - 0.10, p[1] - 0.12)
            assert math.isclose(dist, 0.005, rel_tol=1e-4), (
                f"Cylinder node off-radius: {dist} != 0.005"
            )


def test_parse_comsol_txt_shuffled_column_order(tmp_path: Path):
    """Verify parse_comsol_txt dynamically matches columns by expression and unit even if shuffled."""
    test_txt = tmp_path / "shuffled.txt"
    header = "% Length unit: m\n% x                       y                        spf.U (m/s) @ t=0.5        p (Pa) @ t=0.5             vx-uy (1/s) @ t=0.5        v (m/s) @ t=0.5            u (m/s) @ t=0.5\n"
    data_row = "0.1000                    0.1200                   0.0150                     0.0450                     12.5000                    0.0050                     0.0141\n"
    test_txt.write_text(header + data_row, encoding="utf-8")

    res = parse_comsol_txt(test_txt, target_time=0.5)
    assert math.isclose(res["U"][0], 0.0150, rel_tol=1e-4)
    assert math.isclose(res["p"][0], 0.0450, rel_tol=1e-4)
    assert math.isclose(res["vorticity"][0], 12.5000, rel_tol=1e-4)
    assert math.isclose(res["v"][0], 0.0050, rel_tol=1e-4)
    assert math.isclose(res["u"][0], 0.0141, rel_tol=1e-4)


def test_parse_comsol_txt_mismatched_columns_rejected(tmp_path: Path):
    """Verify parse_comsol_txt rejects files with missing expressions or mismatched row columns."""
    test_txt = tmp_path / "mismatched.txt"
    # Header missing vx-uy
    header = "% Length unit: m\n% x                       y                        u (m/s) @ t=0.5            v (m/s) @ t=0.5            p (Pa) @ t=0.5             spf.U (m/s) @ t=0.5\n"
    data_row = "0.10 0.12 0.01 0.00 0.05 0.01\n"
    test_txt.write_text(header + data_row, encoding="utf-8")

    with pytest.raises(ValueError, match="missing required expressions"):
        parse_comsol_txt(test_txt, target_time=0.5)


def test_parse_comsol_txt_nan_inf_rejected(tmp_path: Path):
    """Verify parse_comsol_txt rejects data corrupted by NaN or Inf."""
    test_txt = tmp_path / "corrupted_nan.txt"
    header = "% Length unit: m\n% x                       y                        u (m/s) @ t=0.5            v (m/s) @ t=0.5            p (Pa) @ t=0.5             vx-uy (1/s) @ t=0.5        spf.U (m/s) @ t=0.5\n"
    data_row = "0.10 0.12 NaN 0.00 0.05 1.0 0.01\n"
    test_txt.write_text(header + data_row, encoding="utf-8")

    with pytest.raises(ValueError, match="NaN or Inf found"):
        parse_comsol_txt(test_txt, target_time=0.5)


def test_build_re100_case_fails_closed_on_invalid_setup(tmp_path: Path):
    """Verify build_re100_benchmark fails closed with ModelValidationError when input is invalid."""
    invalid_mph = tmp_path / "invalid.mph"
    invalid_mph.write_bytes(b"not a valid comsol mph file")
    out_mph = tmp_path / "out.mph"

    with pytest.raises((ModelValidationError, ComsolConnectionError)):
        build_re100_benchmark(invalid_mph, out_mph)
    assert not out_mph.exists()


def test_parse_comsol_txt_real_verification_file():
    """Verify parse_comsol_txt on the real solved CFD flow data file."""
    data_file = WORKSPACE_DIR / "runs" / "re100_s0_verification" / "flow_solution_data.txt"
    if not data_file.is_file():
        pytest.skip(f"Real verification file not found: {data_file}")

    res = parse_comsol_txt(data_file, target_time=0.5)
    assert len(res["timesteps"]) == 11
    assert math.isclose(res["time"], 0.5)
    assert len(res["x"]) == 3944
    assert res["U"].min() >= 0.0
    assert not math.isnan(res["u"].mean())
    assert not math.isnan(res["p"].mean())
    assert not math.isnan(res["vorticity"].mean())
