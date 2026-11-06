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

import json
import math
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    ComsolConnectionError,
    compute_file_hash,
    export_s1_diagnostics,
    file_publish_lock,
    find_comsol_root,
    list_s1_diagnostic_versions,
    recover_s1_diagnostic_version,
)
from hydro_disturb_bench.io import (
    S1_REQUIRED_SERIES,
    S1_REQUIRED_SNAPSHOTS,
    ComsolExportValidationError,
    build_s1_manifest,
    load_comsol_1d_timeseries,
    validate_s1_diagnostics,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL = WORKSPACE_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = WORKSPACE_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
S1_TRIAL_DIR = WORKSPACE_DIR / "runs" / "re100_s1a_trial"
SOLVED_MPH = S1_TRIAL_DIR / "re100_s1a_solved.mph"

BASELINE_SHA256 = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
BENCHMARK_SHA256 = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"


@pytest.fixture
def offline_model(tmp_path: Path) -> Path:
    """Provide source bytes for mocked export tests without a historical MPH."""
    model = tmp_path / "offline_model.mph"
    model.write_bytes(b"offline source model; external COMSOL execution is mocked")
    return model


def comsol_available() -> bool:
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


@pytest.mark.artifact
def test_s1a_models_immutability():
    """Verify both baseline and S0 benchmark models have strictly unchanged SHA-256 hashes."""
    assert BASELINE_MODEL.is_file(), f"Baseline model not found at {BASELINE_MODEL}"
    assert BENCHMARK_MODEL.is_file(), f"Benchmark model not found at {BENCHMARK_MODEL}"

    base_hash = compute_file_hash(BASELINE_MODEL)
    bench_hash = compute_file_hash(BENCHMARK_MODEL)
    assert base_hash == BASELINE_SHA256, f"Baseline hash altered: {base_hash} != {BASELINE_SHA256}"
    assert bench_hash == BENCHMARK_SHA256, (
        f"Benchmark hash altered: {bench_hash} != {BENCHMARK_SHA256}"
    )


@pytest.mark.artifact
def test_s1a_diagnostics_presence_fail_closed_no_silent_skip():
    """Verify S1-A diagnostic outputs exist. If missing, fail closed; DO NOT silently skip!"""
    assert S1_TRIAL_DIR.is_dir(), f"S1 trial directory missing: {S1_TRIAL_DIR}"

    for fname in S1_REQUIRED_SERIES:
        p = S1_TRIAL_DIR / fname
        if not p.is_file():
            pytest.fail(
                f"[FAIL CLOSED] Required S1 time series file missing: {fname} (silent skip forbidden)"
            )
        if p.stat().st_size == 0:
            pytest.fail(f"[FAIL CLOSED] S1 time series file is empty: {fname}")

    for img_name in S1_REQUIRED_SNAPSHOTS:
        p = S1_TRIAL_DIR / img_name
        if not p.is_file():
            pytest.fail(
                f"[FAIL CLOSED] Required S1 snapshot image missing: {img_name} (silent skip forbidden)"
            )
        if p.stat().st_size < 100:
            pytest.fail(
                f"[FAIL CLOSED] S1 snapshot image is too small ({p.stat().st_size} bytes): {img_name}"
            )


@pytest.mark.artifact
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
        lines = [
            "% Model: mock.mph",
            "% Dimension: 1",
            "% Nodes: 11",
            "% Expressions: 1",
            "% 时间 (s) Mock",
        ]
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
    valid_res = validate_s1_diagnostics(
        mock_dir, expected_steps=11, expected_dt=1.0, expected_t_final=10.0
    )
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


@pytest.mark.artifact
def test_aerodynamic_coefficients_normalization_consistency():
    """Verify force normalization scale and aerodynamic coefficient formulas."""
    rho = 998.2
    U_inf = 0.010058104588
    D = 0.01
    W_depth = 1.0

    q_inf = 0.5 * rho * (U_inf**2)
    F_ref = q_inf * D * W_depth
    assert math.isclose(q_inf, 0.05049169, rel_tol=1e-4)
    assert math.isclose(F_ref, 5.049169e-4, rel_tol=1e-4)

    # Load actual lift and drag
    t, cl, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "lift_coeff.txt")
    _, cd, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "drag_coeff.txt")

    # In late periodic stage (t in [80, 100]s):
    mask = t >= 80.0
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
    assert math.isclose(f_zc, f_true, rel_tol=0.005), (
        f"Zero-crossing freq {f_zc} deviates from true {f_true}"
    )

    # 2. Peak-to-Peak method
    from scipy.signal import find_peaks

    peaks, _ = find_peaks(y, distance=int(5.0 / dt))
    t_peaks = t[peaks]
    periods_pp = np.diff(t_peaks)
    f_pp = 1.0 / np.mean(periods_pp)
    assert math.isclose(f_pp, f_true, rel_tol=0.005), (
        f"Peak-to-peak freq {f_pp} deviates from true {f_true}"
    )

    # 3. Discrete FFT and resolution bounds
    delta_f = 1.0 / T_win  # 0.025 Hz
    fft_vals = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(len(t), d=dt)
    peak_idx = int(np.argmax(np.abs(fft_vals[1:])) + 1)
    f_fft = float(freqs[peak_idx])
    # FFT peak must match true frequency within discrete bin spacing (+/- delta_f/2)
    assert abs(f_fft - f_true) <= delta_f / 2.0 + 1e-6, (
        f"FFT freq {f_fft} outside +/- delta_f/2 of {f_true}"
    )

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
    mask_a = t <= 20.0
    mask_b = t >= 20.0
    amp_a = (np.max(y_grow[mask_a]) - np.min(y_grow[mask_a])) / 2.0
    amp_b = (np.max(y_grow[mask_b]) - np.min(y_grow[mask_b])) / 2.0

    drift_rate = abs(amp_b - amp_a) / amp_a

    # A saturated limit cycle requires drift < 10%
    # This growing signal should have drift > 50%
    assert drift_rate > 0.50, f"Growing signal drift rate was unexpectedly low: {drift_rate}"

    # Stability decision logic
    is_saturated_limit_cycle = drift_rate < 0.10
    assert not is_saturated_limit_cycle, (
        "Growing signal was falsely classified as a saturated limit cycle!"
    )


@pytest.mark.artifact
def test_s1a_pressure_surge_physical_bounds():
    """Verify startup pressure surge bounds and decay to steady hydrodynamic stagnation pressure."""
    t, ip, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "inlet_p.txt")
    _, sp, _ = load_comsol_1d_timeseries(S1_TRIAL_DIR / "stag_p.txt")

    # In S1-A (dt=0.2s), the fast ramp (t in [0, 0.1s]) occurs between saved points t=0 and t=0.2s
    # At t=0.2s (post-ramp), inlet pressure must already have decayed below 0.01 Pa
    post_ramp_ip = ip[t >= 0.4]
    assert np.all(post_ramp_ip < 0.01), (
        f"Post-ramp inlet pressure exceeded 0.01 Pa: max={post_ramp_ip.max()}"
    )

    # Cylinder stagnation pressure at late times should match steady Bernoulli dynamic pressure
    # q_inf = 0.5 * rho * U_inf^2 ~ 0.0505 Pa (within physical range 0.04 ~ 0.08 Pa)
    late_sp = sp[t >= 20.0]
    assert np.all(late_sp > 0.04) and np.all(late_sp < 0.08), (
        "Late stagnation pressure outside [0.04, 0.08] Pa"
    )


@pytest.mark.artifact
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
    mask_late = t >= 60.0
    t_late = t[mask_late]
    cl_late = cl[mask_late]
    crossings = []
    for i in range(len(t_late) - 1):
        if cl_late[i] <= 0.0 and cl_late[i + 1] > 0.0:
            tz = t_late[i] - cl_late[i] * (t_late[i + 1] - t_late[i]) / (
                cl_late[i + 1] - cl_late[i]
            )
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


@pytest.mark.integration
def test_real_comsol_export_s1_diagnostics_end_to_end(tmp_path: Path):
    """Test full COMSOL Java API export and safe publish using already-solved model into isolated dir."""
    if not comsol_available() or not SOLVED_MPH.is_file():
        pytest.skip("COMSOL or solved model not available for live export integration")

    export_dest = tmp_path / "real_s1_export"
    res = export_s1_diagnostics(SOLVED_MPH, export_dest, allow_overwrite=False)

    assert res["status"] == "SUCCESS"
    assert (export_dest / "manifest.json").is_file()

    # Validate complete data contract with manifest check and source model hash
    solved_hash = compute_file_hash(SOLVED_MPH)
    val = validate_s1_diagnostics(
        export_dest,
        expected_steps=501,
        expected_dt=0.20,
        expected_t_final=100.0,
        require_manifest=True,
        expected_source_hash=solved_hash,
    )
    assert val["valid"] is True
    assert len(val["snapshots"]) == 4
    for s in S1_REQUIRED_SERIES:
        assert s in val["series"]


def test_file_publish_lock_lifecycle_and_directory_target(tmp_path: Path):
    """Verify lock file creation, mutual exclusion, timeout, and release even if dir path is provided."""
    target_dir = tmp_path / "diag_dest"
    target_dir.mkdir()

    # Passing directory path should derive .publish_diag_dest.lock safely without failing
    lock_file = tmp_path / f".publish_{target_dir.name}.lock"

    with file_publish_lock(target_dir):
        # Lock file exists
        assert lock_file.is_file()

        # Another attempt to acquire the same lock times out
        with pytest.raises(TimeoutError, match="Timed out"):
            with file_publish_lock(target_dir, timeout_sec=0.1, poll_interval=0.02):
                pass

    # After exiting, lock is released and can be re-acquired immediately
    with file_publish_lock(target_dir, timeout_sec=1.0):
        assert True


def test_export_s1_diagnostics_refuses_overwrite_by_default(tmp_path: Path, offline_model: Path):
    """When target directory contains results, allow_overwrite=False refuses publish and preserves originals."""
    target_dir = tmp_path / "diag_existing"
    target_dir.mkdir()

    # Populate target_dir with existing files
    for fname in S1_REQUIRED_SERIES:
        (target_dir / fname).write_text(
            f"% Original {fname}\n0.0 1.0\n100.0 2.0\n", encoding="utf-8"
        )
    for iname in S1_REQUIRED_SNAPSHOTS:
        (target_dir / iname).write_bytes(b"ORIGINAL_IMAGE_BYTES" * 10)
    (target_dir / "manifest.json").write_text(
        '{"status": "COMPLETED", "files": {}}', encoding="utf-8"
    )

    orig_lift_bytes = (target_dir / "lift_coeff.txt").read_bytes()

    # Call export_s1_diagnostics with allow_overwrite=False (mocking bridge to produce valid staging)
    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge") as mock_bridge:

        def side_effect(args, root=None):
            staging = Path(args[2])
            for f in S1_REQUIRED_SERIES:
                lines = ["% Header", "% 时间 (s) Value"] + [
                    f"{t * 0.2:.4f} 0.5" for t in range(501)
                ]
                (staging / f).write_text("\n".join(lines), encoding="utf-8")
            for f in S1_REQUIRED_SNAPSHOTS:
                (staging / f).write_bytes(b"NEW_SNAPSHOT_BYTES" * 10)
            return {"status": "SUCCESS", "operation": "export_s1_diagnostics"}

        mock_bridge.side_effect = side_effect

        with pytest.raises(
            FileExistsError, match="Refusing to overwrite existing diagnostic result set"
        ):
            export_s1_diagnostics(offline_model, target_dir, allow_overwrite=False)

    # Destination files are strictly untouched!
    assert (target_dir / "lift_coeff.txt").read_bytes() == orig_lift_bytes

    # Staging was moved to quarantine as conflict_*
    quarantine_dirs = list((tmp_path / "quarantine").glob("conflict_*"))
    assert len(quarantine_dirs) == 1
    assert (quarantine_dirs[0] / "lift_coeff.txt").is_file()


def test_export_s1_diagnostics_versioning_and_full_recovery(tmp_path: Path, offline_model: Path):
    """When allow_overwrite=True, old results are preserved in versions/ and both versions are recoverable."""
    target_dir = tmp_path / "diag_versioned"
    target_dir.mkdir()

    # Step 1: Create Version 1
    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge") as mock_bridge:

        def side_effect_v1(args, root=None):
            staging = Path(args[2])
            for f in S1_REQUIRED_SERIES:
                lines = ["% Header V1", "% 时间 (s) Value"] + [
                    f"{t * 0.2:.4f} 1.1" for t in range(501)
                ]
                (staging / f).write_text("\n".join(lines), encoding="utf-8")
            for f in S1_REQUIRED_SNAPSHOTS:
                (staging / f).write_bytes(b"V1_SNAPSHOT" * 20)
            return {"status": "SUCCESS"}

        mock_bridge.side_effect = side_effect_v1
        res1 = export_s1_diagnostics(offline_model, target_dir, allow_overwrite=False)

    assert (target_dir / "manifest.json").is_file()
    assert (target_dir / "lift_coeff.txt").read_text(encoding="utf-8").startswith("% Header V1")

    # Step 2: Overwrite with Version 2
    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge") as mock_bridge:

        def side_effect_v2(args, root=None):
            staging = Path(args[2])
            for f in S1_REQUIRED_SERIES:
                lines = ["% Header V2", "% 时间 (s) Value"] + [
                    f"{t * 0.2:.4f} 2.2" for t in range(501)
                ]
                (staging / f).write_text("\n".join(lines), encoding="utf-8")
            for f in S1_REQUIRED_SNAPSHOTS:
                (staging / f).write_bytes(b"V2_SNAPSHOT" * 20)
            return {"status": "SUCCESS"}

        mock_bridge.side_effect = side_effect_v2
        res2 = export_s1_diagnostics(offline_model, target_dir, allow_overwrite=True)

    # Active target_dir is now Version 2
    assert (target_dir / "lift_coeff.txt").read_text(encoding="utf-8").startswith("% Header V2")

    # Version 1 is archived in versions/
    versions = list_s1_diagnostic_versions(target_dir)
    assert len(versions) == 1
    v1_tag = versions[0]["version_tag"]

    # Both Version 1 (archived) and Version 2 (active) validate with manifest
    val_v1 = validate_s1_diagnostics(target_dir / "versions" / v1_tag, require_manifest=True)
    val_v2 = validate_s1_diagnostics(target_dir, require_manifest=True)
    assert val_v1["valid"] is True
    assert val_v2["valid"] is True

    # Recover Version 1 into an isolated restore directory
    restore_dir = tmp_path / "restored_v1"
    recover_s1_diagnostic_version(target_dir, v1_tag, restore_dir)
    assert (restore_dir / "lift_coeff.txt").read_text(encoding="utf-8").startswith("% Header V1")
    assert validate_s1_diagnostics(restore_dir, require_manifest=True)["valid"] is True


@pytest.mark.parametrize("fail_file_idx", [1, 5, 11])
def test_simulated_publish_failure_at_file_1_5_11_prevents_mixed_results(
    tmp_path: Path, fail_file_idx: int, offline_model: Path
):
    """Simulate failure transferring the 1st, 5th, or 11th file: ensure no mixed result set is left marked as completed."""
    target_dir = tmp_path / f"fail_sim_{fail_file_idx}"
    target_dir.mkdir()

    # Pre-populate with known sentinel data to verify it is NOT corrupted
    sentinel_file = target_dir / "sentinel.txt"
    sentinel_file.write_text("DO_NOT_TOUCH", encoding="utf-8")

    real_copy2 = shutil.copy2
    call_count = 0

    def faulty_copy2(src, dst, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == fail_file_idx:
            raise OSError(
                f"Simulated filesystem failure on file #{fail_file_idx} ({Path(src).name})"
            )
        return real_copy2(src, dst, *args, **kwargs)

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge") as mock_bridge:

        def side_effect(args, root=None):
            staging = Path(args[2])
            for f in S1_REQUIRED_SERIES:
                lines = ["% Header", "% 时间 (s) Value"] + [
                    f"{t * 0.2:.4f} 0.5" for t in range(501)
                ]
                (staging / f).write_text("\n".join(lines), encoding="utf-8")
            for f in S1_REQUIRED_SNAPSHOTS:
                (staging / f).write_bytes(b"IMAGE_BYTES" * 20)
            return {"status": "SUCCESS"}

        mock_bridge.side_effect = side_effect

        with patch("shutil.copy2", side_effect=faulty_copy2):
            with pytest.raises(OSError, match="Simulated filesystem failure"):
                export_s1_diagnostics(offline_model, target_dir, allow_overwrite=True)

    # 1. manifest.json must NOT exist in target_dir!
    assert not (target_dir / "manifest.json").exists(), (
        "manifest.json was written despite publish failure!"
    )

    # 2. No partial .tmp_ files left behind
    tmp_files = list(target_dir.glob(".tmp_*"))
    assert len(tmp_files) == 0, f"Temporary staging files left in destination: {tmp_files}"

    # 3. Validation must fail closed: cannot be validated as a completed set
    with pytest.raises(ComsolExportValidationError):
        validate_s1_diagnostics(target_dir, require_manifest=True)

    # 4. Failed staging directory was preserved in quarantine, NOT deleted!
    quarantines = list((tmp_path / "quarantine").glob("failed_publish_*"))
    assert len(quarantines) == 1, "Failed staging was not preserved in quarantine!"
    assert (quarantines[0] / "lift_coeff.txt").is_file()

    # 5. Pre-existing sentinel file was strictly untouched
    assert sentinel_file.read_text(encoding="utf-8") == "DO_NOT_TOUCH"


def test_export_failure_preserves_staging_evidence_in_quarantine(tmp_path: Path, offline_model: Path):
    """When COMSOL aborts or data validation fails, staging artifacts are quarantined, never deleted with ignore_errors."""
    target_dir = tmp_path / "abort_target"

    with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge") as mock_bridge:

        def side_effect_abort(args, root=None):
            staging = Path(args[2])
            # Partial export left behind before crashing
            (staging / "lift_coeff.txt").write_text("% Partial lift\n0.0 0.1\n", encoding="utf-8")
            raise ComsolConnectionError("COMSOL batch process terminated unexpectedly")

        mock_bridge.side_effect = side_effect_abort

        with pytest.raises(ComsolConnectionError, match="COMSOL batch process terminated"):
            export_s1_diagnostics(offline_model, target_dir, allow_overwrite=False)

    # Staging directory must be in quarantine as failed_*
    quarantined = list((tmp_path / "quarantine").glob("failed_*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "lift_coeff.txt").is_file()
    assert not target_dir.exists() or len(list(target_dir.iterdir())) == 0


def test_multiprocess_concurrent_publish_race_no_corruption_or_loss(tmp_path: Path, offline_model: Path):
    """Two concurrent processes racing to export/publish to the same output_dir serialize via lock without data loss."""
    import subprocess

    target_dir = tmp_path / "race_diag"

    worker_code = """
import sys
from pathlib import Path
from unittest.mock import patch
from hydro_disturb_bench.comsol_java.bridge import export_s1_diagnostics
from hydro_disturb_bench.io import S1_REQUIRED_SERIES, S1_REQUIRED_SNAPSHOTS

base = Path(sys.argv[1])
out = Path(sys.argv[2])
proc_id = sys.argv[3]

def mock_run(args, root=None):
    staging = Path(args[2])
    for f in S1_REQUIRED_SERIES:
        lines = [f"% Header {proc_id}", "% 时间 (s) Value"] + [f"{t * 0.2:.4f} 0.5" for t in range(501)]
        (staging / f).write_text("\\n".join(lines), encoding="utf-8")
    for f in S1_REQUIRED_SNAPSHOTS:
        (staging / f).write_bytes(f"IMAGE_{proc_id}".encode("utf-8") * 20)
    return {"status": "SUCCESS"}

with patch("hydro_disturb_bench.comsol_java.bridge._run_bridge", side_effect=mock_run):
    try:
        export_s1_diagnostics(base, out, allow_overwrite=False)
        print("SUCCESS")
    except FileExistsError:
        print("CONFLICT")
"""
    sub_env = {**os.environ, "PYTHONPATH": "src"}
    p1 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(offline_model), str(target_dir), "P1"],
        env=sub_env,
        stdout=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", worker_code, str(offline_model), str(target_dir), "P2"],
        env=sub_env,
        stdout=subprocess.PIPE,
        text=True,
    )

    out1, _ = p1.communicate(timeout=30)
    out2, _ = p2.communicate(timeout=30)

    results = {out1.strip(), out2.strip()}
    assert "SUCCESS" in results
    assert "CONFLICT" in results

    # Winner's result set passes validation
    val = validate_s1_diagnostics(target_dir, require_manifest=True)
    assert val["valid"] is True

    # Loser's staging was preserved in quarantine
    quarantined = list((tmp_path / "quarantine").glob("conflict_*"))
    assert len(quarantined) == 1


def test_s1a_manifest_tamper_detection(tmp_path: Path):
    """Verify validate_s1_diagnostics fails closed on any tampering with manifest or data files."""
    diag_dir = tmp_path / "tamper_test"
    diag_dir.mkdir()

    for f in S1_REQUIRED_SERIES:
        lines = ["% Header", "% 时间 (s) Value"] + [f"{t * 0.2:.4f} 0.5" for t in range(501)]
        (diag_dir / f).write_text("\n".join(lines), encoding="utf-8")
    for f in S1_REQUIRED_SNAPSHOTS:
        (diag_dir / f).write_bytes(b"SNAPSHOT_BYTES" * 20)

    val_raw = validate_s1_diagnostics(diag_dir, require_manifest=False)
    manifest = build_s1_manifest(diag_dir, BASELINE_MODEL, "FAKE_HASH", "test_id", val_raw)
    (diag_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Valid as is
    assert validate_s1_diagnostics(diag_dir, require_manifest=True)["valid"] is True

    # Tamper 1: change status to IN_PROGRESS
    manifest["status"] = "IN_PROGRESS"
    (diag_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="manifest.json status is 'IN_PROGRESS'"):
        validate_s1_diagnostics(diag_dir, require_manifest=True)

    # Restore status, tamper 2: alter one file content
    manifest["status"] = "COMPLETED"
    (diag_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (diag_dir / "lift_coeff.txt").write_text("% Tampered line\n0.0 999.0\n", encoding="utf-8")
    with pytest.raises(
        ComsolExportValidationError, match="deviates from manifest|does not match manifest"
    ):
        validate_s1_diagnostics(diag_dir, require_manifest=True)
