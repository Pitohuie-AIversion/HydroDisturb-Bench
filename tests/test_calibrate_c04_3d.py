"""Unit tests for 3D C04 calibration normalization, stability checks, and campaign planner."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import calibrate_c04_3d as c04cal


def _write_table(path: Path, header: str, t: np.ndarray, vals: np.ndarray) -> None:
    lines = ["% Test COMSOL Table", f"% Time (s) | {header}"]
    for ti, vi in zip(t, vals):
        lines.append(f"{ti:.12g} {vi:.12g}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_normalize_c04_probe_tables_avg_and_integral_identity(tmp_path: Path):
    """Verify traction-average normalization exactly matches integral-force normalization."""
    t = np.linspace(0.0, 6.0, 101)
    q_inf = 0.5 * c04cal.C04_RHO0 * (c04cal.C04_U0**2)
    area_frontal = c04cal.C04_D_M * c04cal.C04_L_SPAN_M
    area_cyl = math.pi * area_frontal

    cl_true = 0.45 * np.sin(2.0 * math.pi * t / 3.0)
    cd_true = 1.20 + 0.05 * np.cos(4.0 * math.pi * t / 3.0)

    # Raw integral forces (with negative streamwise traction convention)
    fz_int = cl_true * q_inf * area_frontal
    fx_int = -cd_true * q_inf * area_frontal
    tz_avg = fz_int / area_cyl
    tx_avg = fx_int / area_cyl

    _write_table(tmp_path / "probe_lift_avg.txt", "T_tracz", t, tz_avg)
    _write_table(tmp_path / "probe_drag_avg.txt", "T_tracx", t, tx_avg)
    _write_table(tmp_path / "probe_lift_int.txt", "F_z", t, fz_int)
    _write_table(tmp_path / "probe_drag_int.txt", "F_x", t, fx_int)

    res = c04cal.normalize_c04_probe_tables(tmp_path)
    assert res["consistency"]["normalization_source"] == "integral_force"
    assert res["consistency"]["drag_sign_multiplier"] == -1.0
    assert res["consistency"]["lift_avg_vs_int_max_abs_diff"] < 1e-10
    assert res["consistency"]["drag_avg_vs_int_max_abs_diff"] < 1e-10
    assert np.allclose(res["cl"], cl_true, atol=1e-9)
    assert np.allclose(res["cd"], cd_true, atol=1e-9)
    assert (tmp_path / "lift_coeff.txt").is_file()
    assert (tmp_path / "drag_coeff.txt").is_file()
    assert (tmp_path / "force_coefficients.csv").is_file()


def test_normalize_prefers_integral_force_over_faceted_circumference_bias(tmp_path: Path):
    """Verify polygonal mesh chord difference (~0.288% on mesh2) does not bias primary CL/CD."""
    t = np.linspace(0.0, 2.0, 21)
    q_inf = 0.5 * c04cal.C04_RHO0 * (c04cal.C04_U0**2)
    area_frontal = c04cal.C04_D_M * c04cal.C04_L_SPAN_M
    discrete_cyl_area = 2.8193003888754973  # exact measured mesh2 wallbc1 area vs pi*D*L=2.827433

    cl_exact = 0.30 * np.sin(2.0 * math.pi * t)
    cd_exact = np.full_like(t, 1.10)
    fz_int = cl_exact * q_inf * area_frontal
    fx_int = -cd_exact * q_inf * area_frontal
    # COMSOL avg traction divides integral by discrete faceted surface area, not pi*D*L
    tz_avg = fz_int / discrete_cyl_area
    tx_avg = fx_int / discrete_cyl_area

    _write_table(tmp_path / "probe_lift_avg.txt", "T_tracz", t, tz_avg)
    _write_table(tmp_path / "probe_drag_avg.txt", "T_tracx", t, tx_avg)
    _write_table(tmp_path / "probe_lift_int.txt", "F_z", t, fz_int)
    _write_table(tmp_path / "probe_drag_int.txt", "F_x", t, fx_int)

    res = c04cal.normalize_c04_probe_tables(tmp_path)
    assert res["consistency"]["normalization_source"] == "integral_force"
    assert np.allclose(res["cl"], cl_exact, atol=1e-12)
    assert np.allclose(res["cd"], cd_exact, atol=1e-12)
    assert res["consistency"]["drag_avg_vs_int_rel_pct"] == pytest.approx(0.28848, abs=1e-3)


def test_normalize_c04_probe_tables_aligns_unequal_time_grids(tmp_path: Path):
    """Verify CSV consolidation handles duplicate/offset probe timestamps without crashing."""
    t_l = np.array([0.0, 0.06, 0.06, 0.12, 0.18])
    t_d = np.array([0.0, 0.05, 0.12, 0.18])
    tz = np.array([0.0, 1.0, 1.0, 2.0, 1.0])
    tx = np.array([10.0, 10.5, 11.0, 12.0])  # Positive drag sign

    _write_table(tmp_path / "probe_lift_avg.txt", "T_tracz", t_l, tz)
    _write_table(tmp_path / "probe_drag_avg.txt", "T_tracx", t_d, tx)

    res = c04cal.normalize_c04_probe_tables(tmp_path)
    assert len(res["t_lift"]) == 4  # deduplicated
    assert res["consistency"]["drag_sign_multiplier"] == 1.0
    assert res["consistency"]["time_grid_aligned"] is False
    rows = (tmp_path / "force_coefficients.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 5  # header + 4 deduplicated rows


def test_evaluate_c04_series_stable_shedding_and_pair_comparison():
    """Verify exact recovery of St, Cd_mean, Cl_amp, Cl_rms and pairwise PASS/SENSITIVE gates."""
    t = np.linspace(0.0, 60.0, 2001)  # dt = 0.03 s
    period = 3.0  # St = D / (T * U0) = 0.30 / (3.0 * 0.50) = 0.20 exactly
    cl = 0.45 * np.sin(2.0 * math.pi * t / period)
    cd = 1.20 + 0.04 * np.cos(4.0 * math.pi * t / period)

    step_info = c04cal.parse_comsol_bdf_step_log(Path("nonexistent_log.txt"))
    assert step_info["step_count"] is None

    ev_c = c04cal.evaluate_c04_series("C_ref", t, cl, t, cd, min_shedding_t=15.0, min_cycles=5)
    assert ev_c["status"] == "STABLE"
    assert ev_c["is_stable"] is True
    assert ev_c["all_envelope_checks_pass"] is True
    assert ev_c["St_zc_mean"] == pytest.approx(0.20, rel=1e-4)
    assert ev_c["Cd_mean"] == pytest.approx(1.20, rel=1e-4)
    assert ev_c["Cl_amp_mean"] == pytest.approx(0.45, rel=1e-3)
    assert ev_c["Cl_demeaned_rms"] == pytest.approx(0.45 / math.sqrt(2.0), rel=1e-3)

    # Within 1% partner -> PASS_WITHIN_1PCT
    ev_d_pass = c04cal.evaluate_c04_series(
        "D_pass", t, cl * 1.004, t, cd * 0.998, min_shedding_t=15.0, min_cycles=5
    )
    cmp_pass = c04cal.compare_c04_pair(ev_c, ev_d_pass)
    assert cmp_pass["can_compare"] is True
    assert cmp_pass["conclusion"] == "PASS_WITHIN_1PCT"
    assert cmp_pass["exceeded"] == []

    # Outside 1% partner -> SENSITIVE
    ev_d_sens = c04cal.evaluate_c04_series(
        "D_sens", t, cl * 1.03, t, cd, min_shedding_t=15.0, min_cycles=5
    )
    cmp_sens = c04cal.compare_c04_pair(ev_c, ev_d_sens)
    assert cmp_sens["can_compare"] is True
    assert cmp_sens["conclusion"] == "SENSITIVE"
    assert any("Cl_amp_mean" in msg for msg in cmp_sens["exceeded"])


def test_evaluate_c04_series_short_trace_reports_insufficient_cycles():
    t = np.linspace(0.0, 2.0, 50)
    cl = 0.2 * np.sin(2.0 * math.pi * t / 3.0)
    cd = np.full_like(t, 1.15)
    ev = c04cal.evaluate_c04_series("short_trial", t, cl, t, cd)
    assert ev["status"] == "INSUFFICIENT_CYCLES"
    assert ev["is_stable"] is False

    cmp_res = c04cal.compare_c04_pair(ev, ev)
    assert cmp_res["can_compare"] is False
    assert cmp_res["conclusion"] == "UNSTABLE_OR_INCOMPLETE"


def test_parse_comsol_bdf_step_log_and_plan_campaign(tmp_path: Path):
    log_path = tmp_path / "comsol_t1_message.log"
    log_path.write_text(
        "\n".join(
            [
                "Step  Time  Stepsize  Res  Jac",
                "   1  0.005   0.005    2   1",
                " 200  25.00   0.05     2   1",
                " 201  25.05   0.04     2   1",
            ]
        ),
        encoding="utf-8",
    )
    parsed = c04cal.parse_comsol_bdf_step_log(log_path, late_t_min=20.0)
    assert parsed["step_count"] == 3
    assert parsed["late_dt_mean"] == pytest.approx(0.045)
    assert parsed["late_dt_range"] == [0.04, 0.05]

    plan_path = tmp_path / "plan.json"
    rc = c04cal.main(["plan-campaign", "--out", str(plan_path), "--t-end", "60", "--dt-out", "0.06"])
    assert rc == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(plan["conditions"]) == 9
    c04 = next(item for item in plan["conditions"] if item["case_id"] == "C04")
    assert c04["U0"] == 0.50
    assert c04["alpha_deg"] == 90
    assert c04["needs_std1"] is False
    assert plan["calibrated_solver"]["rtol"] == "0.0001"
    assert plan["calibrated_solver"]["maxstepbdf"] == "0.05"
    assert plan["dual_xeon_execution"]["worker_0"]["numafirst"] == 0
    assert plan["dual_xeon_execution"]["worker_1"]["numafirst"] == 1
    assert plan["dual_xeon_execution"]["single_job_mode"]["np"] == 32
    assert plan["dual_xeon_execution"]["single_job_mode"]["numasets"] == 2


def test_verify_solver_readback_fail_closed():
    ok_readback = {
        "rtol": "0.0001",
        "maxstepconstraintbdf": "const",
        "maxstepbdf": 0.05,
        "initialstepbdfactive": False,
        "initialstepbdf": 0.001,
        "consistent": "bweuler",
    }
    c04cal.verify_solver_readback(
        ok_readback,
        rtol="0.0001",
        maxstep_mode="const",
        maxstep_val="0.05",
        init_step="auto",
        consistent="bweuler",
    )

    bad_maxstep = dict(ok_readback, maxstepconstraintbdf="auto")
    with pytest.raises(RuntimeError, match="maxstepconstraintbdf"):
        c04cal.verify_solver_readback(
            bad_maxstep,
            rtol="0.0001",
            maxstep_mode="const",
            maxstep_val="0.05",
            init_step="auto",
            consistent="bweuler",
        )

    bad_rtol = dict(ok_readback, rtol="0.005")
    with pytest.raises(RuntimeError, match="rtol"):
        c04cal.verify_solver_readback(
            bad_rtol,
            rtol="0.0001",
            maxstep_mode="const",
            maxstep_val="0.05",
            init_step="auto",
            consistent="bweuler",
        )


def test_parse_worker_specs_and_save_mph_cases():
    specs2 = c04cal.parse_worker_specs(workers=2, np_per_worker=24, default_mpmode="throughput")
    assert specs2 == [
        {"np": 24, "numasets": 1, "numafirst": 0, "mpmode": "throughput"},
        {"np": 24, "numasets": 1, "numafirst": 1, "mpmode": "throughput"},
    ]
    specs3 = c04cal.parse_worker_specs(workers=3, np_per_worker=16, default_mpmode="throughput")
    assert len(specs3) == 3
    assert specs3[0] == {"np": 32, "numasets": 1, "numafirst": 0, "mpmode": "throughput"}
    assert specs3[1]["numafirst"] == 1 and specs3[2]["numafirst"] == 1

    custom = c04cal.parse_worker_specs(
        workers=3,
        np_per_worker=16,
        default_mpmode="throughput",
        worker_specs_str="32:1:0:turnaround, 16:1:1:throughput, 16:1:1",
    )
    assert custom[0]["mpmode"] == "turnaround"
    assert custom[2]["mpmode"] == "throughput"

    selected = ["B_rtol1e3", "C_rtol1e4_max005", "D_rtol1e4_max0025"]
    assert c04cal.resolve_save_mph_cases(selected, save_mph=True, save_mph_cases="C_rtol1e4_max005") == {
        "C_rtol1e4_max005"
    }
    assert c04cal.resolve_save_mph_cases(selected, save_mph=True, save_mph_cases="none") == set()
    with pytest.raises(ValueError, match="not in selected"):
        c04cal.resolve_save_mph_cases(selected, save_mph=True, save_mph_cases="A_rtol5e3")

    assert c04cal._resolve_schedule_order(selected, "D_rtol1e4_max0025,C_rtol1e4_max005,B_rtol1e3") == [
        "D_rtol1e4_max0025",
        "C_rtol1e4_max005",
        "B_rtol1e3",
    ]
    with pytest.raises(ValueError, match="permutation"):
        c04cal._resolve_schedule_order(selected, "D_rtol1e4_max0025,B_rtol1e3")


def test_cmd_run_preserves_canonical_eval_order_when_dispatched_out_of_order(tmp_path: Path, monkeypatch):
    base_mph = tmp_path / "base.mph"
    base_mph.write_bytes(b"dummy-mph")
    out_dir = tmp_path / "out"
    scratch = tmp_path / "scratch"
    ini_file = tmp_path / "perf.ini"
    ini_file.write_text("batch\n", encoding="utf-8")

    dispatched: list[tuple[str, bool, int, int]] = []
    t = np.linspace(0.0, 60.0, 1001)
    cl = 0.45 * np.sin(2.0 * math.pi * t / 3.0)
    cd = 1.20 + 0.03 * np.cos(4.0 * math.pi * t / 3.0)

    def fake_one_case(**kwargs):
        cid = kwargs["case_id"]
        dispatched.append((cid, kwargs["save_mph"], kwargs["worker_np"], kwargs["worker_numafirst"]))
        ev = c04cal.evaluate_c04_series(cid, t, cl, t, cd)
        return {
            "case_id": cid,
            "save_mph": kwargs["save_mph"],
            "worker": {"np": kwargs["worker_np"], "numafirst": kwargs["worker_numafirst"]},
            "wall_s": 1.0,
            "evaluation": ev,
        }

    monkeypatch.setattr(c04cal, "find_comsol_root", lambda: tmp_path)
    monkeypatch.setattr(c04cal, "get_comsol_bin_dir", lambda root: tmp_path)
    monkeypatch.setattr(c04cal, "ensure_java_compiled", lambda *a, **kw: tmp_path / "Run.class")
    monkeypatch.setattr(c04cal, "_run_one_calibration_case", fake_one_case)

    rc = c04cal.main(
        [
            "run",
            "--base-model",
            str(base_mph),
            "--out-dir",
            str(out_dir),
            "--ini",
            str(ini_file),
            "--scratch",
            str(scratch),
            "--workers",
            "3",
            "--schedule-order",
            "D_rtol1e4_max0025,C_rtol1e4_max005,B_rtol1e3",
            "--save-mph-cases",
            "C_rtol1e4_max005",
        ]
    )
    assert rc == 0
    summary = json.loads((out_dir / "calibration_summary.json").read_text(encoding="utf-8"))
    # Summary evaluation order MUST remain B -> C -> D even though dispatch was D -> C -> B
    assert [c["case_id"] for c in summary["cases"]] == [
        "B_rtol1e3",
        "C_rtol1e4_max005",
        "D_rtol1e4_max0025",
    ]
    assert [p["pair"] for p in summary["pairwise_comparisons"]] == [
        "B_rtol1e3 -> C_rtol1e4_max005",
        "C_rtol1e4_max005 -> D_rtol1e4_max0025",
    ]
    save_map = {cid: sv for cid, sv, _, _ in dispatched}
    assert save_map == {"D_rtol1e4_max0025": False, "C_rtol1e4_max005": True, "B_rtol1e3": False}
