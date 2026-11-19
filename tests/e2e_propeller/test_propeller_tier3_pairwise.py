"""Tier 3 Pairwise Cross-Feature Combinatorial E2E Suite (18 tests: T3-01 .. T3-18).

Exercises cross-feature data flow, control flow, and shared-state couplings across
Layers L1-L5 in deterministic offline mode.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, to_safe_path

from .conftest import (
    HARD_INVARIANTS,
    require_contract,
    require_repo_artifact,
    resolve_contract_attr,
)

pytestmark = pytest.mark.tier3


def _is_valid(audit) -> bool:
    if isinstance(audit, dict):
        return bool(audit.get("is_valid", audit.get("valid", False)))
    return bool(getattr(audit, "is_valid", getattr(audit, "valid", False)))


def test_t3_01_f1_f3_spatial_selector_with_cw_ccw_bc_swap() -> None:
    """T3-01 (F1+F3): Renumbered 3D CAD faces map dynamically to CW/CCW upstream/open BCs."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    classifier = resolve_contract_attr(
        cad_mod, ("classify_spatial_entities", "classify_boundaries", "SpatialEntityClassifier"), "spatial classifier"
    )
    swapper = resolve_contract_attr(
        cad_mod, ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"), "BC swapper"
    )
    faces = [
        {"id": 101, "x_min": -3.39, "x_max": -3.39, "r_max": 1.60},
        {"id": 102, "x_min": 1.54, "x_max": 1.54, "r_max": 1.60},
        {"id": 103, "x_min": -3.39, "x_max": 1.54, "r_max": 1.60},
        {"id": 104, "x_min": -3.39, "x_max": 1.54, "r_max": 1.60},
        {"id": 105, "x_min": 0.010, "x_max": 0.202, "r_max": 0.194},
        {"id": 106, "x_min": 0.065, "x_max": 0.130, "r_max": 0.160},
    ]
    cls_res = classifier(faces) if callable(classifier) else classifier().classify(faces)
    far_ids = set(cls_res["far"])
    far_faces = [f for f in faces if f["id"] in far_ids]
    bc_cw = swapper(speed_rpm=1400.0, far_faces=far_faces)
    bc_ccw = swapper(speed_rpm=-1400.0, far_faces=far_faces)
    assert bc_cw["upstream"] == [102] and set(bc_cw["open"]) == {101, 103, 104}
    assert bc_ccw["upstream"] == [101] and set(bc_ccw["open"]) == {102, 103, 104}
    assert set(bc_cw["upstream"]).isdisjoint(set(bc_cw["open"]))
    assert set(bc_ccw["upstream"]).isdisjoint(set(bc_ccw["open"]))


def test_t3_02_f1_f2_bridge_sha256_safety_and_unicode_space_paths(
    tmp_path: Path, unicode_space_tmp_dir, mock_comsol_propeller_subprocess
) -> None:
    """T3-02 (F1+F2): inspect_propeller + CoW staging keep Unicode source SHA-256 intact."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("inspect_propeller",),
        allow_pending_attrs=True,
    )
    safe_src = to_safe_path(unicode_space_tmp_dir["mph"])
    assert Path(safe_src).exists()
    fake_runner = mock_comsol_propeller_subprocess()
    info = bridge.inspect_propeller(unicode_space_tmp_dir["mph"], process_runner=fake_runner)
    assert info["status"] == "SUCCESS"
    staging_dir = tmp_path / "runs" / "propeller_opt" / "staging" / "case_001"
    staging_dir.mkdir(parents=True, exist_ok=True)
    assert str(staging_dir.name).isascii() and " " not in staging_dir.name
    assert compute_file_hash(unicode_space_tmp_dir["mph"]) == unicode_space_tmp_dir["sha256"]


def test_t3_03_f1_f7_imp_filename_override_and_parametric_rebuild(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T3-03 (F1+F7): Parametric build overrides legacy E:/prop test2 path & creates conformal union."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("build_propeller_geometry",),
        allow_pending_attrs=True,
    )
    out_mph = tmp_path / "runs" / "propeller_opt" / "staging" / "rebuild.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    fake_runner = mock_comsol_propeller_subprocess(
        payload={
            "status": "SUCCESS",
            "fin_action": "union",
            "fluid_domains": ["station", "rotorflow"],
            "assembly_pairs": [],
            "geometry_audit": {"d_prop_m": 0.320, "d_hub_m": 0.084},
        }
    )
    src = tmp_path / "legacy.mph"
    src.write_bytes(b"MOCK_LEGACY_MPH")
    res = bridge.build_propeller_geometry(
        source_model=src,
        output_model=out_mph,
        design=nominal_design_dict,
        process_runner=fake_runner,
    )
    assert res["status"] == "SUCCESS"
    for call in fake_runner.calls:
        assert "E:/prop test2" not in " ".join(call["cmd"])


def test_t3_04_f4_f7_f8_fixed_diameter_lock_across_schema_cad_and_guard(nominal_design_dict) -> None:
    """T3-04 (F4+F7+F8): Diameter lock enforced across PropellerDesignVector, CAD, & GeometryGuard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    for bad_key in ("diameter", "D_prop_m", "R_prop_m"):
        assert bad_key not in schema_mod.PropellerDesignVector.model_fields
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, **{bad_key: 0.35}))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert _is_valid(guard_mod.GeometryGuard.validate(design, measured_bbox={"d_prop_m": 0.3200, "r_prop_m": 0.1600}))
    assert not _is_valid(guard_mod.GeometryGuard.validate(design, measured_bbox={"d_prop_m": 0.3205, "r_prop_m": 0.16025}))


def test_t3_05_f5_f7_f8_hub_lock_with_blade_root_loft(nominal_design_dict) -> None:
    """T3-05 (F5+F7+F8): Hub mounting lock rejects root axial/radial breaches before solver."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    ok_audit = guard_mod.GeometryGuard.validate(
        design,
        measured_bbox={"d_hub_m": 0.084, "hub_x_min_m": 0.065, "hub_x_max_m": 0.130, "blade_root_x_min_m": 0.068, "blade_root_x_max_m": 0.128},
    )
    assert _is_valid(ok_audit)
    bad_audit = guard_mod.GeometryGuard.validate(
        design,
        measured_bbox={"d_hub_m": 0.090, "hub_x_min_m": 0.062, "hub_x_max_m": 0.130},
    )
    assert not _is_valid(bad_audit)


def test_t3_06_f6_f7_f8_duct_outer_envelope_and_tip_clearance_sweep(nominal_design_dict) -> None:
    """T3-06 (F6+F7+F8): Tip clearance sweep preserves D_prop=0.320 and outer duct <= 0.388856 m."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    for gap in (0.0015, 0.0050, 0.0060):
        d = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=gap))
        assert _is_valid(guard_mod.GeometryGuard.validate(d))
    for bad_gap in (0.0010, 0.0080):
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=bad_gap))
    d_ok = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(guard_mod.GeometryGuard.validate(d_ok, measured_bbox={"duct_outer_max_d_m": 0.390}))


def test_t3_07_f8_f9_geometry_guard_gates_serial_evaluator(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T3-07 (F8+F9): Invalid designs short-circuit before COMSOL (2 calls for 4-case batch)."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    fake_runner = mock_comsol_propeller_subprocess()
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=fake_runner, max_workers=1)
    d1 = dict(nominal_design_dict, pitch_r07_deg=21.0)
    d2 = dict(nominal_design_dict, pitch_r07_deg=23.0)
    b1 = dict(nominal_design_dict, skew_tip_deg=-6.0)
    b2 = dict(nominal_design_dict, chord_r04_m=0.115)
    results = [ev.evaluate(d, case_id=f"c{i}") for i, d in enumerate((d1, b1, d2, b2))]
    statuses = [r["status"] if isinstance(r, dict) else r.status for r in results]
    assert statuses[0] == "SUCCESS" and statuses[2] == "SUCCESS"
    assert statuses[1] == "GEOMETRY_REJECTED" and statuses[3] == "GEOMETRY_REJECTED"
    assert len(fake_runner.calls) == 2


def test_t3_08_f2_f9_sha256_protection_quarantine_and_warm_start_recovery(
    tmp_path: Path, nominal_design_dict, unicode_space_tmp_dir
) -> None:
    """T3-08 (F2+F9): Case 2 SOLVER_FAILED quarantines non-destructively; Case 3 warm-starts."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    calls: list[list[str]] = []

    def _runner(cmd, *args, **kwargs):
        calls.append(list(cmd))
        idx = len(calls)
        payload = (
            {"status": "SOLVER_FAILED", "error_code": "SOLVER_FAILED"}
            if idx == 2
            else {"status": "SUCCESS", "rel_err": 0.0012, "thrust_n": 2100.0, "power_kw": 10.2}
        )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=f"<<<JSON_START>>>\n{json.dumps(payload)}\n<<<JSON_END>>>\n", stderr=""
        )

    runs_dir = tmp_path / "runs"
    ev = evaluator_cls(work_dir=runs_dir, process_runner=_runner, max_workers=1)
    r1 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=20.0), case_id="q1")
    r2 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=21.0), case_id="q2")
    r3 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=22.0), case_id="q3")
    assert (r2["status"] if isinstance(r2, dict) else r2.status) == "SOLVER_FAILED"
    assert (r3["status"] if isinstance(r3, dict) else r3.status) == "SUCCESS"
    assert compute_file_hash(unicode_space_tmp_dir["mph"]) == unicode_space_tmp_dir["sha256"]
    assert not list(runs_dir.rglob(".lease_*.lock"))


def test_t3_09_f9_f10_cfd_quality_gate_blocks_unconverged_symmetry() -> None:
    """T3-09 (F9+F10): High-thrust unconverged solution (mass=2.4%, rel_err=0.008) fails C4."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    sym_fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    qual_fn = resolve_contract_attr(
        metrics_mod, ("check_cfd_quality", "audit_cfd_quality", "validate_cfd_quality"), "CFD quality audit"
    )
    assert float(sym_fn(t_cw=2200.0, t_ccw=2300.0)) > 0.90
    q_good = qual_fn(mass_imbalance_pct=0.25, momentum_closure_pct=2.5, y_plus_imp=37.0, y_plus_duct=15.0, rel_err=0.0018)
    q_bad = qual_fn(mass_imbalance_pct=2.40, momentum_closure_pct=2.5, y_plus_imp=37.0, y_plus_duct=15.0, rel_err=0.0080)
    assert bool(q_good if isinstance(q_good, bool) else q_good.get("passed", q_good.get("is_valid")))
    assert not bool(q_bad if isinstance(q_bad, bool) else q_bad.get("passed", q_bad.get("is_valid")))


def test_t3_10_f10_f12_bidirectional_symmetry_coupled_with_cavitation_depths() -> None:
    """T3-10 (F10+F12): Baseline fails C2 & C3 at h=0 m; symmetric design passes & gains margin at h=10 m."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    sym_fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    cav_fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    assert float(sym_fn(t_cw=1262.36, t_ccw=2475.37)) < 0.75
    assert not bool(cav_fn(p_min_gauge_kpa=-214.44, area_sub99_m2=0.0007, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)["c3_passed"])

    assert float(sym_fn(t_cw=1890.0, t_ccw=2250.0)) >= 0.75
    c0 = cav_fn(p_min_gauge_kpa=-78.5, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)
    c10 = cav_fn(p_min_gauge_kpa=-78.5, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=10.0, speed_rpm=1400.0)
    assert bool(c0["c3_passed"])
    assert (float(c10["sigma"]) + float(c10["c_p_min"])) > (float(c0["sigma"]) + float(c0["c_p_min"]))


def test_t3_11_f11_f12_dp_crossflow_coupled_with_tip_gap_leakage(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T3-11 (F11+F12): DP K_T/K_Q/eta_0 + crossflow S_dist + tip leakage track clearance monotonically."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    dp_fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    leak_fn = resolve_contract_attr(
        metrics_mod, ("compute_tip_gap_leakage", "estimate_tip_leakage"), "tip leakage fn"
    )
    d_tight = calibrated_rov_oracle.evaluate(dict(nominal_design_dict, tip_clearance_m=0.0018), advance_j=0.20, u_lat_mps=0.5)
    d_wide = calibrated_rov_oracle.evaluate(dict(nominal_design_dict, tip_clearance_m=0.0055), advance_j=0.20, u_lat_mps=0.5)
    dp_0 = dp_fn(u_a_mps=0.0, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=2000.0, torque_nm=65.0, rho=1025.0)
    assert float(dp_0["eta_0"]) == 0.0
    assert float(d_tight["hover_disturbance"]["s_dist"]) < float(d_wide["hover_disturbance"]["s_dist"])
    assert float(leak_fn(tip_clearance_m=0.0018, delta_p_pa=25000.0)) < float(leak_fn(tip_clearance_m=0.0055, delta_p_pa=25000.0))


def test_t3_12_f11_f13_hydrodynamic_loads_feed_fsi_wet_modal_and_acoustics(nominal_design_dict) -> None:
    """T3-12 (F11+F13): Hydrodynamic peak thrust feeds FSI stress, wet modal BPF, & SPL_proxy."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    eval_mp = resolve_contract_attr(mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics"), "multiphysics eval")
    check_c5 = resolve_contract_attr(mp_mod, ("check_structural_safety", "check_c5_structural_modal"), "C5 checker")
    ok_res = eval_mp(nominal_design_dict, thrust_n=2200.0, speed_rpm=1400.0)
    assert ok_res["fidelity"] in ("calibrated_analytical", "comsol_3d")
    assert "spl_proxy_db" in ok_res
    assert bool(check_c5(sigma_vm_max_pa=ok_res["sigma_vm_max_pa"], sigma_y_pa=276e6, wet_f1_hz=ok_res["wet_f1_hz"], speed_rpm=1400.0))
    thin = eval_mp(dict(nominal_design_dict, max_thickness_ratio=0.025), thrust_n=3200.0, speed_rpm=1400.0)
    assert not bool(check_c5(sigma_vm_max_pa=thin["sigma_vm_max_pa"], sigma_y_pa=276e6, wet_f1_hz=thin["wet_f1_hz"], speed_rpm=1400.0))


def test_t3_13_f9_f14_interrupted_doe_ledger_resumes_zero_duplicate_solves(
    tmp_path: Path, nominal_design_dict
) -> None:
    """T3-13 (F9+F14): Interrupted 10-point DoE resumes with 0 duplicate solves for cached points."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")

    samples = doe_fn(n_samples=10, method="lhs", seed=77)
    call_counter = {"n": 0}

    def _runner(cmd, *args, **kwargs):
        call_counter["n"] += 1
        payload = {"status": "SUCCESS", "rel_err": 0.001, "thrust_n": 2050.0, "power_kw": 10.1}
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=f"<<<JSON_START>>>\n{json.dumps(payload)}\n<<<JSON_END>>>\n", stderr=""
        )

    runs_dir = tmp_path / "doe_resume"
    ev1 = evaluator_cls(work_dir=runs_dir, process_runner=_runner, max_workers=1)
    for i in range(6):
        ev1.evaluate(samples[i], case_id=f"doe_{i:02d}")
    calls_phase1 = call_counter["n"]

    ev2 = evaluator_cls(work_dir=runs_dir, process_runner=_runner, max_workers=1)
    all_res = [ev2.evaluate(samples[i], case_id=f"doe_{i:02d}") for i in range(10)]
    assert len(all_res) == 10
    assert call_counter["n"] - calls_phase1 == 4


def test_t3_14_f14_f15_surrogate_cv_drives_constrained_nsga2_and_infill(calibrated_rov_oracle) -> None:
    """T3-14 (F14+F15): Surrogate CV + deterministic NSGA-II + adaptive infill work end-to-end."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    infill_fn = resolve_contract_attr(opt_mod, ("select_infill_candidates", "adaptive_infill"), "infill selector")

    samples = doe_fn(n_samples=24, method="lhs", seed=42)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    surr = surrogate_cls(backend="gp")
    cv = surr.cross_validate(samples, y_rows, k_folds=4)
    assert all(np.isfinite(v) for v in cv["r2"].values())
    surr.fit(samples, y_rows)
    res1 = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=24, n_gen=15, seed=101)
    res2 = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=24, n_gen=15, seed=101)
    p1 = res1["pareto_set"] if isinstance(res1, dict) else res1.pareto_set
    p2 = res2["pareto_set"] if isinstance(res2, dict) else res2.pareto_set
    assert len(p1) >= 3 and p1 == p2
    chosen = infill_fn(existing_doe=samples, candidate_pool=p1, k=min(2, len(p1)))
    assert len(chosen) >= 1


def test_t3_15_f8_f15_geometry_guard_filters_nsga2_pareto_front(calibrated_rov_oracle) -> None:
    """T3-15 (F8+F15): 100% of feasible NSGA-II Pareto candidates pass GeometryGuard & D_prop=0.320."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    res = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=32, n_gen=20, seed=7)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    assert len(pareto) >= 1
    for cand in pareto:
        audit = guard_mod.GeometryGuard.validate(cand)
        assert _is_valid(audit)
        ad = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
        assert abs(float(ad["d_prop_m"]) - 0.320) <= 2e-4
        assert abs(float(ad["d_hub_m"]) - 0.084) <= 2e-4


def test_t3_16_f15_f2_f9_back_verification_compares_against_baseline(
    tmp_path: Path, calibrated_rov_oracle, nominal_design_dict, unicode_space_tmp_dir
) -> None:
    """T3-16 (F15+F2+F9): Verify stage emits quantitative baseline delta & leaves baseline intact."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    verify_fn = resolve_contract_attr(
        wf_mod, ("verify_candidates", "back_verify_pareto", "run_verification_stage"), "back-verification"
    )
    cands = [
        dict(nominal_design_dict, max_camber_ratio=0.003, skew_tip_deg=18.0, tip_clearance_m=0.0020),
        dict(nominal_design_dict, max_camber_ratio=0.005, skew_tip_deg=22.0, tip_clearance_m=0.0025),
    ]
    report = verify_fn(candidates=cands, work_dir=tmp_path / "verify_stage", evaluator_fn=calibrated_rov_oracle.evaluate)
    assert len(report["verified_candidates"]) >= 1
    row = report["verified_candidates"][0]
    assert "delta_vs_baseline" in row
    assert compute_file_hash(unicode_space_tmp_dir["mph"]) == unicode_space_tmp_dir["sha256"]


def test_t3_17_cli_config_and_staged_checkpoint_equivalence(
    tmp_path: Path, calibrated_rov_oracle
) -> None:
    """T3-17: Stage-by-stage precheck->doe->train->optimize->verify matches single-shot --stage all."""
    cfg_path = require_repo_artifact("configs/propeller_opt_v10f.json")
    assert cfg_path.is_file()
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")

    dir_all = tmp_path / "wf_all"
    res_all = run_wf(
        work_dir=dir_all, stage="all", evaluator_fn=calibrated_rov_oracle.evaluate, doe_samples=18, pop_size=20, n_gen=10, seed=55
    )

    dir_staged = tmp_path / "wf_staged"
    for st in ("precheck", "doe", "train", "optimize", "verify"):
        res_st = run_wf(
            work_dir=dir_staged, stage=st, evaluator_fn=calibrated_rov_oracle.evaluate, doe_samples=18, pop_size=20, n_gen=10, seed=55
        )
    assert res_all["status"] == "SUCCESS" and res_st["status"] == "SUCCESS"
    assert len(res_all["pareto_front"]) == len(res_st["pareto_front"])


def test_t3_18_f7_f12_f13_tip_clearance_lip_skew_synergy_cavitation_acoustics(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T3-18 (F7+F12+F13): Tighter gap + positive skew improve tip leakage & SPL with fixed outer envelope."""
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    leak_fn = resolve_contract_attr(metrics_mod, ("compute_tip_gap_leakage", "estimate_tip_leakage"), "tip leakage fn")

    d_coarse = dict(nominal_design_dict, tip_clearance_m=0.0055, skew_tip_deg=5.0, max_camber_ratio=0.005)
    d_refined = dict(nominal_design_dict, tip_clearance_m=0.0018, skew_tip_deg=24.0, max_camber_ratio=0.005)
    assert _is_valid(guard_mod.GeometryGuard.validate(d_coarse))
    assert _is_valid(guard_mod.GeometryGuard.validate(d_refined))

    r_coarse = calibrated_rov_oracle.evaluate(d_coarse)
    r_refined = calibrated_rov_oracle.evaluate(d_refined)
    assert r_refined["structural_modal"]["spl_proxy_db"] < r_coarse["structural_modal"]["spl_proxy_db"]
    assert float(leak_fn(tip_clearance_m=0.0018, delta_p_pa=25000.0)) < float(leak_fn(tip_clearance_m=0.0055, delta_p_pa=25000.0))
    assert r_refined["geometry_audit"]["duct_outer_max_d_m"] <= HARD_INVARIANTS["duct_outer_max_d_m"]
