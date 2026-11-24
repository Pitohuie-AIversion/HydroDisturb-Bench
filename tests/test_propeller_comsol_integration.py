"""Live COMSOL Multiphysics integration tests for 3D ROV propeller bridge & baseline."""

from __future__ import annotations

from pathlib import Path

import pytest

from hydro_disturb_bench.comsol_java import (
    ComsolConnectionError,
    compute_file_hash,
    find_comsol_root,
    inspect_propeller,
)
from hydro_disturb_bench.propeller.baseline import (
    FROZEN_SHA256,
    reproduce_propeller_baselines,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CW_BASELINE = (
    WORKSPACE_DIR
    / "models"
    / "prop test2"
    / "COMSOL_CW_1400rpm"
    / "V10f-StaticFine-1400rpm.mph"
)
CCW_BASELINE = (
    WORKSPACE_DIR
    / "models"
    / "prop test2"
    / "COMSOL_CCW_1400rpm"
    / "V10f-CCWFine-1400rpm.mph"
)
CAD_BASELINE = WORKSPACE_DIR / "models" / "prop test2" / "3_320kgf_v10f.x_t"


def _has_comsol() -> bool:
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_comsol(), reason="Real COMSOL installation not found"),
    pytest.mark.skipif(
        not (CW_BASELINE.is_file() and CCW_BASELINE.is_file() and CAD_BASELINE.is_file()),
        reason="Propeller baseline assets missing under models/prop test2/",
    ),
]


def test_live_inspect_propeller_cw_and_ccw_invariants():
    cw_hash_before = compute_file_hash(CW_BASELINE)
    ccw_hash_before = compute_file_hash(CCW_BASELINE)
    cad_hash_before = compute_file_hash(CAD_BASELINE)
    assert cw_hash_before == FROZEN_SHA256["cw_mph"]
    assert ccw_hash_before == FROZEN_SHA256["ccw_mph"]
    assert cad_hash_before == FROZEN_SHA256["cad_xt"]

    cw_info = inspect_propeller(CW_BASELINE, expected_sha256=cw_hash_before)
    ccw_info = inspect_propeller(CCW_BASELINE, expected_sha256=ccw_hash_before)

    # Parameters & rotation frame
    assert cw_info["parameters"]["speed_rpm"]["value"] == pytest.approx(1400.0 / 60.0, rel=1e-6)
    assert ccw_info["parameters"]["speed_rpm"]["value"] == pytest.approx(-1400.0 / 60.0, rel=1e-6)
    assert cw_info["parameters"]["rho_water"]["value"] == pytest.approx(1025.0, rel=1e-6)
    assert cw_info["rot1"]["revolutionsPerTime"] == "speed_rpm"
    assert cw_info["rot1"]["rotationAxis"] == ["1", "0", "0"]

    # Boundary conditions & coupling operators
    assert cw_info["physics_bcs"]["upstream"]["selection_entities"] == [29]
    assert cw_info["physics_bcs"]["open"]["selection_entities"] == [1, 2, 3]
    assert ccw_info["physics_bcs"]["upstream"]["selection_entities"] == [1]
    assert ccw_info["physics_bcs"]["open"]["selection_entities"] == [2, 3, 29]
    for op in ("intR", "intD", "intFar", "minOp1", "maxOp1"):
        assert op in cw_info["coupling_operators"]
        assert op in ccw_info["coupling_operators"]

    # 3D mounting & geometric invariants
    audit = cw_info["geometry_audit"]
    assert audit["d_prop_m"] == pytest.approx(0.320, abs=2e-4)
    assert audit["duct_outer_max_d_m"] <= 0.388856 + 2e-4
    assert audit["d_hub_m"] == pytest.approx(0.084, abs=2e-4)
    assert audit["moving_domain_bounds"]["r_max_m"] == pytest.approx(0.1625, abs=2e-4)
    assert audit["moving_domain_bounds"]["x_min_m"] == pytest.approx(0.055, abs=2e-4)
    assert audit["moving_domain_bounds"]["x_max_m"] == pytest.approx(0.140, abs=2e-4)

    # Strict SHA-256 immutability
    assert compute_file_hash(CW_BASELINE) == cw_hash_before
    assert compute_file_hash(CCW_BASELINE) == ccw_hash_before
    assert compute_file_hash(CAD_BASELINE) == cad_hash_before


def test_live_reproduce_propeller_baseline_cw_ccw(tmp_path):
    cw_hash_before = compute_file_hash(CW_BASELINE)
    ccw_hash_before = compute_file_hash(CCW_BASELINE)
    cad_hash_before = compute_file_hash(CAD_BASELINE)

    summary = reproduce_propeller_baselines(
        cw_model=CW_BASELINE,
        ccw_model=CCW_BASELINE,
        cad_path=CAD_BASELINE,
        output_dir=tmp_path / "baseline_it",
        solver_mode="warm_fine",
        save_mph=False,
        allow_overwrite=False,
        pin_hashes=True,
    )
    assert summary["status"] == "SUCCESS"
    assert summary["immutability_verified"] is True
    assert summary["verification"]["passed"] is True
    assert summary["cw_baseline"]["kpis"]["thrust_N"] == pytest.approx(1262.356, rel=0.005)
    assert summary["ccw_baseline"]["kpis"]["thrust_N"] == pytest.approx(2475.371, rel=0.005)
    assert summary["verification"]["eta_sym"] == pytest.approx(0.5100, abs=0.005)
    assert summary["cw_baseline"]["solver_stats"]["max_solver_err"] <= 0.0025
    assert summary["ccw_baseline"]["solver_stats"]["max_solver_err"] <= 0.0025

    assert compute_file_hash(CW_BASELINE) == cw_hash_before
    assert compute_file_hash(CCW_BASELINE) == ccw_hash_before
    assert compute_file_hash(CAD_BASELINE) == cad_hash_before
