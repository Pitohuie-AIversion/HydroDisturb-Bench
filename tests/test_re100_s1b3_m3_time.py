"""Unit tests for M3 time-step halving study entry point and verification rules.

All tests run offline without active COMSOL server.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
for location in (ROOT_DIR, ROOT_DIR / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from scripts import run_re100_s1b3_m3_time as m3_time


def _mock_step_log_content(rows):
    return "Step Time Stepsize Res Jac Sol Order Tfail NLfail\n" + "\n".join(
        f"{index:6d} {time:.9e} {step:.9e} 2 1 2 2 0 0"
        for index, time, step in rows
    )


def _generate_valid_rows(cap=0.025):
    rows = []
    t = 0.0
    index = 1
    # adaptive initial steps
    for dt in [0.001, 0.005, 0.01]:
        t += dt
        rows.append((index, t, dt))
        index += 1
    # main steps at cap
    while t < 200.0:
        dt = min(cap, 200.0 - t)
        t += dt
        rows.append((index, t, dt))
        index += 1
    return rows


def test_m3_time_check_step_log_accepts_valid_log(tmp_path):
    log_file = tmp_path / "solver.log"
    rows = _generate_valid_rows(0.025)
    log_file.write_text(_mock_step_log_content(rows), encoding="utf-8")

    res = m3_time.check_step_log(log_file, cap=0.025)
    assert res["cap_verified"] is True
    assert res["last_logged_time"] == pytest.approx(200.0)
    assert res["strictly_increasing_time"] is True
    assert res["sequential_unique_step_ids"] is True
    assert res["all_dt_range"][1] <= 0.0250001


@pytest.mark.parametrize("fault", ["exceed_cap", "gap_id", "duplicate_id", "time_reversal", "early_stop"])
def test_m3_time_check_step_log_rejects_faults(tmp_path, fault):
    log_file = tmp_path / "solver.log"
    rows = _generate_valid_rows(0.025)

    if fault == "exceed_cap":
        # Introduce step exceeding cap
        idx, t, _ = rows[50]
        rows[50] = (idx, t, 0.035)
    elif fault == "gap_id":
        del rows[50]
    elif fault == "duplicate_id":
        rows.insert(50, rows[50])
    elif fault == "time_reversal":
        idx, t, dt = rows[50]
        rows[50] = (idx, rows[49][1] - 0.001, dt)
    elif fault == "early_stop":
        rows = rows[:100]

    log_file.write_text(_mock_step_log_content(rows), encoding="utf-8")
    with pytest.raises(RuntimeError):
        m3_time.check_step_log(log_file, cap=0.025)


def _stable_stats(name="M3"):
    return {
        "name": name,
        "is_stable": True,
        "num_cycles": 5,
        "St_zc_mean": 0.163699755,
        "St_peaks_mean": 0.163699755,
        "Cd_mean": 1.365101445,
        "Cl_amp_mean": 0.329649206,
        "Cl_demeaned_rms": 0.233552003,
        "window": [165.6980, 196.0653],
        "step_info": {"step_count": 4087},
        "solve_wall_clock_sec": 728.1,
    }


def test_classify_m3_time_comparison_passes_when_within_one_percent():
    stats_b = _stable_stats("M3 dt=0.05s (baseline)")
    stats_h = _stable_stats("M3 dt=0.025s (halved)")
    # Introduce small perturbations < 1%
    stats_h["St_zc_mean"] *= 1.002
    stats_h["Cd_mean"] *= 1.001
    stats_h["Cl_amp_mean"] *= 1.003
    stats_h["Cl_demeaned_rms"] *= 1.003
    stats_h["step_info"] = {"step_count": 8100}
    stats_h["solve_wall_clock_sec"] = 1450.0

    res = m3_time.classify_m3_time_comparison(stats_b, stats_h)
    assert res["can_compare"] is True
    assert res["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE"
    assert res["acceptance_status"] == "PASS"
    assert res["phase_can_close"] is True
    assert "M3 internal time-step halving study on identical M3 native mesh" in res["scientific_summary"]


@pytest.mark.parametrize("metric", ["St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"])
def test_classify_m3_time_comparison_fails_when_any_metric_exceeds_threshold(metric):
    stats_b = _stable_stats("M3 dt=0.05s (baseline)")
    stats_h = _stable_stats("M3 dt=0.025s (halved)")
    stats_h[metric] *= 1.015  # +1.5% exceeds 1% threshold

    res = m3_time.classify_m3_time_comparison(stats_b, stats_h)
    assert res["conclusion"] == "SENSITIVE"
    assert res["acceptance_status"] == "SENSITIVE"
    assert res["phase_can_close"] is False


def test_classify_m3_time_comparison_requires_at_least_five_cycles():
    stats_b = _stable_stats("M3 dt=0.05s (baseline)")
    stats_h = _stable_stats("M3 dt=0.025s (halved)")
    stats_h["num_cycles"] = 4

    res = m3_time.classify_m3_time_comparison(stats_b, stats_h)
    assert res["can_compare"] is False
    assert res["conclusion"] == "INSUFFICIENT_CYCLES"
    assert res["phase_can_close"] is False


def test_check_settings_verifies_m3_mesh_and_rtol_invariance():
    sol_b = {
        "rtol": "1e-4",
        "maxstepconstraintbdf": "const",
        "maxstepbdf": "0.05",
        "timemethod": "bdf",
        "tstepsbdf": "intermediate",
    }
    sol_h = {
        "rtol": "1e-4",
        "maxstepconstraintbdf": "const",
        "maxstepbdf": "0.025",
        "timemethod": "bdf",
        "tstepsbdf": "intermediate",
    }
    mesh_info = {"num_elem": 34752, "num_vertex": 17910, "sdim": 2}
    phys_info = {"shape_order": "1"}

    before = {"solver_details": sol_b, "mesh_details": mesh_info, "physics_details": phys_info}
    after = {"solver_details": sol_h, "mesh_details": mesh_info, "physics_details": phys_info}
    tree = {"parameters": {"U_inf": {"value": "0.01"}}, "components": ["comp1"]}

    res = m3_time.check_settings(before, after, tree, tree)
    assert res["status"] == "PASS"
    assert res["mesh_invariance_verified"] is True
