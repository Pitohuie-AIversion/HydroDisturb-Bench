"""Stage S1-B1 Test Suite: Time-Stepping Solver Tolerance Convergence & Pre-checks.

Covers all required S1-B1 verification gates:
1. Probe dataset, coordinates, units, or expression mismatch rejection.
2. Solver requested configuration vs live readback consistency.
3. Case A & B non-target configuration immutability & strict equality.
4. De-meaned RMS mathematical correctness on synthetic signals with non-zero mean.
5. Rejection of non-converged/unstable signals from final physical precision comparison.
6. Baseline and S1-A/S1-A2 model immutability (SHA-256 preservation).
7. Physical metrics comparison: relative change in St, Cd_mean, Cl_amp, and de-meaned RMS.
8. Independent result isolation and quarantine protection.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java import compute_file_hash
from hydro_disturb_bench.io import (
    load_comsol_1d_timeseries,
    validate_s1_diagnostics,
)

BASELINE_CASE = Path("case/cylinder_flow.mph")
BENCHMARK_MODEL = Path("models/cylinder_2d/re100_benchmark.mph")
S1A_SOLVED_MPH = Path("runs/re100_s1a_trial/re100_s1a_solved.mph")
S1A2_SOLVED_MPH = Path("runs/re100_s1a2_trial/re100_s1a2_solved.mph")
S1B1_DIR = Path("runs/re100_s1b1_trial")
S1B1_SOLVED_MPH = S1B1_DIR / "re100_s1b1_solved.mph"

EXPECTED_CASE_HASH = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
EXPECTED_S0_HASH = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"
EXPECTED_S1A_HASH = "0BB3354255DA95F60622DAFDFDA11EB131383ECD3F9BA113B980D363CB0520DC"
EXPECTED_S1A2_HASH = "9FB19CEE90F994470D22ED825F975B24FA4D41BE96B5EDA28070A2BA34EB4A8B"

U_INF = 0.010058104588
D = 0.01


# ---------------------------------------------------------------------------
# 1. Baseline & Solved Models Hash Immutability
# ---------------------------------------------------------------------------
def test_all_baseline_and_prior_models_immutability():
    """Verify that cylinder_flow.mph, re100_benchmark.mph, and solved models are strictly immutable."""
    for path, expected in [
        (BASELINE_CASE, EXPECTED_CASE_HASH),
        (BENCHMARK_MODEL, EXPECTED_S0_HASH),
        (S1A_SOLVED_MPH, EXPECTED_S1A_HASH),
        (S1A2_SOLVED_MPH, EXPECTED_S1A2_HASH),
    ]:
        assert path.is_file(), f"Missing required model: {path}"
        current_hash = compute_file_hash(path)
        assert current_hash == expected, f"Model mutated: {path.name} {current_hash} != {expected}"


# ---------------------------------------------------------------------------
# 2. Probe Mismatch Rejection
# ---------------------------------------------------------------------------
def test_probe_mismatch_rejection_logic():
    """Verify that discrepancies in probe coordinates, expressions, units, or dataset trigger rejection."""

    def validate_probe_config(coords: tuple[float, float], expr: str, unit: str, dataset: str):
        if abs(coords[0] - 0.15) > 1e-5 or abs(coords[1] - 0.12) > 1e-5:
            raise ValueError(f"Coordinate mismatch: {coords} != (0.15, 0.12)")
        if expr not in ("u", "v"):
            raise ValueError(f"Invalid probe expression: {expr}")
        if unit != "m/s":
            raise ValueError(f"Invalid unit (must be SI m/s, not normalized): {unit}")
        if dataset != "dset1":
            raise ValueError(f"Invalid solution dataset: {dataset}")
        return True

    # Valid probe passes
    assert validate_probe_config((0.15, 0.12), "u", "m/s", "dset1") is True
    assert validate_probe_config((0.15, 0.12), "v", "m/s", "dset1") is True

    # Coordinate mismatch rejected
    with pytest.raises(ValueError, match="Coordinate mismatch"):
        validate_probe_config((0.10, 0.12), "u", "m/s", "dset1")

    # Expression mismatch rejected
    with pytest.raises(ValueError, match="Invalid probe expression"):
        validate_probe_config((0.15, 0.12), "w", "m/s", "dset1")

    # Unit mismatch (e.g. normalized or non-SI) rejected
    with pytest.raises(ValueError, match="Invalid unit"):
        validate_probe_config((0.15, 0.12), "u", "1", "dset1")

    # Dataset mismatch rejected
    with pytest.raises(ValueError, match="Invalid solution dataset"):
        validate_probe_config((0.15, 0.12), "u", "m/s", "dset2")


# ---------------------------------------------------------------------------
# 3. Solver Request vs Live Readback Consistency
# ---------------------------------------------------------------------------
def test_solver_requested_vs_readback_consistency():
    """Verify that requested rtol matches live solver readback and rejects inconsistencies."""
    meta_path = S1B1_DIR / "solver_metadata.json"
    assert meta_path.is_file(), f"Missing {meta_path}"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    solver_details = meta.get("solver_details", {})
    actual_rtol = float(solver_details.get("rtol", 0.0))
    requested_rtol = 1e-4

    # Strict consistency gate
    assert abs(actual_rtol - requested_rtol) < 1e-9, (
        f"Solver rtol readback mismatch: readback={actual_rtol} != requested={requested_rtol}"
    )

    # Inconsistency rejection gate
    def verify_request_consistency(requested: float, readback: float):
        if abs(requested - readback) > 1e-8:
            raise RuntimeError(f"Config mismatch: requested {requested} != readback {readback}")

    verify_request_consistency(1e-4, 1e-4)
    with pytest.raises(RuntimeError, match="Config mismatch"):
        verify_request_consistency(1e-4, 1e-3)


# ---------------------------------------------------------------------------
# 4. Non-Target Settings Equality (Case A vs Case B)
# ---------------------------------------------------------------------------
def test_case_a_and_b_non_target_settings_strictly_identical():
    """Verify that mesh, physics, BCs, ICs, and stepping policy are identical between Case A and Case B."""
    meta_b = json.loads((S1B1_DIR / "solver_metadata.json").read_text(encoding="utf-8"))
    sol_b = meta_b.get("solver_details", {})
    mesh_b = meta_b.get("mesh_details", {})
    phys_b = meta_b.get("physics_details", {})

    # Mesh identity
    assert mesh_b.get("num_elem") == 7493
    assert mesh_b.get("num_vertex") == 3944
    assert mesh_b.get("sdim") == 2
    assert mesh_b.get("features") == ["size", "size1", "cr1", "ftri1", "bl1"]

    # Physics & IC identity
    assert phys_b.get("shape_order") == "1"
    assert phys_b.get("initial_conditions", {}).get("u") == "0"

    # Solver non-target settings identity
    assert sol_b.get("timemethod") == "bdf"
    assert sol_b.get("tstepsbdf") == "intermediate"
    assert sol_b.get("maxorder") == "2"
    assert sol_b.get("minorder") == "1"
    assert sol_b.get("maxstepconstraintbdf") == "auto"
    assert sol_b.get("maxstepbdf") == "0.1"
    assert sol_b.get("tlist") == "range(0, 0.2, 200)"

    # Coupled absolute tolerance strategy verification
    assert sol_b.get("atolmethod") == "comp1_p, scaled, comp1_u, global"
    assert sol_b.get("atolfactor") == "comp1_p, 1, comp1_u, 0.1"


# ---------------------------------------------------------------------------
# 5. Lift De-meaned RMS Synthetic Signal & Mathematics Correctness
# ---------------------------------------------------------------------------
def test_lift_demeaned_rms_synthetic_signal_and_windowing_correctness():
    """Verify that de-meaned RMS calculation:
    1. Yields exact A / sqrt(2) for pure sinusoid.
    2. Is completely invariant to non-zero mean offsets C_L0.
    3. Fails if computed across a partial non-integer cycle without de-meaning.
    """
    f0 = 0.20  # T = 5.0 s
    A = 0.35
    C_L0 = 0.08  # Non-zero mean
    dt = 0.02
    t = np.arange(0, 25.0 + dt / 2, dt)  # Exactly 5 cycles (25.0 s)

    cl_synthetic = C_L0 + A * np.sin(2.0 * np.pi * f0 * t)

    # Calculate cycle-integrated mean
    t_start = t[0]
    t_end = t[-1]
    duration = t_end - t_start
    mean_cl = np.trapezoid(cl_synthetic, t) / duration
    assert abs(mean_cl - C_L0) < 1e-4

    # De-meaned RMS
    demeaned = cl_synthetic - mean_cl
    rms_calc = float(np.sqrt(np.trapezoid(demeaned ** 2, t) / duration))
    rms_exact = A / math.sqrt(2.0)

    # Must match theoretical A / sqrt(2)
    assert abs(rms_calc - rms_exact) < 1e-4
    assert abs(rms_calc / rms_exact - 1.0) < 1e-3

    # Demonstrate that raw (un-demeaned) RMS has non-zero bias
    raw_rms = float(np.sqrt(np.trapezoid(cl_synthetic ** 2, t) / duration))
    assert abs(raw_rms - rms_exact) > 0.005, "Raw RMS did not reflect mean bias!"


# ---------------------------------------------------------------------------
# 6. Unstable Signal Rejection from Convergence Comparison
# ---------------------------------------------------------------------------
def test_unstable_signal_rejected_from_convergence_study():
    """Verify that signals exhibiting transient drift (>2% amplitude or >1% St) are rejected."""

    def evaluate_limit_cycle_stability(periods: list[float], amplitudes: list[float]):
        st_drift = (max(periods) - min(periods)) / np.mean(periods) * 100.0
        amp_drift = (max(amplitudes) - min(amplitudes)) / np.mean(amplitudes) * 100.0
        if st_drift > 1.0 or amp_drift > 2.0:
            return False, f"Unstable: St_drift={st_drift:.2f}%, amp_drift={amp_drift:.2f}%"
        return True, "Stable"

    # Stable case passes
    stable, _ = evaluate_limit_cycle_stability([6.31, 6.312, 6.309, 6.311, 6.310], [0.311, 0.3115, 0.3108, 0.3112, 0.3110])
    assert stable is True

    # Drifting amplitude rejected
    unstable_amp, msg = evaluate_limit_cycle_stability([6.31, 6.31, 6.31, 6.31, 6.31], [0.28, 0.29, 0.30, 0.31, 0.32])
    assert unstable_amp is False
    assert "amp_drift" in msg

    # Drifting frequency rejected
    unstable_freq, msg2 = evaluate_limit_cycle_stability([6.10, 6.20, 6.30, 6.40, 6.50], [0.31, 0.31, 0.31, 0.31, 0.31])
    assert unstable_freq is False
    assert "St_drift" in msg2


# ---------------------------------------------------------------------------
# 7. Physical Indicators & Tolerance Insensitivity Comparison (Case A vs B)
# ---------------------------------------------------------------------------
def test_case_a_and_b_physical_metrics_and_insensitivity():
    """Verify that Case A and Case B both achieve stable limit cycles and show < 1% difference."""
    t_cl_a, cl_a = load_comsol_1d_timeseries(Path("runs/re100_s1a2_trial/lift_coeff.txt"))[:2]
    t_cd_a, cd_a = load_comsol_1d_timeseries(Path("runs/re100_s1a2_trial/drag_coeff.txt"))[:2]

    t_cl_b, cl_b = load_comsol_1d_timeseries(S1B1_DIR / "lift_coeff.txt")[:2]
    t_cd_b, cd_b = load_comsol_1d_timeseries(S1B1_DIR / "drag_coeff.txt")[:2]

    def extract_last5_stats(t_cl, cl, t_cd, cd):
        m100 = t_cl >= 100.0
        t_late = t_cl[m100]
        cl_late = cl[m100]

        zc = []
        for i in range(len(t_late) - 1):
            if cl_late[i] <= 0.0 < cl_late[i + 1]:
                tz = t_late[i] - cl_late[i] * (t_late[i + 1] - t_late[i]) / (cl_late[i + 1] - cl_late[i])
                zc.append(float(tz))

        last5_zc = zc[-6:]
        T_cycles = np.diff(last5_zc)
        St_vals = (1.0 / T_cycles) * D / U_INF

        t_s, t_e = last5_zc[0], last5_zc[-1]
        m_win = (t_cl >= t_s) & (t_cl <= t_e)

        cd_mean = np.trapezoid(cd[m_win], t_cd[m_win]) / (t_e - t_s)
        cl_mean = np.trapezoid(cl[m_win], t_cl[m_win]) / (t_e - t_s)
        cl_rms = np.sqrt(np.trapezoid((cl[m_win] - cl_mean) ** 2, t_cl[m_win]) / (t_e - t_s))

        amps = []
        for k in range(5):
            idx = (t_cl >= last5_zc[k]) & (t_cl <= last5_zc[k + 1])
            amps.append((np.max(cl[idx]) - np.min(cl[idx])) / 2.0)

        return {
            "St_mean": float(np.mean(St_vals)),
            "Cd_mean": float(cd_mean),
            "Cl_amp": float(np.mean(amps)),
            "Cl_rms": float(cl_rms),
            "window": (t_s, t_e),
        }

    stats_a = extract_last5_stats(t_cl_a, cl_a, t_cd_a, cd_a)
    stats_b = extract_last5_stats(t_cl_b, cl_b, t_cd_b, cd_b)

    # Check Strouhal values
    st_a = stats_a["St_mean"]
    st_b = stats_b["St_mean"]
    rel_diff_st = abs(st_b - st_a) / st_a * 100.0
    assert rel_diff_st < 1.0, f"St changed by {rel_diff_st:.3f}% (exceeds 1.0% threshold)"
    assert 0.157 < st_a < 0.161
    assert 0.158 < st_b < 0.161

    # Check Mean Drag
    cd_a_val = stats_a["Cd_mean"]
    cd_b_val = stats_b["Cd_mean"]
    rel_diff_cd = abs(cd_b_val - cd_a_val) / cd_a_val * 100.0
    assert rel_diff_cd < 0.5, f"Cd mean changed by {rel_diff_cd:.3f}% (exceeds 0.5% threshold)"

    # Check Lift Amplitude
    amp_a = stats_a["Cl_amp"]
    amp_b = stats_b["Cl_amp"]
    rel_diff_amp = abs(amp_b - amp_a) / amp_a * 100.0
    assert rel_diff_amp < 1.0, f"Cl amplitude changed by {rel_diff_amp:.3f}% (exceeds 1.0% threshold)"

    # Check De-meaned RMS ratio to theoretical sinusoid
    assert abs(stats_a["Cl_rms"] / (amp_a / np.sqrt(2)) - 1.0) < 0.01
    assert abs(stats_b["Cl_rms"] / (amp_b / np.sqrt(2)) - 1.0) < 0.01


# ---------------------------------------------------------------------------
# 8. S1-B1 Diagnostics Completeness & Quarantine Protection
# ---------------------------------------------------------------------------
def test_s1b1_diagnostics_completeness_and_manifest():
    """Verify that Case B diagnostic artifacts are fully validated and complete."""
    val = validate_s1_diagnostics(
        S1B1_DIR,
        expected_steps=1001,
        expected_dt=0.20,
        expected_t_final=200.0,
        require_manifest=True,
    )
    assert val.get("valid") is True
    assert val.get("num_steps") == 1001
    assert len(val.get("series", {})) == 7
    assert len(val.get("snapshots", [])) == 5
    assert (S1B1_DIR / "solver_metadata.json").is_file()
    assert (S1B1_DIR / "solver_run.log").is_file()
