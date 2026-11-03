"""Tests for Stage S1-A2 (200s) long-time wake stability, cycle statistics, and diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
)
from hydro_disturb_bench.io import (
    ComsolExportValidationError,
    load_comsol_1d_timeseries,
    validate_s1_diagnostics,
)

BASELINE_CASE = Path("case/cylinder_flow.mph")
BENCHMARK_MODEL = Path("models/cylinder_2d/re100_benchmark.mph")
S1A_SOLVED_MPH = Path("runs/re100_s1a_trial/re100_s1a_solved.mph")
S1A2_DIR = Path("runs/re100_s1a2_trial")
S1A2_SOLVED_MPH = S1A2_DIR / "re100_s1a2_solved.mph"

EXPECTED_CASE_HASH = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
EXPECTED_S0_HASH = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"
EXPECTED_S1A_HASH = "0BB3354255DA95F60622DAFDFDA11EB131383ECD3F9BA113B980D363CB0520DC"

U_INF = 0.010058104588
D = 0.01


def test_baseline_and_s1a_models_immutability():
    """Verify that original baseline, S0 benchmark, and S1-A solved models are strictly unmodified."""
    assert BASELINE_CASE.is_file(), f"Missing {BASELINE_CASE}"
    assert BENCHMARK_MODEL.is_file(), f"Missing {BENCHMARK_MODEL}"
    assert S1A_SOLVED_MPH.is_file(), f"Missing {S1A_SOLVED_MPH}"

    h_case = compute_file_hash(BASELINE_CASE)
    h_s0 = compute_file_hash(BENCHMARK_MODEL)
    h_s1a = compute_file_hash(S1A_SOLVED_MPH)

    assert h_case == EXPECTED_CASE_HASH, f"Baseline case mutated: {h_case} != {EXPECTED_CASE_HASH}"
    assert h_s0 == EXPECTED_S0_HASH, f"S0 benchmark mutated: {h_s0} != {EXPECTED_S0_HASH}"
    assert h_s1a == EXPECTED_S1A_HASH, f"S1-A solved model mutated: {h_s1a} != {EXPECTED_S1A_HASH}"


def test_s1a2_solution_time_table_alignment():
    """Verify that S1-A2 solved model metadata matches the 200s extended schedule."""
    import json

    meta_file = S1A2_DIR / "solver_metadata.json"
    assert meta_file.is_file(), f"Missing solver metadata file: {meta_file}"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))

    t_cfg = meta.get("time_config", {})
    assert t_cfg.get("t_start") == 0.0
    assert t_cfg.get("t_final") == 200.0
    assert t_cfg.get("dt") == 0.20
    assert t_cfg.get("num_steps") == 1001

    sol_stats = meta.get("solution_stats", {})
    assert sol_stats.get("num_timesteps") == 1001
    assert sol_stats.get("time_min") == 0.0
    assert sol_stats.get("time_max") == 200.0
    assert sol_stats.get("has_nan_or_inf") is False
    assert sol_stats.get("has_physical_field") is True

    s_settings = meta.get("solver_settings", {})
    assert s_settings.get("timemethod") == "bdf"
    assert s_settings.get("maxorder") == 2
    assert s_settings.get("rtol") == 0.001


def test_s1a2_diagnostics_data_contract_and_manifest():
    """Verify full data contract, timestamp alignment, and manifest token for S1-A2 diagnostics."""
    assert S1A2_DIR.is_dir(), f"Missing S1-A2 diagnostics directory: {S1A2_DIR}"
    val = validate_s1_diagnostics(
        S1A2_DIR,
        expected_steps=1001,
        expected_dt=0.20,
        expected_t_final=200.0,
        require_manifest=True,
    )
    assert val.get("valid") is True
    assert val.get("num_steps") == 1001
    assert val.get("time_range") == [0.0, 200.0]
    assert len(val.get("series", {})) == 7
    assert len(val.get("snapshots", [])) == 5
    assert val.get("manifest", {}).get("status") == "COMPLETED"


def test_s1a2_synthetic_sine_cycle_detection_correctness():
    """Test cycle extraction algorithm on known synthetic sine signal."""
    t = np.arange(0, 100.0 + 0.1, 0.2)
    # Known signal with period T = 5.0 s (f = 0.2 Hz), amplitude = 1.0
    f_known = 0.20
    t_known = 1.0 / f_known
    a_known = 1.0
    sig = a_known * np.sin(2.0 * np.pi * f_known * t)

    zc = []
    for i in range(len(t) - 1):
        if sig[i] <= 0.0 < sig[i + 1]:
            t_zero = t[i] - sig[i] * (t[i + 1] - t[i]) / (sig[i + 1] - sig[i])
            zc.append(float(t_zero))

    assert len(zc) >= 19
    periods = np.diff(zc)
    mean_t = float(np.mean(periods))
    assert abs(mean_t - t_known) < 1e-4

    # Extract amplitudes per cycle
    for k in range(len(zc) - 1):
        idx = (t >= zc[k]) & (t <= zc[k + 1])
        amp = (np.max(sig[idx]) - np.min(sig[idx])) / 2.0
        assert abs(amp - a_known) < 0.02


def test_growing_amplitude_signal_rejected_as_limit_cycle():
    """Verify that a growing amplitude signal fails the 2% relative drift criterion."""
    t = np.arange(0, 100.0 + 0.1, 0.2)
    f_known = 0.20
    # Growing amplitude from 0.1 to 0.5
    amp_profile = 0.10 + 0.004 * t
    sig = amp_profile * np.sin(2.0 * np.pi * f_known * t)

    zc = []
    for i in range(len(t) - 1):
        if sig[i] <= 0.0 < sig[i + 1]:
            t_zero = t[i] - sig[i] * (t[i + 1] - t[i]) / (sig[i + 1] - sig[i])
            zc.append(float(t_zero))

    amps = []
    for k in range(len(zc) - 1):
        idx = (t >= zc[k]) & (t <= zc[k + 1])
        amps.append((np.max(sig[idx]) - np.min(sig[idx])) / 2.0)

    last5_amps = amps[-5:]
    rel_drift = (max(last5_amps) - min(last5_amps)) / np.mean(last5_amps) * 100.0
    # Must exceed 2% threshold
    assert rel_drift > 2.0, f"Growing amplitude signal was falsely deemed stable: drift={rel_drift:.2f}%"


def test_s1a2_real_data_cycle_by_cycle_statistics_and_stability_thresholds():
    """Verify that real COMSOL S1-A2 data meets the recommended engineering stability criteria over the last 5 cycles."""
    t_cl, cl = load_comsol_1d_timeseries(S1A2_DIR / "lift_coeff.txt")[:2]
    t_cd, cd = load_comsol_1d_timeseries(S1A2_DIR / "drag_coeff.txt")[:2]
    t_wu, wu = load_comsol_1d_timeseries(S1A2_DIR / "wake_u.txt")[:2]
    t_wv, wv = load_comsol_1d_timeseries(S1A2_DIR / "wake_v.txt")[:2]

    # Find zero crossings in t >= 100s
    m_100 = t_cl >= 100.0
    t_late = t_cl[m_100]
    cl_late = cl[m_100]

    zc = []
    for i in range(len(t_late) - 1):
        if cl_late[i] <= 0.0 < cl_late[i + 1]:
            t_zero = t_late[i] - cl_late[i] * (t_late[i + 1] - t_late[i]) / (cl_late[i + 1] - cl_late[i])
            zc.append(float(t_zero))

    n_cycles = len(zc) - 1
    assert n_cycles >= 10, f"Expected >= 10 complete cycles in [100, 200]s, got {n_cycles}"

    cycle_records = []
    for k in range(n_cycles):
        t_s = zc[k]
        t_e = zc[k + 1]
        T_k = t_e - t_s
        st_k = (1.0 / T_k) * D / U_INF
        idx = (t_cl >= t_s) & (t_cl <= t_e)
        cl_amp = (np.max(cl[idx]) - np.min(cl[idx])) / 2.0
        cd_mean = np.mean(cd[idx])
        cycle_records.append({"St": st_k, "Cl_amp": cl_amp, "Cd_mean": cd_mean, "T": T_k})

    last5 = cycle_records[-5:]
    st_vals = [c["St"] for c in last5]
    cl_vals = [c["Cl_amp"] for c in last5]
    cd_vals = [c["Cd_mean"] for c in last5]

    st_drift = (max(st_vals) - min(st_vals)) / np.mean(st_vals) * 100.0
    cl_drift = (max(cl_vals) - min(cl_vals)) / np.mean(cl_vals) * 100.0
    cd_drift = (max(cd_vals) - min(cd_vals)) / np.mean(cd_vals) * 100.0

    # Verify engineering stability criteria
    assert st_drift < 1.0, f"Strouhal drift {st_drift:.3f}% exceeded 1.0% threshold"
    assert cl_drift < 2.0, f"Lift amplitude drift {cl_drift:.3f}% exceeded 2.0% threshold"
    assert cd_drift < 1.0, f"Mean drag drift {cd_drift:.3f}% exceeded 1.0% threshold"

    # Verify wake probe trend in [160, 200]s
    m_160 = t_wu >= 160.0
    poly_wu = np.polyfit(t_wu[m_160], wu[m_160], 1)
    poly_wv = np.polyfit(t_wv[m_160], wv[m_160], 1)
    assert abs(poly_wu[0]) < 1e-4, f"Wake u linear drift {poly_wu[0]:.2e} exceeded tolerance"
    assert abs(poly_wv[0]) < 1e-4, f"Wake v linear drift {poly_wv[0]:.2e} exceeded tolerance"


def test_s1a2_multi_window_statistics_consistency():
    """Verify that Window 1 [120, 160]s and Window 2 [160, 200]s show consistent limit-cycle properties."""
    t_cl, cl = load_comsol_1d_timeseries(S1A2_DIR / "lift_coeff.txt")[:2]
    t_cd, cd = load_comsol_1d_timeseries(S1A2_DIR / "drag_coeff.txt")[:2]

    m_w1 = (t_cl >= 120.0) & (t_cl <= 160.0)
    m_w2 = (t_cl >= 160.0) & (t_cl <= 200.0)

    cd_mean_w1 = np.mean(cd[m_w1])
    cd_mean_w2 = np.mean(cd[m_w2])
    cl_amp_w1 = (np.max(cl[m_w1]) - np.min(cl[m_w1])) / 2.0
    cl_amp_w2 = (np.max(cl[m_w2]) - np.min(cl[m_w2])) / 2.0

    cd_drift = abs(cd_mean_w2 - cd_mean_w1) / cd_mean_w1 * 100.0
    cl_drift = abs(cl_amp_w2 - cl_amp_w1) / cl_amp_w1 * 100.0

    assert cd_drift < 1.0, f"Window Cd drift {cd_drift:.3f}% exceeded 1.0%"
    assert cl_drift < 2.0, f"Window Cl amplitude drift {cl_drift:.3f}% exceeded 2.0%"


def test_s1a_legacy_100s_interface_unaffected():
    """Verify that validating existing S1-A 100s results remains 100% functional and backward-compatible."""
    s1a_dir = Path("runs/re100_s1a_trial")
    assert s1a_dir.is_dir()
    val = validate_s1_diagnostics(
        s1a_dir,
        expected_steps=501,
        expected_dt=0.20,
        expected_t_final=100.0,
        require_manifest=True,
    )
    assert val.get("valid") is True
    assert val.get("num_steps") == 501
    assert len(val.get("snapshots", [])) == 4
    assert val.get("manifest", {}).get("status") == "COMPLETED"
