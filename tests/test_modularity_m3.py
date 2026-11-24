"""Unit tests for Milestone 3 modularity extractions: time_convergence and stability."""

from __future__ import annotations

import math

import numpy as np
import pytest

from hydro_disturb_bench.stability import (
    compute_sliding_stationarity,
    detect_limit_cycle_onset,
    extract_cycles,
    find_stability_onset,
)
from hydro_disturb_bench.time_convergence import (
    compute_cycle_half_amplitude,
    compute_cycle_mean,
    compute_demeaned_rms,
    compute_strouhal_number,
    evaluate_limit_cycle_stability,
    integrate_exact_window,
)
from scripts import analyze_cl_stability_onset, verify_and_compare_s1b1


def test_reexport_identity():
    """Verify backward-compatible re-exports in scripts match library functions."""
    assert verify_and_compare_s1b1.integrate_exact_window is integrate_exact_window
    assert verify_and_compare_s1b1.compute_cycle_mean is compute_cycle_mean
    assert verify_and_compare_s1b1.compute_demeaned_rms is compute_demeaned_rms
    assert verify_and_compare_s1b1.compute_cycle_half_amplitude is compute_cycle_half_amplitude
    assert verify_and_compare_s1b1.compute_strouhal_number is compute_strouhal_number
    assert (
        verify_and_compare_s1b1.evaluate_limit_cycle_stability
        is evaluate_limit_cycle_stability
    )

    assert analyze_cl_stability_onset.extract_cycles is extract_cycles
    assert analyze_cl_stability_onset.compute_sliding_stationarity is compute_sliding_stationarity
    assert analyze_cl_stability_onset.find_stability_onset is find_stability_onset
    assert analyze_cl_stability_onset.detect_limit_cycle_onset is detect_limit_cycle_onset


def test_time_convergence_exact_integration_and_rms():
    """Verify exact window integration, cycle mean, half-amplitude, and de-meaned RMS."""
    t = np.linspace(0.0, 20.0, 2001)
    # Constant signal
    y_const = np.full_like(t, 3.5)
    integral, mean_val = integrate_exact_window(t, y_const, 1.23, 9.87)
    assert mean_val == pytest.approx(3.5, abs=1e-12)
    assert integral == pytest.approx(3.5 * (9.87 - 1.23), abs=1e-12)
    assert compute_cycle_mean(t, y_const, 1.23, 9.87) == pytest.approx(3.5, abs=1e-12)

    # Sinusoid with DC offset
    period = 4.0
    amp = 0.6
    offset = 1.2
    y_sin = offset + amp * np.sin(2.0 * math.pi * t / period)
    y_bar, rms = compute_demeaned_rms(t, y_sin, 2.0, 14.0)
    assert y_bar == pytest.approx(offset, abs=1e-4)
    assert rms == pytest.approx(amp / math.sqrt(2.0), rel=1e-3)
    assert compute_cycle_half_amplitude(t, y_sin, 2.0, 14.0) == pytest.approx(amp, rel=1e-3)

    with pytest.raises(ValueError, match="Invalid duration"):
        integrate_exact_window(t, y_const, 5.0, 5.0)
    with pytest.raises(ValueError, match="Invalid duration"):
        compute_cycle_half_amplitude(t, y_const, 5.0, 4.0)


def test_strouhal_and_limit_cycle_stability():
    """Verify Strouhal number calculation and stability drift threshold evaluation."""
    st = compute_strouhal_number(frequency=0.165, characteristic_length=0.01, u_inf=0.01)
    assert st == pytest.approx(0.165, abs=1e-12)

    with pytest.raises(ValueError, match="characteristic_length"):
        compute_strouhal_number(0.165, 0.0, 0.01)
    with pytest.raises(ValueError, match="u_inf"):
        compute_strouhal_number(0.165, 0.01, -0.01)
    with pytest.raises(ValueError, match="frequency"):
        compute_strouhal_number(-0.1, 0.01, 0.01)

    ok, drifts, msg = evaluate_limit_cycle_stability(
        periods=[6.0, 6.01, 5.99, 6.0, 6.0],
        amplitudes=[0.33, 0.331, 0.329, 0.33, 0.33],
        cds=[1.36, 1.361, 1.359, 1.36, 1.36],
    )
    assert ok is True
    assert msg == "Stable"
    assert drifts["st_drift_pct"] < 1.0


def test_stability_onset_detection_on_synthetic_wake():
    """Verify cycle extraction and limit-cycle onset detection on a synthetic transient wake."""
    t = np.linspace(0.0, 120.0, 6001)
    period = 6.0
    # Amplitude grows from 0.1 and saturates at 0.33 around t=40s
    envelope = 0.33 * (1.0 - 0.7 * np.exp(-t / 10.0))
    cl = envelope * np.sin(2.0 * math.pi * t / period)
    cd = np.full_like(t, 1.36)

    zc, cycles = extract_cycles(t, cl, t, cd)
    assert len(zc) >= 18
    assert len(cycles) >= 17

    sliding = compute_sliding_stationarity(cycles, window_size=5)
    assert len(sliding) == len(cycles) - 4

    onset_t = detect_limit_cycle_onset(t, cl, window_cycles=5, tol_pct=1.0)
    assert onset_t is not None
    assert 30.0 <= onset_t <= 90.0

    ref_amp = cycles[-1]["amp_half"]
    ref_st = cycles[-1]["St"]
    ref_cd = cycles[-1]["mean_cd"]
    tier = find_stability_onset(cycles, 1.0, 1.0, 1.0, ref_amp, ref_st, ref_cd)
    assert tier is not None
    assert tier["t_start"] <= 90.0


def test_stability_onset_rejects_drifting_drag_with_stationary_lift():
    """Stable lift alone must not mark a wake with drifting drag as stationary."""
    t = np.linspace(0.0, 120.0, 6001)
    cl = np.sin(2.0 * math.pi * t / 6.0)

    assert detect_limit_cycle_onset(t, cl, t, np.ones_like(t)) == pytest.approx(0.0)
    assert detect_limit_cycle_onset(t, cl, t, 1.0 + t) is None
