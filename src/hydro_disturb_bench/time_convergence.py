"""Reusable cycle-integrated physical statistics and temporal convergence metrics.

Provides exact continuous-window trapezoidal integration with boundary endpoint
interpolation, de-meaned RMS, cycle half-amplitude, Strouhal number calculation,
and multi-metric limit-cycle drift evaluation.
"""

from __future__ import annotations

import numpy as np


def integrate_exact_window(
    t: np.ndarray, y: np.ndarray, t_start: float, t_end: float
) -> tuple[float, float]:
    """Integrate y(t) over exact continuous window [t_start, t_end] with boundary interpolation.

    Guarantees:
    - If y(t) == C (constant), mean == C to machine precision.
    - If y(t) is linear, mean == (y(t_start) + y(t_end)) / 2 to machine precision.
    - The integration interval [t_start, t_end] matches the denominator (t_end - t_start) exactly.

    Returns:
        (integral_val, mean_val)
    """
    duration = float(t_end - t_start)
    if duration <= 0.0:
        raise ValueError(f"Invalid duration: t_end ({t_end}) must be > t_start ({t_start})")

    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    # Boundary interpolation
    inside = (t > t_start) & (t < t_end)
    t_eval = np.concatenate(([t_start], t[inside], [t_end]))
    y_eval = np.interp(t_eval, t, y)

    _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    integral_val = float(_trapz(y_eval, t_eval))
    mean_val = float(integral_val / duration)
    return integral_val, mean_val


def compute_cycle_mean(
    t: np.ndarray, y: np.ndarray, t_start: float, t_end: float
) -> float:
    """Compute exact continuous-window mean of y(t) over [t_start, t_end]."""
    _, mean_val = integrate_exact_window(t, y, t_start, t_end)
    return mean_val


def compute_demeaned_rms(
    t: np.ndarray, y: np.ndarray, t_start: float, t_end: float
) -> tuple[float, float]:
    """Compute exact cycle-integrated mean and de-meaned RMS over [t_start, t_end].

    Formula:
        y_bar = (1 / (t_end - t_start)) * int_{t_start}^{t_end} y(t) dt
        RMS = sqrt( (1 / (t_end - t_start)) * int_{t_start}^{t_end} (y(t) - y_bar)^2 dt )

    Returns:
        (mean_val, rms_val)
    """
    duration = float(t_end - t_start)
    _, y_bar = integrate_exact_window(t, y, t_start, t_end)

    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    inside = (t > t_start) & (t < t_end)
    t_eval = np.concatenate(([t_start], t[inside], [t_end]))
    y_eval = np.interp(t_eval, t, y)

    demeaned = y_eval - y_bar
    _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    rms_val = float(np.sqrt(_trapz(demeaned**2, t_eval) / duration))
    return y_bar, rms_val


def compute_cycle_half_amplitude(
    t: np.ndarray, y: np.ndarray, t_start: float, t_end: float
) -> float:
    """Compute half peak-to-peak amplitude over [t_start, t_end] including interpolated endpoints."""
    duration = float(t_end - t_start)
    if duration <= 0.0:
        raise ValueError(f"Invalid duration: t_end ({t_end}) must be > t_start ({t_start})")

    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    inside = (t > t_start) & (t < t_end)
    t_eval = np.concatenate(([t_start], t[inside], [t_end]))
    y_eval = np.interp(t_eval, t, y)
    return float((np.max(y_eval) - np.min(y_eval)) / 2.0)


def compute_strouhal_number(
    frequency: float, characteristic_length: float, u_inf: float
) -> float:
    """Compute dimensionless Strouhal number St = f * D / U_inf."""
    if characteristic_length <= 0.0:
        raise ValueError(f"characteristic_length must be > 0, got {characteristic_length}")
    if u_inf <= 0.0:
        raise ValueError(f"u_inf must be > 0, got {u_inf}")
    if frequency < 0.0:
        raise ValueError(f"frequency must be >= 0, got {frequency}")
    return float(frequency * characteristic_length / u_inf)


def evaluate_limit_cycle_stability(
    periods: list[float] | np.ndarray,
    amplitudes: list[float] | np.ndarray,
    cds: list[float] | np.ndarray,
    st_thresh: float = 1.0,
    amp_thresh: float = 2.0,
    cd_thresh: float = 1.0,
) -> tuple[bool, dict[str, float], str]:
    """Check whether cycle-by-cycle metrics satisfy recommended stability drift criteria.

    Returns:
        (is_stable, drifts_dict, reason_str)
    """
    st_drift = float((max(periods) - min(periods)) / np.mean(periods) * 100.0)
    amp_drift = float((max(amplitudes) - min(amplitudes)) / np.mean(amplitudes) * 100.0)
    cd_drift = float((max(cds) - min(cds)) / np.mean(cds) * 100.0)

    is_stable = bool(st_drift < st_thresh and amp_drift < amp_thresh and cd_drift < cd_thresh)
    drifts = {
        "st_drift_pct": st_drift,
        "amp_drift_pct": amp_drift,
        "cd_drift_pct": cd_drift,
    }

    reasons = []
    if st_drift >= st_thresh:
        reasons.append(f"St drift {st_drift:.3f}% >= {st_thresh}%")
    if amp_drift >= amp_thresh:
        reasons.append(f"Amp drift {amp_drift:.3f}% >= {amp_thresh}%")
    if cd_drift >= cd_thresh:
        reasons.append(f"Cd drift {cd_drift:.3f}% >= {cd_thresh}%")

    msg = "; ".join(reasons) if reasons else "Stable"
    return is_stable, drifts, msg
