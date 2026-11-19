"""Tier 4 Offline Real-World ROV Application Scenarios (7 tests: T4-01 .. T4-07).

Each scenario exercises a full end-to-end ROV optimization workflow using the
baseline-calibrated deterministic physics oracle (`CalibratedROVOracle`) and
real L2-L4 Python modules (schema, GeometryGuard, metrics, multiphysics,
surrogate, constrained NSGA-II, workflow).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import BASELINE_CONSTANTS, HARD_INVARIANTS, require_contract, resolve_contract_attr

pytestmark = pytest.mark.tier4


def test_t4_01_rov_dp_station_keeping_j_sweep(calibrated_rov_oracle, nominal_design_dict) -> None:
    """T4-01: ROV DP station-keeping (J in [0, 0.35]) dominates baseline with D_prop=0.320 m."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")

    def _dp_eval(design, **kwargs):
        return calibrated_rov_oracle.evaluate(design, advance_j=0.20)

    res = opt_fn(evaluator=_dp_eval, pop_size=28, n_gen=20, seed=10)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    assert len(pareto) >= 1
    best_tp = max(_dp_eval(c)["t_over_p_mean"] for c in pareto)
    assert best_tp > BASELINE_CONSTANTS["T_over_P_mean"]
    for cand in pareto:
        audit = guard_mod.GeometryGuard.validate(cand)
        ad = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
        assert abs(float(ad["d_prop_m"]) - 0.320) <= 2e-4
        assert abs(float(ad["d_hub_m"]) - 0.084) <= 2e-4
        for j_val in (0.0, 0.10, 0.20, 0.35):
            dp = calibrated_rov_oracle.evaluate(cand, advance_j=j_val)["dp_maneuver"]
            assert dp["K_T"] > 0.0 and dp["K_Q"] > 0.0 and dp["eta_0"] >= 0.0


def test_t4_02_zero_speed_bollard_pull_and_bidirectional_symmetry(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T4-02: Bollard pull (J=0) achieves eta_sym >= 0.75 (vs 0.510) & improved T/P."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")

    res = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=32, n_gen=25, seed=20)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    evals = [calibrated_rov_oracle.evaluate(c, advance_j=0.0) for c in pareto]
    feasible = [
        e for e in evals if e["eta_sym"] >= HARD_INVARIANTS["eta_sym_floor"] and e["t_mean_n"] >= BASELINE_CONSTANTS["T_mean_N"]
    ]
    assert len(feasible) >= 1
    assert max(e["t_over_p_mean"] for e in feasible) > BASELINE_CONSTANTS["T_over_P_mean"]


def test_t4_03_low_speed_maneuvering_and_hover_disturbance_rejection(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T4-03: Crossflow (U_lat=0.5 m/s) optimization reduces S_dist strictly below baseline."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    baseline_s_dist = calibrated_rov_oracle.evaluate(
        nominal_design_dict, u_lat_mps=0.5, baseline_anchor=True
    )["hover_disturbance"]["s_dist"]

    def _crossflow_eval(design, **kwargs):
        return calibrated_rov_oracle.evaluate(design, u_lat_mps=0.5)

    res = opt_fn(evaluator=_crossflow_eval, pop_size=28, n_gen=20, seed=30)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    s_vals = [_crossflow_eval(c)["hover_disturbance"]["s_dist"] for c in pareto]
    assert min(s_vals) < baseline_s_dist
    assert all(s >= 0.0 for s in s_vals)


def test_t4_04_cavitation_free_reverse_thrust_and_tip_gap_suppression(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T4-04: Eliminates CW baseline -214.44 kPa suction peak (p_min >= -99 kPa, 0% area)."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    res = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=32, n_gen=25, seed=40)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    cav_clean = [
        c
        for c in pareto
        if calibrated_rov_oracle.evaluate(c, depth_m=0.0)["cavitation"]["worst_p_min_kpa"]
        >= HARD_INVARIANTS["p_min_cav_kpa"]
    ]
    assert len(cav_clean) >= 1
    ev_5m = calibrated_rov_oracle.evaluate(cav_clean[0], depth_m=5.0)
    assert ev_5m["cavitation"]["margin"] > 0.0
    assert ev_5m["geometry_audit"]["duct_outer_max_d_m"] <= HARD_INVARIANTS["duct_outer_max_d_m"]


def test_t4_05_full_closed_loop_pipeline_with_checkpoint_resume(
    tmp_path: Path, calibrated_rov_oracle
) -> None:
    """T4-05: Full precheck->doe(50% crash)->resume->train->optimize->verify with 0 violations."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))

    work_dir = tmp_path / "runs" / "propeller_opt" / "full_loop"
    eval_calls = {"n": 0}

    def _counting_eval(design, **kwargs):
        eval_calls["n"] += 1
        return calibrated_rov_oracle.evaluate(design)

    run_wf(work_dir=work_dir, stage="precheck", evaluator_fn=_counting_eval, doe_samples=20, seed=50)

    # Simulate partial crash mid-DoE after 10 samples
    crash_calls = {"n": 0}

    def _crash_at_11(design, **kwargs):
        crash_calls["n"] += 1
        if crash_calls["n"] > 10:
            raise RuntimeError("Simulated mid-DoE power interruption")
        return _counting_eval(design)

    with pytest.raises(RuntimeError):
        run_wf(work_dir=work_dir, stage="doe", evaluator_fn=_crash_at_11, doe_samples=20, seed=50)

    calls_before_resume = eval_calls["n"]
    assert calls_before_resume == 10

    # Resume remaining DoE -> train -> optimize -> verify
    for stage in ("doe", "train", "optimize", "verify"):
        out = run_wf(
            work_dir=work_dir,
            stage=stage,
            evaluator_fn=_counting_eval,
            doe_samples=20,
            pop_size=24,
            n_gen=15,
            seed=50,
        )
    assert out["status"] == "SUCCESS"
    # Remaining 10 DoE + verified top candidates, never re-running first 10 cached DoE points
    assert eval_calls["n"] >= 20
    for cand in out.get("pareto_front", []):
        audit = guard_mod.GeometryGuard.validate(cand)
        assert bool(audit.is_valid if hasattr(audit, "is_valid") else audit["is_valid"])


def test_t4_06_multiphysics_safety_envelope_fsi_wet_modal_acoustics(
    calibrated_rov_oracle,
) -> None:
    """T4-06: Pareto candidates satisfy C5 stress (SF>=2), BPF margin>=15%, positive skew, fidelity tag."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    res = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=32, n_gen=20, seed=60)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    safe = []
    for cand in pareto:
        ev = calibrated_rov_oracle.evaluate(cand)
        sm = ev["structural_modal"]
        assert sm["fidelity"] in ("calibrated_analytical", "comsol_3d")
        if (
            sm["fatigue_sf"] >= HARD_INVARIANTS["stress_safety_factor_min"]
            and sm["min_bpf_sep"] >= HARD_INVARIANTS["modal_bpf_sep_min"]
            and float(cand["skew_tip_deg"]) > 0.0
        ):
            safe.append(ev)
    assert len(safe) >= 1


def test_t4_07_subsea_inspection_tradeoff_unicode_space_portability(
    tmp_path: Path, calibrated_rov_oracle
) -> None:
    """T4-07: Depth h=20 m optimization inside Unicode/space path yields >=5 Pareto solutions."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")
    unicode_dir = tmp_path / "📁 水下机器人 prop test2" / "mission_20m"

    def _depth20_eval(design, **kwargs):
        return calibrated_rov_oracle.evaluate(design, depth_m=20.0, u_lat_mps=0.3)

    report = run_wf(
        work_dir=unicode_dir,
        stage="all",
        evaluator_fn=_depth20_eval,
        doe_samples=24,
        pop_size=28,
        n_gen=20,
        seed=70,
    )
    assert report["status"] == "SUCCESS"
    assert len(report.get("pareto_front", [])) >= 5
    for cand in report["pareto_front"]:
        assert "diameter" not in cand
