"""test_stability_onset.py - Tests for C_L stability onset determination."""

from pathlib import Path

import numpy as np
import pytest

from scripts.analyze_cl_stability_onset import (
    compute_sliding_stationarity,
    extract_cycles,
    find_stability_onset,
)

pytestmark = pytest.mark.artifact


@pytest.fixture
def sample_timeseries():
    """Load verified M3 baseline timeseries."""
    cl_path = Path("runs/re100_s1b2_m3_20261105_verified/lift_coeff.txt")
    cd_path = Path("runs/re100_s1b2_m3_20261105_verified/drag_coeff.txt")
    assert cl_path.is_file(), f"Missing {cl_path}"
    assert cd_path.is_file(), f"Missing {cd_path}"

    data_cl = np.loadtxt(cl_path, comments="%")
    data_cd = np.loadtxt(cd_path, comments="%")
    return data_cl[:, 0], data_cl[:, 1], data_cd[:, 0], data_cd[:, 1]


def test_extract_cycles_properties(sample_timeseries):
    t_cl, cl, t_cd, cd = sample_timeseries
    zc, cycles = extract_cycles(t_cl, cl, t_cd, cd)

    # 31 upward zero crossings and 30 full cycles in [0, 200] s
    assert len(zc) >= 30
    assert len(cycles) >= 29

    # Late cycles should have period ~ 6.07 s and St ~ 0.1637
    last_cycle = cycles[-1]
    assert 6.00 < last_cycle["T"] < 6.15
    assert 0.160 < last_cycle["St"] < 0.166
    assert 0.320 < last_cycle["amp_half"] < 0.340
    assert 1.350 < last_cycle["mean_cd"] < 1.380


def test_stability_onset_tiers(sample_timeseries):
    t_cl, cl, t_cd, cd = sample_timeseries
    _, cycles = extract_cycles(t_cl, cl, t_cd, cd)

    ref_amp = float(np.mean([c["amp_half"] for c in cycles[-5:]]))
    ref_st = float(np.mean([c["St"] for c in cycles[-5:]]))
    ref_cd = float(np.mean([c["mean_cd"] for c in cycles[-5:]]))

    # Tier 2 (1.0% tol): Expect onset at Cycle 13 (t_start ~ 92.8 s)
    t2 = find_stability_onset(cycles, 1.0, 0.1, 0.1, ref_amp, ref_st, ref_cd)
    assert t2 is not None
    assert t2["cycle_idx"] == 13
    assert abs(t2["t_start"] - 92.81) < 0.2

    # Tier 3 (0.5% tol): Expect onset at Cycle 14 (t_start ~ 98.9 s)
    t3 = find_stability_onset(cycles, 0.5, 0.05, 0.05, ref_amp, ref_st, ref_cd)
    assert t3 is not None
    assert t3["cycle_idx"] == 14
    assert abs(t3["t_start"] - 98.89) < 0.2


def test_sliding_stationarity(sample_timeseries):
    t_cl, cl, t_cd, cd = sample_timeseries
    _, cycles = extract_cycles(t_cl, cl, t_cd, cd)
    sliding_5 = compute_sliding_stationarity(cycles, window_size=5)

    # From Cycle 13 onwards (t_start >= 92.8 s), 5-cycle sliding drift for amplitude < 1.0%
    for win in sliding_5:
        if win["t_start"] >= 92.8:
            assert win["amp_drift_pct"] < 1.0
            assert win["t_drift_pct"] < 0.1
            assert win["cd_drift_pct"] < 0.1
