"""Mathematical determination of vortex-shedding limit-cycle onset and stationarity.

Provides cycle extraction via linear zero-crossing interpolation, sliding-window
stationarity drift metrics, multi-tier stability onset detection, and a high-level
convenience interface for determining the earliest stationary checkpoint time.
"""

from __future__ import annotations

from typing import Any

import numpy as np

U_INF = 0.010058104588
D = 0.01


def extract_cycles(
    t_cl: np.ndarray,
    cl: np.ndarray,
    t_cd: np.ndarray,
    cd: np.ndarray,
    u_inf: float = U_INF,
    d: float = D,
) -> tuple[list[float], list[dict[str, Any]]]:
    """Find all upward zero-crossings and compute exact per-cycle metrics."""
    t_cl = np.asarray(t_cl, dtype=np.float64)
    cl = np.asarray(cl, dtype=np.float64)
    t_cd = np.asarray(t_cd, dtype=np.float64)
    cd = np.asarray(cd, dtype=np.float64)

    zc: list[float] = []
    for i in range(len(t_cl) - 1):
        if cl[i] <= 0.0 < cl[i + 1]:
            tz = t_cl[i] - cl[i] * (t_cl[i + 1] - t_cl[i]) / (cl[i + 1] - cl[i])
            zc.append(float(tz))

    cycles: list[dict[str, Any]] = []
    for k in range(len(zc) - 1):
        t_start = zc[k]
        t_end = zc[k + 1]
        period = t_end - t_start
        st = (d / u_inf) / period

        mask = (t_cl >= t_start) & (t_cl <= t_end)
        t_sub = np.concatenate(([t_start], t_cl[mask], [t_end]))
        t_sub = np.unique(t_sub)
        cl_sub = np.interp(t_sub, t_cl, cl)
        cd_sub = np.interp(t_sub, t_cd, cd)

        peak_idx = int(np.argmax(cl_sub))
        trough_idx = int(np.argmin(cl_sub))
        t_peak = float(t_sub[peak_idx])
        t_trough = float(t_sub[trough_idx])
        val_peak = float(cl_sub[peak_idx])
        val_trough = float(cl_sub[trough_idx])
        amp_half = float((val_peak - val_trough) / 2.0)

        _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
        mean_cl = float(_trapz(cl_sub, t_sub) / period)
        rms_cl = float(np.sqrt(_trapz((cl_sub - mean_cl) ** 2, t_sub) / period))
        mean_cd = float(_trapz(cd_sub, t_sub) / period)

        cycles.append(
            {
                "cycle_idx": k,
                "t_start": float(t_start),
                "t_end": float(t_end),
                "t_peak": t_peak,
                "t_trough": t_trough,
                "val_peak": val_peak,
                "val_trough": val_trough,
                "T": float(period),
                "St": float(st),
                "amp_half": amp_half,
                "mean_cl": mean_cl,
                "rms_cl": rms_cl,
                "mean_cd": mean_cd,
            }
        )

    return zc, cycles


def compute_sliding_stationarity(
    cycles: list[dict[str, Any]], window_size: int = 5
) -> list[dict[str, Any]]:
    """Compute sliding window drift across consecutive cycles."""
    results: list[dict[str, Any]] = []
    for i in range(len(cycles) - window_size + 1):
        win = cycles[i : i + window_size]
        periods = [c["T"] for c in win]
        amps = [c["amp_half"] for c in win]
        cds = [c["mean_cd"] for c in win]

        t_drift = float((max(periods) - min(periods)) / np.mean(periods) * 100.0)
        amp_drift = float((max(amps) - min(amps)) / np.mean(amps) * 100.0)
        cd_drift = float((max(cds) - min(cds)) / np.mean(cds) * 100.0)

        results.append(
            {
                "start_cycle": i,
                "end_cycle": i + window_size - 1,
                "t_start": float(win[0]["t_start"]),
                "t_end": float(win[-1]["t_end"]),
                "t_drift_pct": t_drift,
                "amp_drift_pct": amp_drift,
                "cd_drift_pct": cd_drift,
            }
        )
    return results


def find_stability_onset(
    cycles: list[dict[str, Any]],
    tol_amp: float,
    tol_st: float,
    tol_cd: float,
    ref_amp: float,
    ref_st: float,
    ref_cd: float,
) -> dict[str, Any] | None:
    """Find the earliest cycle where ALL subsequent cycles remain within tolerance."""
    for k in range(len(cycles)):
        all_pass = True
        for follow in cycles[k:]:
            amp_err = abs(follow["amp_half"] - ref_amp) / ref_amp * 100.0
            st_err = abs(follow["St"] - ref_st) / ref_st * 100.0
            cd_err = abs(follow["mean_cd"] - ref_cd) / ref_cd * 100.0
            if amp_err > tol_amp or st_err > tol_st or cd_err > tol_cd:
                all_pass = False
                break
        if all_pass:
            return {
                "cycle_idx": k,
                "t_start": cycles[k]["t_start"],
                "t_end": cycles[k]["t_end"],
                "T": cycles[k]["T"],
                "St": cycles[k]["St"],
                "amp_half": cycles[k]["amp_half"],
                "amp_err_pct": (cycles[k]["amp_half"] - ref_amp) / ref_amp * 100.0,
                "mean_cd": cycles[k]["mean_cd"],
                "cd_err_pct": (cycles[k]["mean_cd"] - ref_cd) / ref_cd * 100.0,
            }
    return None


def detect_limit_cycle_onset(
    t_cl: np.ndarray,
    cl: np.ndarray,
    t_cd: np.ndarray | None = None,
    cd: np.ndarray | None = None,
    window_cycles: int = 5,
    tol_pct: float = 1.0,
    u_inf: float = U_INF,
    d: float = D,
) -> float | None:
    """Return the earliest physical time t_start where sliding window drift remains <= tol_pct."""
    t_cl_arr = np.asarray(t_cl, dtype=np.float64)
    cl_arr = np.asarray(cl, dtype=np.float64)
    if t_cd is None or cd is None:
        t_cd_arr = t_cl_arr
        cd_arr = np.ones_like(cl_arr, dtype=np.float64)
    else:
        t_cd_arr = np.asarray(t_cd, dtype=np.float64)
        cd_arr = np.asarray(cd, dtype=np.float64)

    _, cycles = extract_cycles(t_cl_arr, cl_arr, t_cd_arr, cd_arr, u_inf=u_inf, d=d)
    if len(cycles) < window_cycles:
        return None

    sliding = compute_sliding_stationarity(cycles, window_size=window_cycles)
    for idx in range(len(sliding)):
        if all(
            w["amp_drift_pct"] <= tol_pct
            and w["t_drift_pct"] <= tol_pct
            and w["cd_drift_pct"] <= tol_pct
            for w in sliding[idx:]
        ):
            return float(sliding[idx]["t_start"])
    return None
