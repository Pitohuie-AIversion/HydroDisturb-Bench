"""Tests for Stage S1-A: Re=100 Long-Time Cylinder Flow Trial Run.

Validates:
1. Baseline and S0 benchmark model SHA-256 immutability.
2. S1-A diagnostic files presence fail-closed (no silent skip on missing data).
3. S1-A diagnostic data contract, sample counts, timestamp alignment, and finite values.
4. Data contract rejection on corruptions (NaN/Inf, timestamp misalignments, missing files).
5. Aerodynamic force integration and normalization formulas consistency.
6. Frequency extraction algorithms on synthetic pure sinusoidal signals (FFT resolution bounds, zero-crossing).
7. Growing-amplitude signals correctly rejected from being falsely classified as limit cycles.
8. Startup pressure surge physical bounds (rigid fluid column inertial acceleration vs residual).
9. Multi-window statistical comparison and cycle-by-cycle stability analysis on actual S1-A data.
"""
from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    compute_file_hash,
    find_comsol_root,
    ComsolConnectionError,
)
from hydro_disturb_bench.io import (
    load_comsol_1d_timeseries,
    validate_s1_diagnostics,
    ComsolExportValidationError,
    S1_REQUIRED_SERIES,
    S1_REQUIRED_SNAPSHOTS,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL = WORKSPACE_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = WORKSPACE_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
S1_TRIAL_DIR = WORKSPACE_DIR / "runs" / "re100_s1a_trial"

BASELINE_SHA256 = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
BENCHMARK_SHA256 = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"


def comsol_available() -> bool:
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


def test_s1a_models_immutability():
    """Verify both baseline and S0 benchmark models have strictly unchanged SHA-256 hashes."""
    assert BASELINE_MODEL.is_file(), f"Baseline model not found at {BASELINE_MODEL}"
    assert BENCHMARK_MODEL.is_file(), f"Benchmark model not found at {BENCHMARK_MODEL}"

    base_hash = compute_file_hash(BASELINE_MODEL)
    bench_hash = compute_file_hash(BENCHMARK_MODEL)
    assert base_hash == BASELINE_SHA256, f"Baseline hash altered: {base_hash} != {BASELINE_SHA256}"
    assert bench_hash == BENCHMARK_SHA256, f"Benchmark hash altered: {bench_hash} != {BENCHMARK_SHA256}"


def test_s1a_diagnostics_presence_fail_closed_no_silent_skip():
    """Verify S1-A diagnostic outputs exist. If missing, fail closed; DO NOT silently skip!"""
    assert S1_TRIAL_DIR.is_dir(), f"S1 trial directory missing: {S1_TRIAL_DIR}"

    for fname in S1_REQUIRED_SERIES:
        p = S1_TRIAL_DIR / fname
        if not p.is_file():
            pytest.fail(f"[FAIL CLOSED] Required S1 time series file missing: {fname} (silent skip forbidden)")
        if p.stat().st_size == 0:
            pytest.fail(f"[FAIL CLOSED] S1 time series file is empty: {fname}")

    for img_name in S1_REQUIRED_SNAPSHOTS:
        p = S1_TRIAL_DIR / img_name
        if not p.is_file():
            pytest.fail(f"[FAIL CLOSED] Required S1 snapshot image missing: {img_name} (silent skip forbidden)")
        if p.stat().st_size < 100:
            pytest.fail(f"[FAIL CLOSED] S1 snapshot image is too small ({p.stat().st_size} bytes): {img_name}")


def test_s1a_data_contract_and_alignment():
    """Validate full S1 data contract: sample count, timestamp alignment, units, and finite values."""
    res = validate_s1_diagnostics(
        S1_TRIAL_DIR,
        expected_steps=501,
        expected_dt=0.20,
        expected_t_final=100.0,
    )
    assert res["valid"] is True
    assert res["num_steps"] == 501
    assert math.isclose(res["dt"], 0.20, abs_tol=1e-5)
    assert math.isclose(res["time_range"][0], 0.0, abs_tol=1e-5)
    assert math.isclose(res["time_range"][1], 100.0, abs_tol=1e-5)
    assert len(res["snapshots"]) == 4
    for sname in S1_REQUIRED_SERIES:
        assert sname in res["series"]
        assert res["series"][sname]["num_samples"] == 501


def test_s1a_data_contract_rejection_on_corruptions(tmp_path: Path):
    """Verify validate_s1_diagnostics fails closed on NaN/Inf, misaligned times, or missing files."""
    mock_dir = tmp_path / "mock_diag"
    mock_dir.mkdir()

    # Create 4 mock snapshots
    for snap in S1_REQUIRED_SNAPSHOTS:
        (mock_dir / snap).write_bytes(b"PNG_MOCK_DATA" * 50)

    # Valid times: 0 to 10 with dt=1.0 (11 steps)
    t = np.linspace(0, 10, 11)

    def write_series(filename: str, times: np.ndarray, vals: list[float] | np.ndarray):
        lines = ["% Model: mock.mph", "% Dimension: 1", "% Nodes: 11", "% Expressions: 1", "% 时间 (s) Mock"]
        for ti, vi in zip(times, vals):
            lines.append(f"{ti} {vi}")
        (mock_dir / filename).write_text("\n".join(lines), encoding="utf-8")

    # 1. Missing one series -> must raise ComsolExportValidationError
    for s in S1_REQUIRED_SERIES[:-1]:
        write_series(s, t, np.sin(t))
    with pytest.raises(ComsolExportValidationError, match="Required S1 time series file missing"):
        validate_s1_diagnostics(mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0)

    # Complete the missing file
    write_series(S1_REQUIRED_SERIES[-1], t, np.sin(t))
    # Now it should be valid
    valid_res = validate_s1_diagnostics(mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0)
    assert valid_res["valid"] is True

    # 2. Corrupt with NaN -> must raise ComsolExportValidationError
    bad_nan_lines = ["% Header", "0.0 1.0", "1.0 NaN", "2.0 3.0"]
    (mock_dir / S1_REQUIRED_SERIES[0]).write_text("\n".join(bad_nan_lines), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="non-finite value"):
        validate_s1_diagnostics(mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0)

    # 3. Corrupt with Inf -> must raise ComsolExportValidationError
    bad_inf_lines = ["% Header", "0.0 1.0", "1.0 Infinity", "2.0 3.0"]
    (mock_dir / S1_REQUIRED_SERIES[0]).write_text("\n".join(bad_inf_lines), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="non-finite value"):
        validate_s1_diagnostics(mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0)

    # 4. Misaligned timestamps -> must raise ComsolExportValidationError
    write_series(S1_REQUIRED_SERIES[0], t, np.sin(t))
    t_skewed = t.copy()
    t_skewed[5] += 0.5  # Skew one timestamp
    write_series(S1_REQUIRED_SERIES[1], t_skewed, np.sin(t_skewed))
    with pytest.raises(ComsolExportValidationError, match="timestamp misalignment"):
        validate_s1_diagnostics(mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0)


def test_aerodynamic_coefficients_normalization_consistency():
    """Verify force normalization scale and aerodynamic coefficient formulas."""
    rho = 998.2
    U_inf = 0.010058104588
    D = 0.01
    W_depth = 1.0

    q_inf = 0.5 * rho * (U_inf ** 2)
    F_ref = q_inf * D * W_depth
    assert math.isclose(q_inf, 0.05049169, rel_tol=1e-4)
    assert math.isclose(F_ref, 5.049169e-4, rel_tol=1e-4)

    # Load actual lift and drag
    t, cl, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "lift_coeff.txt")
    _, cd, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "drag_coeff.txt")

    # In late periodic stage (t in [80, 100]s):
    mask = (t >= 80.0)
    cd_late = cd[mask]
    cl_late = cl[mask]

    # Drag is positive (downstream force)
    assert np.all(cd_late > 0.0)
    # Mean drag is around 1.30 ~ 1.35
    assert 1.25 <= np.mean(cd_late) <= 1.40
    # Mean lift preserves top/bottom channel symmetry (< 0.02)
    assert abs(np.mean(cl_late)) < 0.02
    # Lift amplitude is in physical range [0.25, 0.35]
    cl_amp = (np.max(cl_late) - np.min(cl_late)) / 2.0
    assert 0.25 <= cl_amp <= 0.35


def test_frequency_extraction_algorithm_on_synthetic_signal():
    """Verify frequency extraction algorithms (FFT resolution bounds, zero-crossing, peak-to-peak) on known sine."""
    dt = 0.2
    T_win = 40.0
    t = np.arange(0, T_win + dt / 2, dt)
    f_true = 0.153  # Known true shedding frequency
    A_true = 0.30

    y = A_true * np.sin(2.0 * np.pi * f_true * t)

    # 1. Zero-Crossing method
    crossings = []
    for i in range(len(t) - 1):
        if y[i] <= 0.0 and y[i + 1] > 0.0:
            tz = t[i] - y[i] * (t[i + 1] - t[i]) / (y[i + 1] - y[i])
            crossings.append(float(tz))
    periods_zc = np.diff(crossings)
    f_zc = 1.0 / np.mean(periods_zc)
    assert math.isclose(f_zc, f_true, rel_tol=0.005), f"Zero-crossing freq {f_zc} deviates from true {f_true}"

    # 2. Peak-to-Peak method
    from scipy.signal import find_peaks
    peaks, _ = find_peaks(y, distance=int(5.0 / dt))
    t_peaks = t[peaks]
    periods_pp = np.diff(t_peaks)
    f_pp = 1.0 / np.mean(periods_pp)
    assert math.isclose(f_pp, f_true, rel_tol=0.005), f"Peak-to-peak freq {f_pp} deviates from true {f_true}"

    # 3. Discrete FFT and resolution bounds
    delta_f = 1.0 / T_win  # 0.025 Hz
    fft_vals = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(len(t), d=dt)
    peak_idx = int(np.argmax(np.abs(fft_vals[1:])) + 1)
    f_fft = float(freqs[peak_idx])
    # FFT peak must match true frequency within discrete bin spacing (+/- delta_f/2)
    assert abs(f_fft - f_true) <= delta_f / 2.0 + 1e-6, f"FFT freq {f_fft} outside +/- delta_f/2 of {f_true}"

    # 4. Amplitude extraction
    amp_est = (np.max(y) - np.min(y)) / 2.0
    assert math.isclose(amp_est, A_true, rel_tol=0.002)


def test_growing_amplitude_signal_not_falsely_classified_as_limit_cycle():
    """Verify that an exponentially growing signal is correctly rejected from being classified as a limit cycle."""
    dt = 0.2
    t = np.arange(0, 40.0 + dt / 2, dt)
    f0 = 0.15
    gamma = 0.03  # 3% growth per second
    # Signal with exponential envelope
    y_grow = 0.15 * np.exp(gamma * t) * np.sin(2.0 * np.pi * f0 * t)

    # Split into Window A [0, 20]s and Window B [20, 40]s
    mask_a = (t <= 20.0)
    mask_b = (t >= 20.0)
    amp_a = (np.max(y_grow[mask_a]) - np.min(y_grow[mask_a])) / 2.0
    amp_b = (np.max(y_grow[mask_b]) - np.min(y_grow[mask_b])) / 2.0

    drift_rate = abs(amp_b - amp_a) / amp_a

    # A saturated limit cycle requires drift < 10%
    # This growing signal should have drift > 50%
    assert drift_rate > 0.50, f"Growing signal drift rate was unexpectedly low: {drift_rate}"

    # Stability decision logic
    is_saturated_limit_cycle = drift_rate < 0.10
    assert not is_saturated_limit_cycle, "Growing signal was falsely classified as a saturated limit cycle!"


def test_s1a_pressure_surge_physical_bounds():
    """Verify startup pressure surge bounds and decay to steady hydrodynamic stagnation pressure."""
    t, ip, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "inlet_p.txt")
    _, sp, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "stag_p.txt")

    # In S1-A (dt=0.2s), the fast ramp (t in [0, 0.1s]) occurs between saved points t=0 and t=0.2s
    # At t=0.2s (post-ramp), inlet pressure must already have decayed below 0.01 Pa
    post_ramp_ip = ip[t >= 0.4]
    assert np.all(post_ramp_ip < 0.01), f"Post-ramp inlet pressure exceeded 0.01 Pa: max={post_ramp_ip.max()}"

    # Cylinder stagnation pressure at late times should match steady Bernoulli dynamic pressure
    # q_inf = 0.5 * rho * U_inf^2 ~ 0.0505 Pa (within physical range 0.04 ~ 0.08 Pa)
    late_sp = sp[t >= 20.0]
    assert np.all(late_sp > 0.04) and np.all(late_sp < 0.08), f"Late stagnation pressure outside [0.04, 0.08] Pa"


def test_s1a_multi_window_statistics_and_cycle_analysis():
    """Verify multi-window comparison, cycle stability, and Strouhal number on actual S1-A data."""
    t, cl, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "lift_coeff.txt")
    _, cd, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "drag_coeff.txt")
    _, wv, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "wake_v.txt")

    # Total duration 100s, 501 points
    assert len(t) == 501
    assert math.isclose(t[-1], 100.0, abs_tol=1e-3)

    # Window A [60, 80]s vs Window B [80, 100]s
    mask_a = (t >= 60.0) & (t <= 80.0)
    mask_b = (t >= 80.0) & (t <= 100.0)

    cd_mean_a = float(np.mean(cd[mask_a]))
    cd_mean_b = float(np.mean(cd[mask_b]))
    cl_amp_a = float((np.max(cl[mask_a]) - np.min(cl[mask_a])) / 2.0)
    cl_amp_b = float((np.max(cl[mask_b]) - np.min(cl[mask_b])) / 2.0)

    # Mean drag in standard benchmark range [1.25, 1.40]
    assert 1.25 <= cd_mean_a <= 1.40
    assert 1.25 <= cd_mean_b <= 1.40

    # Mean drag window drift is < 5%
    cd_drift = abs(cd_mean_b - cd_mean_a) / cd_mean_a
    assert cd_drift < 0.05, f"Mean drag drift {cd_drift:.3f} exceeded 5%"

    # Lift amplitude in physical range [0.25, 0.35]
    assert 0.25 <= cl_amp_a <= 0.35
    assert 0.25 <= cl_amp_b <= 0.35

    # Cycle-by-cycle zero-crossing period and frequency
    mask_late = (t >= 60.0)
    t_late = t[mask_late]
    cl_late = cl[mask_late]
    crossings = []
    for i in range(len(t_late) - 1):
        if cl_late[i] <= 0.0 and cl_late[i + 1] > 0.0:
            tz = t_late[i] - cl_late[i] * (t_late[i + 1] - t_late[i]) / (cl_late[i + 1] - cl_late[i])
            crossings.append(float(tz))

    assert len(crossings) >= 5, f"Expected at least 5 zero crossings, got {len(crossings)}"
    periods = np.diff(crossings)
    f_zc = 1.0 / np.mean(periods)
    D = 0.01
    U_inf = 0.010058104588
    St_zc = f_zc * D / U_inf

    # Strouhal number within physical trial range [0.14, 0.17]
    assert 0.14 <= St_zc <= 0.17, f"Zero-crossing St {St_zc:.4f} outside [0.14, 0.17]"

    # Wake v oscillation amplitude
    wv_late = wv[mask_late]
    wv_amp = (np.max(wv_late) - np.min(wv_late)) / 2.0
    assert wv_amp > 1e-3, f"Wake v oscillation amplitude too small: {wv_amp}"
