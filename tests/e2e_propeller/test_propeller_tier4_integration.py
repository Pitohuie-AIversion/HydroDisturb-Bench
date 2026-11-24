"""Tier 4 Live COMSOL 6.4 Integration Scenarios (2 tests: T4-08, T4-09).

Marked `@pytest.mark.tier4` and `@pytest.mark.integration` so offline CI excludes
them (`-m "not integration and not artifact"`), and `--include-integration` runs
them serially (`max_workers=1`) against CoW staging copies under `runs/propeller_opt/`.
"""
from __future__ import annotations

import pytest

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash

from .conftest import BASELINE_CONSTANTS, HARD_INVARIANTS, require_contract

pytestmark = [pytest.mark.tier4, pytest.mark.integration]


def test_t4_08_live_comsol_baseline_reproduction_and_sha256_immutability(
    live_comsol_env, baseline_constants
) -> None:
    """T4-08: Reproduce CW & CCW baselines via CoW warm-start with exact SHA-256 immutability."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("inspect_propeller", "run_propeller_case"),
    )
    cw_out = live_comsol_env["staging_root"] / "tmp_e2e_t408" / "cw_baseline.mph"
    ccw_out = live_comsol_env["staging_root"] / "tmp_e2e_t408" / "ccw_baseline.mph"
    cw_out.parent.mkdir(parents=True, exist_ok=True)

    cw_res = bridge.run_propeller_case(
        source_model=live_comsol_env["cw_mph"],
        output_model=cw_out,
        speed_rpm=1400.0,
        warm_start=True,
    )
    ccw_res = bridge.run_propeller_case(
        source_model=live_comsol_env["ccw_mph"],
        output_model=ccw_out,
        speed_rpm=-1400.0,
        warm_start=True,
    )

    assert cw_res["status"] == "SUCCESS" and ccw_res["status"] == "SUCCESS"
    assert abs(float(cw_res["thrust_n"]) - baseline_constants["cw_kpi"]["T_N"]) / baseline_constants["cw_kpi"]["T_N"] <= 0.02
    assert abs(float(ccw_res["thrust_n"]) - baseline_constants["ccw_kpi"]["T_N"]) / baseline_constants["ccw_kpi"]["T_N"] <= 0.02
    assert float(cw_res.get("mass_imbalance_pct", 0.0)) < HARD_INVARIANTS["mass_imbalance_max_pct"]
    assert float(ccw_res.get("mass_imbalance_pct", 0.0)) < HARD_INVARIANTS["mass_imbalance_max_pct"]
    assert compute_file_hash(live_comsol_env["cw_mph"]) == BASELINE_CONSTANTS["cw_sha256"]
    assert compute_file_hash(live_comsol_env["ccw_mph"]) == BASELINE_CONSTANTS["ccw_sha256"]


def test_t4_09_live_comsol_parametric_rebuild_and_candidate_back_verification(
    live_comsol_env, nominal_design_dict
) -> None:
    """T4-09: Live COMSOL parametric rebuild, post-build GeometryGuard, & Pilot-to-Fine CW/CCW verify."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("build_propeller_geometry", "run_propeller_case"),
    )
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))

    improved = dict(
        nominal_design_dict,
        max_camber_ratio=0.004,
        skew_tip_deg=18.0,
        tip_clearance_m=0.0025,
    )
    staged_geom_cw = live_comsol_env["staging_root"] / "tmp_e2e_t409" / "cand_geom_cw.mph"
    staged_geom_ccw = live_comsol_env["staging_root"] / "tmp_e2e_t409" / "cand_geom_ccw.mph"
    staged_geom_cw.parent.mkdir(parents=True, exist_ok=True)
    build_res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=staged_geom_cw,
        design=improved,
    )
    assert build_res["status"] == "SUCCESS"
    build_res_ccw = bridge.build_propeller_geometry(
        source_model=live_comsol_env["ccw_mph"],
        output_model=staged_geom_ccw,
        design=improved,
    )
    assert build_res_ccw["status"] == "SUCCESS"
    ga = build_res["geometry_audit"]
    assert abs(float(ga["d_prop_m"]) - HARD_INVARIANTS["D_prop_m"]) <= HARD_INVARIANTS["diameter_tol_m"]
    assert abs(float(ga["d_hub_m"]) - HARD_INVARIANTS["D_hub_m"]) <= HARD_INVARIANTS["diameter_tol_m"]
    assert float(ga["duct_outer_max_d_m"]) <= HARD_INVARIANTS["duct_outer_max_d_m"] + 2e-4
    post_audit = guard_mod.GeometryGuard.validate(improved, measured_bbox=ga)
    assert bool(post_audit.is_valid if hasattr(post_audit, "is_valid") else post_audit["is_valid"])

    cw_solve = bridge.run_propeller_case(
        source_model=staged_geom_cw,
        output_model=live_comsol_env["staging_root"] / "tmp_e2e_t409" / "cand_cw.mph",
        speed_rpm=1400.0,
        warm_start=True,
    )
    ccw_solve = bridge.run_propeller_case(
        source_model=staged_geom_ccw,
        output_model=live_comsol_env["staging_root"] / "tmp_e2e_t409" / "cand_ccw.mph",
        speed_rpm=-1400.0,
        warm_start=True,
    )
    assert cw_solve["status"] == "SUCCESS" and ccw_solve["status"] == "SUCCESS"
    assert float(cw_solve.get("rel_err", 0.0)) <= HARD_INVARIANTS["solver_rel_err_max"]
    assert float(ccw_solve.get("rel_err", 0.0)) <= HARD_INVARIANTS["solver_rel_err_max"]
    assert compute_file_hash(live_comsol_env["cw_mph"]) == BASELINE_CONSTANTS["cw_sha256"]
    assert compute_file_hash(live_comsol_env["ccw_mph"]) == BASELINE_CONSTANTS["ccw_sha256"]
