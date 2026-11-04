"""Stage S1-B1 Test Suite: Time-Stepping Solver Tolerance Convergence & Pre-checks.

Covers all required S1-B1 verification gates:
1. Probe dataset, coordinates, units, or expression mismatch rejection using production functions.
2. Solver requested configuration vs live readback consistency.
3. Case A & B non-target configuration immutability & strict equality.
4. Exact window boundary interpolation on constant, linear, and sinusoidal signals.
5. De-meaned RMS mathematical correctness on synthetic signals with non-zero mean and non-grid endpoints.
6. Rejection of non-converged/unstable signals from final physical precision comparison (UNSTABLE gate).
7. Baseline and S1-A/S1-A2 model immutability (SHA-256 preservation).
8. Physical metrics comparison using production analyze_case and compare_cases on Case A vs Case B.
9. Independent result isolation and quarantine protection.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
SCRIPTS_DIR = ROOT_DIR / "scripts"

for p in (str(SRC_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from hydro_disturb_bench.comsol_java import compute_file_hash
from hydro_disturb_bench.io import (
    load_comsol_1d_timeseries,
    validate_s1_diagnostics,
)
from scripts.verify_and_compare_s1b1 import (
    analyze_case,
    compare_cases,
    compute_cycle_half_amplitude,
    compute_demeaned_rms,
    evaluate_limit_cycle_stability,
    integrate_exact_window,
)
from scripts.verify_s1b1_prechecks import (
    validate_probe_config,
    verify_probe_metadata,
)

BASELINE_CASE = ROOT_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = ROOT_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
S1A_SOLVED_MPH = ROOT_DIR / "runs" / "re100_s1a_trial" / "re100_s1a_solved.mph"
S1A2_SOLVED_MPH = ROOT_DIR / "runs" / "re100_s1a2_trial" / "re100_s1a2_solved.mph"
S1B1_DIR = ROOT_DIR / "runs" / "re100_s1b1_trial"
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
# 2. Probe Mismatch Rejection (Using Production Functions)
# ---------------------------------------------------------------------------
def test_probe_mismatch_rejection_logic():
    """Verify that discrepancies in probe coordinates, expressions, units, or dataset trigger rejection."""
    # Valid probe passes via production validate_probe_config
    assert validate_probe_config((0.15, 0.12), "u", "m/s", "dset1") is True
    assert validate_probe_config((0.15, 0.12), ["u", "v"], "m/s", "dset1") is True

    # Coordinate mismatch rejected
    with pytest.raises(ValueError, match="Coordinate mismatch"):
        validate_probe_config((0.10, 0.12), "u", "m/s", "dset1")

    with pytest.raises(ValueError, match="Coordinate mismatch"):
        validate_probe_config((0.15, 0.10), "u", "m/s", "dset1")

    # Expression mismatch rejected
    with pytest.raises(ValueError, match="Invalid probe expression"):
        validate_probe_config((0.15, 0.12), "w", "m/s", "dset1")

    # Unit mismatch (e.g. normalized or non-SI) rejected
    with pytest.raises(ValueError, match="Invalid unit"):
        validate_probe_config((0.15, 0.12), "u", "1", "dset1")

    with pytest.raises(ValueError, match="Invalid unit"):
        validate_probe_config((0.15, 0.12), "u", "cm/s", "dset1")

    # Dataset mismatch rejected
    with pytest.raises(ValueError, match="Invalid solution dataset"):
        validate_probe_config((0.15, 0.12), "u", "m/s", "dset2")


def test_verify_probe_metadata_production_validation():
    """Verify production verify_probe_metadata validates authentic metadata and rejects corrupted readbacks."""
    t_fake = np.linspace(0.0, 200.0, 1001)
    u_fake = np.full(1001, 0.0065)
    v_fake = np.full(1001, 0.0015)

    valid_meta_u = {"column_header": "% 时间 (s)                  速度场，x 分量 (m/s)"}
    valid_meta_v = {"column_header": "% 时间 (s)                  速度场，y 分量 (m/s)"}

    valid_res = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "probe_units": {"u": "m/s", "v": "m/s"},
        "probe_unit_source": "comsol_model_export_node",
        "direct_evaluations": [
            {"target_t": 180.0, "actual_t": 180.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
            {"target_t": 200.0, "actual_t": 200.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
        ],
    }

    report = verify_probe_metadata(valid_res, valid_meta_u, valid_meta_v, t_fake, t_fake, u_fake, v_fake)
    assert report["is_valid"] is True
    assert report["status"] == "PASS"
    assert report["unit"] == "m/s"
    assert report["unit_source"] == "header"
    assert report["raw_metadata_verified"] is True
    assert report["time_alignment_verified"] is True

    # Mismatched coordinates in COMSOL readback
    invalid_coords_res = dict(valid_res, probe_coords={"x": "0.14", "y": "0.12"})
    bad_report = verify_probe_metadata(invalid_coords_res, valid_meta_u, valid_meta_v, t_fake, t_fake, u_fake, v_fake)
    assert bad_report["is_valid"] is False
    assert bad_report["status"] == "FAIL"

    # Missing dset1 dataset
    invalid_dset_res = dict(valid_res, datasets=[{"tag": "dset2", "type": "Solution"}])
    bad_report2 = verify_probe_metadata(invalid_dset_res, valid_meta_u, valid_meta_v, t_fake, t_fake, u_fake, v_fake)
    assert bad_report2["is_valid"] is False
    assert bad_report2["status"] == "FAIL"


def test_verify_probe_metadata_rejects_actual_metadata_errors():
    """Verify that verify_probe_metadata inspects and rejects corrupted/missing raw metadata."""
    t_fake = np.linspace(0.0, 200.0, 1001)
    u_fake = np.full(1001, 0.0065)
    v_fake = np.full(1001, 0.0015)
    valid_res = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "probe_units": {"u": "m/s", "v": "m/s"},
        "probe_unit_source": "comsol_model_export_node",
        "direct_evaluations": [
            {"target_t": 180.0, "actual_t": 180.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
        ],
    }

    # 1. Missing header in meta_u
    rep_missing = verify_probe_metadata(valid_res, {}, {"column_header": "% 时间 (s)  速度场，y 分量"}, t_fake, t_fake, u_fake, v_fake)
    assert rep_missing["is_valid"] is False
    assert any("meta_u" in e for e in rep_missing["errors"])

    # 2. Wrong component expression (e.g. pressure instead of velocity u)
    bad_meta_u = {"column_header": "% 时间 (s)                  压力 p"}
    rep_wrong_expr = verify_probe_metadata(valid_res, bad_meta_u, {"column_header": "% 时间 (s)  速度场，y 分量"}, t_fake, t_fake, u_fake, v_fake)
    assert rep_wrong_expr["is_valid"] is False
    assert any("Invalid velocity expression" in e or "unknown_u_expr" in e for e in rep_wrong_expr["errors"])

    # 3. Normalized dimensionless unit instead of SI m/s
    bad_unit_meta = {"column_header": "% 时间 (s)                  速度场，x 分量 无量纲", "unit": "1"}
    rep_wrong_unit = verify_probe_metadata(valid_res, bad_unit_meta, {"column_header": "% 时间 (s)  速度场，y 分量"}, t_fake, t_fake, u_fake, v_fake)
    assert rep_wrong_unit["is_valid"] is False
    assert any("Invalid velocity unit" in e or "Invalid unit" in e for e in rep_wrong_unit["errors"])


def test_verify_probe_metadata_rejects_actual_time_mismatches():
    """Verify that verify_probe_metadata rejects discrepancies between COMSOL actual_t, target_t, and series times."""
    t_fake = np.linspace(0.0, 200.0, 1001)
    u_fake = np.full(1001, 0.0065)
    v_fake = np.full(1001, 0.0015)
    meta_u = {"column_header": "% 时间 (s)  速度场，x 分量"}
    meta_v = {"column_header": "% 时间 (s)  速度场，y 分量"}

    # 1. COMSOL actual_t differs from target_t
    mismatched_actual_t_res = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "probe_units": {"u": "m/s", "v": "m/s"},
        "direct_evaluations": [
            {"target_t": 180.0, "actual_t": 182.5, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
        ],
    }
    rep_time_drift = verify_probe_metadata(mismatched_actual_t_res, meta_u, meta_v, t_fake, t_fake, u_fake, v_fake)
    assert rep_time_drift["is_valid"] is False
    assert any("COMSOL actual_t mismatch" in e for e in rep_time_drift["errors"])

    # 2. Time series does not align with target_t (e.g. t_u array shifted by 5.0 s)
    t_shifted = t_fake + 5.0
    valid_res = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "probe_units": {"u": "m/s", "v": "m/s"},
        "direct_evaluations": [
            {"target_t": 180.0, "actual_t": 180.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
        ],
    }
    rep_txt_shift = verify_probe_metadata(valid_res, meta_u, meta_v, t_shifted, t_shifted, u_fake, v_fake)
    assert rep_txt_shift["is_valid"] is False
    assert any("Time range mismatch" in e or "TXT t_u mismatch" in e for e in rep_txt_shift["errors"])


def test_verify_probe_metadata_rejects_unspecified_units_and_missing_actual_t():
    """Verify that verify_probe_metadata strictly rejects missing velocity units, non-SI units, wrong time units, and missing actual_t.

    Directly verifies the offline reviewer counterexample suite:
    1. Velocity unit not specified: header only '% 时间 (s)  u', no unit field, no res unit -> FAIL
    2. Non-SI velocity unit: header 'u (cm/s)', 'v (cm/s)' (no unit field) -> FAIL
    3. Dimensionless unit: header 'u (1)', 'v (1)' (no unit field) -> FAIL
    4. Time unit mismatch: header 'Time (ms)' -> FAIL
    5. Omitted actual_t in COMSOL evaluations: item missing 'actual_t' -> FAIL
    """
    t_fake = np.linspace(0.0, 200.0, 1001)
    u_fake = np.full(1001, 0.0065)
    v_fake = np.full(1001, 0.0015)

    base_res = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "direct_evaluations": [
            {"target_t": 180.0, "actual_t": 180.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},
        ],
    }

    # 1. Velocity unit not specified (neither in header nor in res)
    meta_u_nounit = {"column_header": "% 时间 (s)  速度场，x 分量"}
    meta_v_nounit = {"column_header": "% 时间 (s)  速度场，y 分量"}
    rep_no_unit = verify_probe_metadata(base_res, meta_u_nounit, meta_v_nounit, t_fake, t_fake, u_fake, v_fake)
    assert rep_no_unit["is_valid"] is False
    assert any("Velocity unit evidence missing" in e for e in rep_no_unit["errors"])
    assert rep_no_unit["unit"] == "unknown"

    # 2. Non-SI velocity unit in header: u (cm/s), v (cm/s) (no unit field)
    meta_u_cm = {"column_header": "% 时间 (s)  u (cm/s)"}
    meta_v_cm = {"column_header": "% 时间 (s)  v (cm/s)"}
    rep_cm = verify_probe_metadata(base_res, meta_u_cm, meta_v_cm, t_fake, t_fake, u_fake, v_fake)
    assert rep_cm["is_valid"] is False
    assert any("Invalid velocity unit" in e and "cm/s" in e for e in rep_cm["errors"])

    # 3. Dimensionless unit in header: u (1), v (1) (no unit field)
    meta_u_one = {"column_header": "% 时间 (s)  u (1)"}
    meta_v_one = {"column_header": "% 时间 (s)  v (1)"}
    rep_one = verify_probe_metadata(base_res, meta_u_one, meta_v_one, t_fake, t_fake, u_fake, v_fake)
    assert rep_one["is_valid"] is False
    assert any("Invalid velocity unit" in e and "1" in e for e in rep_one["errors"])

    # 4. Time unit mismatch in header: Time (ms)
    meta_u_ms = {"column_header": "% Time (ms)  u (m/s)"}
    meta_v_ms = {"column_header": "% Time (ms)  v (m/s)"}
    rep_ms = verify_probe_metadata(base_res, meta_u_ms, meta_v_ms, t_fake, t_fake, u_fake, v_fake)
    assert rep_ms["is_valid"] is False
    assert any("Invalid time unit" in e and "ms" in e for e in rep_ms["errors"])

    # 5. Missing actual_t in COMSOL evaluations: item has target_t but no actual_t
    res_no_actual_t = {
        "probe_coords": {"x": "0.15", "y": "0.12"},
        "datasets": [{"tag": "dset1", "type": "Solution"}],
        "probe_units": {"u": "m/s", "v": "m/s"},
        "direct_evaluations": [
            {"target_t": 180.0, "point_x": 0.15, "point_y": 0.12, "u_direct": 0.0065, "v_direct": 0.0015},  # no actual_t
        ],
    }
    meta_u_ok = {"column_header": "% 时间 (s)  u (m/s)"}
    meta_v_ok = {"column_header": "% 时间 (s)  v (m/s)"}
    rep_no_actual_t = verify_probe_metadata(res_no_actual_t, meta_u_ok, meta_v_ok, t_fake, t_fake, u_fake, v_fake)
    assert rep_no_actual_t["is_valid"] is False
    assert any("Missing actual_t" in e for e in rep_no_actual_t["errors"])


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
# 5. Core Mathematical Integrals: Constant, Linear, and Non-Grid Endpoints
# ---------------------------------------------------------------------------
def test_exact_window_integration_constant_signal_arbitrary_window():
    """Verify that integrate_exact_window preserves exact mean for constant signals on arbitrary non-grid windows.

    This directly verifies the fix for the P1 bug where omitting boundary intervals
    caused a -0.53% error (0.9947 instead of 1.0) on discrete grids.
    """
    dt = 0.2
    t = np.arange(0.0, 200.0 + dt / 2, dt)
    c_val = 3.141592653589793
    y_const = np.full_like(t, c_val)

    # Windows with arbitrary non-grid endpoints (e.g. Case A and Case B zero-crossing times)
    test_windows = [
        (163.576, 194.942),  # Case A window
        (163.682, 195.048),  # Case B window
        (10.12345, 87.65432),  # Arbitrary window
        (0.05, 199.95),  # Near boundaries
    ]

    for t_s, t_e in test_windows:
        duration = t_e - t_s
        integral_val, mean_calc = integrate_exact_window(t, y_const, t_s, t_e)
        # Must match constant value to machine precision
        assert abs(mean_calc - c_val) < 1e-12, f"Constant mean failed on [{t_s}, {t_e}]: {mean_calc} != {c_val}"
        assert abs(integral_val - c_val * duration) < 1e-12

    # Specific check on Case A window verifying user's exact mathematical counter-example (-0.529%)
    t_s_a, t_e_a = 163.576, 194.942
    dur_a = t_e_a - t_s_a
    m_win_a = (t >= t_s_a) & (t <= t_e_a)
    naive_mean_a = np.trapezoid(y_const[m_win_a], t[m_win_a]) / dur_a
    naive_error_a = (naive_mean_a - c_val) / c_val
    # Demonstrates that naive masking yields -0.529% bias on constant signal
    assert abs(naive_error_a - (-0.005292)) < 1e-5


def test_exact_window_integration_linear_signal_arbitrary_endpoints():
    """Verify that integrate_exact_window yields exact analytical integral for linear signals."""
    dt = 0.2
    t = np.arange(0.0, 200.0 + dt / 2, dt)
    slope = 0.042
    intercept = -1.25
    y_linear = slope * t + intercept

    t_s = 163.576
    t_e = 194.942
    duration = t_e - t_s

    # Analytical integral of (a*t + b) is a*(t_e^2 - t_s^2)/2 + b*(t_e - t_s)
    exact_integral = 0.5 * slope * (t_e ** 2 - t_s ** 2) + intercept * (t_e - t_s)
    exact_mean = exact_integral / duration

    calc_integral, calc_mean = integrate_exact_window(t, y_linear, t_s, t_e)

    assert abs(calc_integral - exact_integral) < 1e-11
    assert abs(calc_mean - exact_mean) < 1e-11


def test_demeaned_rms_synthetic_sine_non_zero_mean_and_arbitrary_phase():
    """Verify that compute_demeaned_rms:
    1. Yields exact A / sqrt(2) for a sinusoid spanning integer periods with arbitrary non-grid phase.
    2. Is invariant to non-zero constant bias C_0.
    3. Production function behaves correctly on non-grid window endpoints.
    """
    f0 = 0.16  # T0 = 6.25 s
    T0 = 1.0 / f0
    A = 0.312
    C0 = 0.145  # Non-zero offset
    dt = 0.20
    t = np.arange(0.0, 200.0 + dt / 2, dt)

    phi0 = 1.2345  # Arbitrary non-zero phase
    y = C0 + A * np.sin(2.0 * np.pi * f0 * t + phi0)

    # Start at arbitrary non-grid time t_start, span exactly 5 full cycles
    t_start = 123.456
    t_end = t_start + 5 * T0  # exactly 5 cycles

    mean_val, rms_calc = compute_demeaned_rms(t, y, t_start, t_end)
    rms_exact = A / math.sqrt(2.0)

    # Error should be minuscule (within trapezoid discretization error for ~31 points per cycle)
    rel_err = abs(rms_calc - rms_exact) / rms_exact
    assert rel_err < 0.001, f"De-meaned RMS rel_err {rel_err:.4e} exceeds 0.1%"
    assert abs(mean_val - C0) < 1e-3

    # Half amplitude check
    half_amp = compute_cycle_half_amplitude(t, y, t_start, t_end)
    assert abs(half_amp - A) / A < 0.005


# ---------------------------------------------------------------------------
# 6. Unstable Signal Rejection Gate (Production Control Flow)
# ---------------------------------------------------------------------------
def test_production_evaluate_limit_cycle_stability():
    """Verify that production evaluate_limit_cycle_stability rejects drifting periods, amplitudes, or Cd."""
    # 1. Stable passes
    periods_stable = [6.310, 6.312, 6.309, 6.311, 6.310]
    amps_stable = [0.3110, 0.3115, 0.3108, 0.3112, 0.3110]
    cds_stable = [1.3467, 1.3468, 1.3466, 1.3467, 1.3468]

    is_stable, drifts, msg = evaluate_limit_cycle_stability(periods_stable, amps_stable, cds_stable)
    assert is_stable is True
    assert msg == "Stable"
    assert drifts["st_drift_pct"] < 1.0
    assert drifts["amp_drift_pct"] < 2.0
    assert drifts["cd_drift_pct"] < 1.0

    # 2. Drifting amplitude rejected
    amps_unstable = [0.28, 0.29, 0.30, 0.31, 0.32]  # ~13% drift
    is_stable_amp, drifts_amp, msg_amp = evaluate_limit_cycle_stability(periods_stable, amps_unstable, cds_stable)
    assert is_stable_amp is False
    assert "amp drift" in msg_amp.lower()

    # 3. Drifting frequency rejected
    periods_unstable = [6.10, 6.20, 6.30, 6.40, 6.50]  # ~6.3% drift
    is_stable_per, drifts_per, msg_per = evaluate_limit_cycle_stability(periods_unstable, amps_stable, cds_stable)
    assert is_stable_per is False
    assert "st drift" in msg_per.lower()


def test_analyze_case_and_compare_cases_reject_unstable_signals():
    """Verify that analyze_case and compare_cases reject unstable signals from precision comparison.

    Ensures that if either case is UNSTABLE, compare_cases returns conclusion='UNSTABLE'
    and can_compare=False, preventing false 'INSENSITIVE' claims.
    """
    dt = 0.2
    t = np.arange(0.0, 200.0 + dt / 2, dt)
    f0 = 0.16  # T0 = 6.25 s
    omega = 2.0 * np.pi * f0

    # Stable Case A
    cl_stable = 0.311 * np.sin(omega * t)
    cd_stable = 1.346 + 0.05 * np.cos(2.0 * omega * t)

    stats_stable_a = analyze_case(
        name="Case_A_Synthetic",
        t_cl=t,
        cl=cl_stable,
        t_cd=t,
        cd=cd_stable,
    )
    assert stats_stable_a["status"] == "STABLE"
    assert stats_stable_a["is_stable"] is True

    # Unstable Case B (growing amplitude: 0.20 -> 0.40 over time)
    amp_envelope = 0.20 + 0.20 * (t / 200.0)
    cl_unstable = amp_envelope * np.sin(omega * t)
    cd_unstable = 1.346 + 0.05 * np.cos(2.0 * omega * t)

    stats_unstable_b = analyze_case(
        name="Case_B_Unstable",
        t_cl=t,
        cl=cl_unstable,
        t_cd=t,
        cd=cd_unstable,
    )
    assert stats_unstable_b["status"] == "UNSTABLE"
    assert stats_unstable_b["is_stable"] is False

    # Comparison MUST reject unstable signal
    comp_unstable = compare_cases(stats_stable_a, stats_unstable_b)
    assert comp_unstable["can_compare"] is False
    assert comp_unstable["conclusion"] == "UNSTABLE"
    assert any("Case_B_Unstable" in item for item in comp_unstable.get("unstable_details", []))

    # Must NOT conclude insensitive!
    assert comp_unstable["conclusion"] != "INSENSITIVE_WITHIN_TESTED_RANGE"
    assert comp_unstable["conclusion"] != "SENSITIVE"


def test_compare_cases_sensitive_when_lift_amplitude_or_rms_exceeds_threshold():
    """Verify that compare_cases flags SENSITIVE when St & Cd are identical but lift amplitude / RMS drift exceeds threshold.

    Directly addresses user finding: both cases stable, St & Cd 0% difference,
    but Case B has 20% higher lift amplitude and RMS. Must return conclusion='SENSITIVE'.
    """
    dt = 0.2
    t = np.arange(0.0, 200.0 + dt / 2, dt)
    f0 = 0.16  # T0 = 6.25 s
    omega = 2.0 * np.pi * f0

    # Base Case A: stable amplitude A = 0.30
    cl_a = 0.30 * np.sin(omega * t)
    cd_a = 1.346 + 0.05 * np.cos(2.0 * omega * t)

    # Modified Case B: St and Cd are IDENTICAL, but lift amplitude is 20% higher (A = 0.36)
    cl_b = 0.36 * np.sin(omega * t)
    cd_b = 1.346 + 0.05 * np.cos(2.0 * omega * t)

    stats_a = analyze_case(name="Case_A_Ref", t_cl=t, cl=cl_a, t_cd=t, cd=cd_a)
    stats_b = analyze_case(name="Case_B_AmpBoost", t_cl=t, cl=cl_b, t_cd=t, cd=cd_b)

    assert stats_a["is_stable"] is True
    assert stats_b["is_stable"] is True

    # St and Cd difference should be negligible (~0%)
    assert abs(stats_a["St_zc_mean"] - stats_b["St_zc_mean"]) < 1e-6
    assert abs(stats_a["Cd_mean"] - stats_b["Cd_mean"]) < 1e-6

    # Compare with default 1.0% tolerances on all 4 metrics
    comp = compare_cases(
        stats_a,
        stats_b,
        st_tol_pct=1.0,
        cd_tol_pct=1.0,
        amp_tol_pct=1.0,
        rms_tol_pct=1.0,
    )

    assert comp["can_compare"] is True
    assert comp["conclusion"] == "SENSITIVE"
    assert comp["conclusion"] != "INSENSITIVE_WITHIN_TESTED_RANGE"

    # Verify sensitive reasons capture lift amplitude and RMS
    reasons_str = " ".join(comp["sensitive_reasons"])
    assert "Lift amp change" in reasons_str
    assert "Lift RMS change" in reasons_str
    assert "+20.000%" in reasons_str or "+20.0" in reasons_str


# ---------------------------------------------------------------------------
# 7. Physical Indicators & Tolerance Insensitivity (Case A vs B Production Data)
# ---------------------------------------------------------------------------
def test_case_a_and_b_real_cfd_production_analysis():
    """Verify Case A and Case B real CFD runs using production analyze_case and compare_cases."""
    case_a_dir = ROOT_DIR / "runs" / "re100_s1a2_trial"
    case_b_dir = ROOT_DIR / "runs" / "re100_s1b1_trial"

    stats_a = analyze_case(
        name="Case_A",
        trial_dir=case_a_dir,
    )

    stats_b = analyze_case(
        name="Case_B",
        trial_dir=case_b_dir,
    )

    # 1. Both cases must independently pass limit-cycle stability gate
    assert stats_a["status"] == "STABLE", f"Case A unstable: {stats_a['stability_drifts']}"
    assert stats_b["status"] == "STABLE", f"Case B unstable: {stats_b['stability_drifts']}"
    assert stats_a["is_stable"] is True
    assert stats_b["is_stable"] is True

    # 2. Check exact window boundaries
    assert stats_a["window"][0] < 163.6 and stats_a["window"][0] > 163.5
    assert stats_a["window"][1] < 195.0 and stats_a["window"][1] > 194.9

    # 3. Compare cases with production compare_cases
    comp = compare_cases(stats_a, stats_b)
    assert comp["can_compare"] is True
    assert comp["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE"

    # 4. Quantitative verification of corrected metrics
    diffs = comp["comparisons"]

    # Strouhal number (zero crossing): ~0.1585 vs ~0.1594 (+0.59%)
    st_a = stats_a["St_zc_mean"]
    st_b = stats_b["St_zc_mean"]
    assert 0.157 < st_a < 0.160
    assert 0.158 < st_b < 0.161
    assert abs(diffs["St_zc"]["rel_diff_pct"] - 0.59) < 0.1

    # Mean Drag: Case A ~1.3468, Case B ~1.3470 (+0.0165%)
    cd_a = stats_a["Cd_mean"]
    cd_b = stats_b["Cd_mean"]
    assert 1.346 < cd_a < 1.348
    assert 1.346 < cd_b < 1.348
    assert abs(diffs["Cd_mean"]["rel_diff_pct"] - 0.0165) < 0.01

    # Lift amplitude & RMS: ~0.55% and ~0.53% change
    assert abs(diffs["Cl_amp"]["rel_diff_pct"] - 0.56) < 0.15
    assert abs(diffs["Cl_rms"]["rel_diff_pct"] - 0.53) < 0.15

    # Pure limit-cycle sinusoidal ratio check: RMS / (C_L' / sqrt(2)) ~ 1.001
    assert abs(stats_a["sinusoidal_ratio"] - 1.0) < 0.005
    assert abs(stats_b["sinusoidal_ratio"] - 1.0) < 0.005


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


@pytest.mark.parametrize("scenario", ["missing", "cm/s", "m/s", "read_failure", "wrong_file", "wrong_dataset", "ft/s_header", "unbound_label", "feature_override"])
def test_probe_run_prechecks_unit_provenance_entry(monkeypatch, tmp_path, scenario):
    """Fault injection goes through the real entry, real TXT loader and binding gate."""
    from scripts import verify_s1b1_prechecks as entry
    model = tmp_path / "solved.mph"
    model.write_bytes(b"fixture model identity")
    def write_series(file, component):
        value = 0.0065 if component == "u" else 0.0015
        file.write_text("% Time (s)  " + component + "\n" +
                        "\n".join(f"{t:.1f} {value}" for t in np.linspace(0, 200, 1001)), encoding="utf-8")
    originals = {}
    for c in ("u", "v"):
        originals[c] = tmp_path / f"old_{c}.txt"
        write_series(originals[c], c)
    if scenario == "ft/s_header":
        content = originals["u"].read_text(encoding="utf-8")
        originals["u"].write_text(content.replace("  u\n", "  u (ft/s)\n"), encoding="utf-8")
    upstream = {}
    def inspect(model_path, *, probe_x, probe_y, probe_output_dir):
        if scenario == "read_failure":
            raise RuntimeError("actual COMSOL read failed")
        probe_output_dir.mkdir()
        upstream.update({"probe_coords": {"x": probe_x, "y": probe_y},
            "datasets": [{"tag": "dset1", "type": "Solution"}],
            "probe_exports": [],
            "direct_evaluations": [{"target_t": 180., "actual_t": 180.,
                "point_x": .15, "point_y": .12, "u_direct": .0065, "v_direct": .0015}]})
        for c in ("u", "v"):
            file = probe_output_dir / f"wake_{c}.txt"
            write_series(file, c)
            if scenario not in ("missing", "unbound_label"):
                upstream["probe_exports"].append({"component": c,
                    "model_path": str(model_path), "model_tag": "Model",
                    "model_sha256": compute_file_hash(model), "filename": str(file),
                    "file_sha256": compute_file_hash(file), "export_tag": f"exp_{c}",
                    "plotgroup_tag": f"pg_{c}", "feature_tag": "pt", "feature_tags": ["pt"], "feature_data": "parent",
                    "expression": c, "unit": "cm/s" if scenario == "cm/s" else "m/s",
                    "dataset_tag": "cpt", "dataset_type": "CutPoint2D", "solution_dataset": "dset1",
                    "solution_type": "Solution", "solution_tag": "sol1", "point_x": "0.15", "point_y": "0.12"})
        if scenario == "wrong_file":
            upstream["probe_exports"][0]["filename"] = str(originals["u"])
        if scenario == "feature_override":
            upstream["probe_exports"][0]["feature_data"] = "other_point"
        if scenario == "wrong_dataset":
            upstream["probe_exports"][0]["solution_dataset"] = "dset2"
        if scenario == "unbound_label":
            upstream["probe_units"] = {"u": "m/s", "v": "m/s"}
            upstream["probe_unit_source"] = "comsol_model_export_node"
        return upstream
    monkeypatch.setattr(entry, "inspect_probe_and_solver", inspect)
    report = entry.run_prechecks(model_path=model, wake_u_path=originals["u"],
        wake_v_path=originals["v"], output_dir=tmp_path / "verification")
    probe = report["precheck_a_wake_probe"]
    assert (report["overall_prechecks_status"] == "PASS") == (scenario == "m/s")
    if scenario != "unbound_label":
        assert "probe_units" not in upstream and "probe_unit_source" not in upstream
    if scenario == "ft/s_header":
        assert probe["unit"] == "ft/s"
        assert probe["unit_source"] == "header"
    if scenario == "cm/s":
        assert probe["unit"] == "cm/s"
        assert upstream["probe_exports"][0]["unit"] == "cm/s"
        assert any("Invalid velocity unit" in e for e in probe["errors"])
    if scenario == "m/s":
        assert probe["unit_source"] == "comsol_model_export_node"
        assert probe["export_associations"]["u"]["fresh_export_evidence"]["expression"] == "u"
    if scenario in ("missing", "read_failure", "wrong_file", "wrong_dataset", "unbound_label", "feature_override"):
        assert probe["unit_source"] == "none"
        assert "comsol_model_export_node" not in json.dumps(probe)
