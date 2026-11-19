"""KPI metrics, hydrodynamic performance indicators, cavitation, and conservation audits.

Provides:
- Bollard pull thrust, shaft power, and thrust-per-power extraction.
- Bidirectional thrust symmetry ratio (target >= 0.75, baseline = 0.5100).
- Open-water advance ratio (J) curves: K_T, K_Q, open-water efficiency eta_0.
- Cavitation margin and inception criteria (-99 kPa floor).
- CFD mass conservation, momentum closure, and y+ wall resolution audits.
- Tip-gap leakage and hover crossflow disturbance sensitivity.
"""
from __future__ import annotations

import math
from typing import Any

from .schema import HARD_INVARIANTS


def compute_bollard_kpis(
    f_rotor_x: float,
    f_duct_x: float,
    torque_nm: float,
    speed_rpm: float,
) -> dict[str, float]:
    """Calculate bollard pull (J=0) thrust, shaft power, and thrust-per-power ratio."""
    for val, name in ((f_rotor_x, "f_rotor_x"), (f_duct_x, "f_duct_x"), (torque_nm, "torque_nm"), (speed_rpm, "speed_rpm")):
        if not math.isfinite(float(val)):
            raise ValueError(f"Parameter {name} must be finite: {val}")

    f_total_x = f_rotor_x + f_duct_x
    thrust_n = abs(f_total_x)
    n_rps = abs(speed_rpm) / 60.0
    power_kw = (2.0 * math.pi * n_rps * abs(torque_nm)) / 1000.0
    if power_kw > 1e-9:
        t_over_p = thrust_n / power_kw
    else:
        t_over_p = 0.0

    return {
        "thrust_n": thrust_n,
        "power_kw": power_kw,
        "t_over_p": t_over_p,
        "f_rotor_x_n": f_rotor_x,
        "f_duct_x_n": f_duct_x,
        "torque_nm": abs(torque_nm),
        "speed_rpm": speed_rpm,
    }


compute_bollard_pull = compute_bollard_kpis
extract_bollard_metrics = compute_bollard_kpis


def compute_thrust_symmetry(t_cw: float, t_ccw: float) -> float:
    """Calculate bidirectional thrust symmetry ratio min(T_CW, T_CCW) / max(T_CW, T_CCW)."""
    t1 = float(t_cw)
    t2 = float(t_ccw)
    if not (math.isfinite(t1) and math.isfinite(t2)):
        raise ValueError("Thrust values must be finite")
    if t1 < 0.0 or t2 < 0.0:
        return 0.0
    t_max = max(t1, t2)
    if t_max <= 1e-9:
        return 0.0
    return min(t1, t2) / t_max


compute_bidirectional_symmetry = compute_thrust_symmetry
symmetry_ratio = compute_thrust_symmetry


def check_cfd_quality(
    mass_imbalance_pct: float,
    momentum_closure_pct: float,
    y_plus_imp: float,
    y_plus_duct: float,
    rel_err: float | None = None,
) -> dict[str, Any]:
    """Audit CFD convergence quality against strict physical conservation thresholds."""
    reasons: list[str] = []

    # Strict mass imbalance < 1.0% (at 1.0% fails)
    if mass_imbalance_pct >= 1.0:
        reasons.append(f"mass_imbalance_pct={mass_imbalance_pct:.3f}% >= 1.0%")

    if momentum_closure_pct >= 5.0:
        reasons.append(f"momentum_closure_pct={momentum_closure_pct:.3f}% >= 5.0%")

    if y_plus_imp < 11.0 or y_plus_imp > 300.0:
        reasons.append(f"y_plus_imp={y_plus_imp:.1f} outside standard log-law wall range [11, 300]")

    if y_plus_duct < 11.0 or y_plus_duct > 300.0:
        reasons.append(f"y_plus_duct={y_plus_duct:.1f} outside standard log-law wall range [11, 300]")

    if rel_err is not None and rel_err > 0.0025 + 1e-7:
        reasons.append(f"rel_err={rel_err:.5f} > 0.0025")

    passed = (len(reasons) == 0)
    return {
        "passed": passed,
        "is_valid": passed,
        "mass_imbalance_pct": mass_imbalance_pct,
        "momentum_closure_pct": momentum_closure_pct,
        "y_plus_imp": y_plus_imp,
        "y_plus_duct": y_plus_duct,
        "rel_err": rel_err,
        "reasons": reasons,
    }


audit_cfd_quality = check_cfd_quality
validate_cfd_quality = check_cfd_quality


def compute_dp_kpis(
    u_a_mps: float,
    speed_rpm: float,
    d_prop_m: float = HARD_INVARIANTS["D_prop_m"],
    thrust_n: float = 0.0,
    torque_nm: float = 0.0,
    rho: float = 1025.0,
) -> dict[str, float]:
    """Calculate open-water dynamic positioning curves K_T, K_Q, and eta_0 from axial velocity."""
    if speed_rpm == 0.0 or not math.isfinite(speed_rpm):
        raise ValueError(f"speed_rpm must be non-zero and finite, got {speed_rpm}")
    if torque_nm <= 0.0 or not math.isfinite(torque_nm):
        raise ValueError(f"torque_nm must be positive and finite, got {torque_nm}")

    n_rps = abs(speed_rpm) / 60.0
    j_advance = u_a_mps / (n_rps * d_prop_m) if n_rps > 1e-9 else 0.0

    denom_t = rho * (n_rps**2) * (d_prop_m**4) if n_rps > 1e-9 else 1.0
    denom_q = rho * (n_rps**2) * (d_prop_m**5) if n_rps > 1e-9 else 1.0

    k_t = thrust_n / denom_t
    k_q = torque_nm / denom_q
    eta_0 = (j_advance / (2.0 * math.pi)) * (k_t / k_q) if (k_q > 1e-9 and j_advance > 0.0) else 0.0

    return {
        "J": j_advance,
        "K_T": k_t,
        "K_Q": k_q,
        "eta_0": eta_0,
        "u_a_mps": u_a_mps,
        "thrust_n": thrust_n,
        "torque_nm": torque_nm,
    }


compute_open_water_kpis = compute_dp_kpis
extract_dp_metrics = compute_dp_kpis
compute_open_water_dp = compute_dp_kpis
evaluate_open_water = compute_dp_kpis


def compute_hover_disturbance(
    t_0_n: float = 2000.0,
    t_disturbed_n: float | None = None,
    f_y_n: float = 0.0,
    f_z_n: float = 0.0,
    alpha: float = 0.5,
    delta_t_rel: float | None = None,
    f_yz_n: float | None = None,
    t_mean_n: float | None = None,
) -> dict[str, float]:
    """Calculate hover crossflow disturbance rejection index S_dist = Delta_T / T0 + alpha * (||F_yz|| / T0)."""
    raw_t0 = t_0_n if t_0_n is not None else t_mean_n
    if raw_t0 is None or raw_t0 <= 0.0 or not math.isfinite(raw_t0):
        raise ValueError(f"t_0_n must be positive and finite: {raw_t0}")
    t0 = float(raw_t0)

    if t_disturbed_n is not None and not math.isfinite(t_disturbed_n):
        raise ValueError(f"t_disturbed_n must be finite: {t_disturbed_n}")

    if delta_t_rel is None:
        if t_disturbed_n is not None:
            delta_t_rel = abs(t0 - t_disturbed_n) / t0
        else:
            delta_t_rel = 0.0
    if f_yz_n is None:
        f_yz_n = math.sqrt(f_y_n**2 + f_z_n**2)
    s_dist = abs(delta_t_rel) + alpha * (f_yz_n / t0)
    return {
        "delta_t_rel": delta_t_rel,
        "f_yz_n": f_yz_n,
        "s_dist": s_dist,
    }


compute_disturbance_sensitivity = compute_hover_disturbance


def extract_transient_pulse_metrics(
    times_s: Any,
    thrust_series_n: Any,
    t_0_n: float,
) -> dict[str, float]:
    """Calculate peak disturbance relative thrust deficit and settling time back to 2% envelope."""
    import numpy as np

    t = np.asarray(times_s)
    thrust = np.asarray(thrust_series_n)
    if t.size < 2 or thrust.size < 2:
        raise ValueError("Time and thrust series must contain at least 2 points for transient pulse analysis")

    diff = np.abs(thrust - t_0_n)
    peak_diff = float(np.max(diff))
    peak_delta_t_rel = peak_diff / max(1.0, t_0_n)

    # Settling time: time duration between disturbance pulse initiation/peak and recovery inside 2% band
    threshold = 0.02 * max(1.0, t_0_n)
    outside_indices = np.where(diff > threshold)[0]
    if len(outside_indices) > 0:
        first_t = t[outside_indices[0]]
        last_t = t[outside_indices[-1]]
        settling_time_s = float(last_t - first_t)
    else:
        settling_time_s = 0.0

    return {
        "peak_delta_t_rel": peak_delta_t_rel,
        "settling_time_s": max(0.5, settling_time_s),
    }


compute_pulse_disturbance_metrics = extract_transient_pulse_metrics


def compute_cavitation_metrics(
    p_min_gauge_kpa: float,
    area_sub99_m2: float,
    area_imp_m2: float,
    depth_m: float = 0.0,
    speed_rpm: float = 1400.0,
    rho: float = 1025.0,
    p_atm: float = 101325.0,
    p_v: float = 2338.0,
    g: float = 9.81,
    d_prop: float = HARD_INVARIANTS["D_prop_m"],
) -> dict[str, Any]:
    """Assess cavitation inception, cavitation number sigma, Cp_min, and C3 requirement."""
    if depth_m < 0.0:
        raise ValueError(f"depth_m cannot be negative: {depth_m}")
    if speed_rpm == 0.0 or not math.isfinite(speed_rpm):
        raise ValueError(f"speed_rpm must be non-zero and finite: {speed_rpm}")
    if area_imp_m2 <= 0.0 or not math.isfinite(area_imp_m2):
        raise ValueError(f"area_imp_m2 must be positive: {area_imp_m2}")
    if p_min_gauge_kpa > -99.0 and area_sub99_m2 > 1e-9:
        raise ValueError(
            f"Physical inconsistency: p_min_gauge_kpa={p_min_gauge_kpa} > -99 kPa but area_sub99_m2={area_sub99_m2} > 0"
        )

    p_amb = p_atm + rho * g * depth_m
    n_rps = abs(speed_rpm) / 60.0
    v_tip = math.pi * n_rps * d_prop
    q_dyn = 0.5 * rho * (v_tip**2)

    sigma = (p_amb - p_v) / q_dyn
    p_min_gauge_pa = p_min_gauge_kpa * 1000.0
    c_p_min = p_min_gauge_pa / q_dyn
    margin = sigma + c_p_min

    # C3 floor: gauge pressure on blade must not drop below -99 kPa (vapor pressure at surface)
    c3_passed = (p_min_gauge_kpa >= -99.00 - 1e-6 and area_sub99_m2 <= 1e-9)
    cav_area_ratio = area_sub99_m2 / area_imp_m2

    return {
        "c3_passed": c3_passed,
        "is_valid": c3_passed,
        "p_min_gauge_kpa": p_min_gauge_kpa,
        "cav_area_ratio": cav_area_ratio,
        "area_sub99_m2": area_sub99_m2,
        "sigma": sigma,
        "c_p_min": c_p_min,
        "margin": margin,
        "depth_m": depth_m,
    }


evaluate_cavitation = compute_cavitation_metrics


def aggregate_bidirectional_cavitation(
    p_min_ccw_kpa: float,
    p_min_cw_kpa: float,
    cav_area_ccw_pct: float,
    cav_area_cw_pct: float,
) -> dict[str, Any]:
    """Aggregate CW and CCW cavitation into worst-case KPIs."""
    worst_p_min = min(p_min_ccw_kpa, p_min_cw_kpa)
    worst_area = max(cav_area_ccw_pct, cav_area_cw_pct)
    c3_passed = (worst_p_min >= -99.00 - 1e-6 and worst_area <= 1e-6)

    return {
        "c3_passed": c3_passed,
        "worst_p_min_kpa": worst_p_min,
        "worst_cav_area_pct": worst_area,
        "p_min_ccw_kpa": p_min_ccw_kpa,
        "p_min_cw_kpa": p_min_cw_kpa,
        "cav_area_ccw_pct": cav_area_ccw_pct,
        "cav_area_cw_pct": cav_area_cw_pct,
    }


def compute_tip_gap_leakage(tip_clearance_m: float, delta_p_pa: float) -> float:
    """Calculate tip clearance vortex leakage flow index (scales with clearance * sqrt(dp))."""
    if tip_clearance_m < 0.0 or not math.isfinite(tip_clearance_m):
        raise ValueError(f"Invalid tip_clearance_m: {tip_clearance_m}")
    dp = max(0.0, float(delta_p_pa))
    return float(tip_clearance_m * math.sqrt(dp) * 0.1)


estimate_tip_leakage = compute_tip_gap_leakage

