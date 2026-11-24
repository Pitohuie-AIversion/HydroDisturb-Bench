#!/usr/bin/env python3
"""Genuine Tier 2 Microscopic 3D Flow-Field Certification & Forensic Audit for HDF5 Datasets.

Performs independent, zero-hardcoding physical & numerical verification on 6-channel
World Model HDF5 tensors of shape (T, 6, Nz, Ny, Nx) with channels:
  [0] u_star       (streamwise velocity / U0)
  [1] v_cross_star (cross-stream velocity / U0)
  [2] w_span_star  (spanwise velocity / U0)
  [3] p_star       (pressure / (rho0 * U0^2))
  [4] sdf_star     (signed distance / D)
  [5] solid_mask   (1 inside cylinder, 0 in fluid)

Verification Modules:
  (a) Structural finiteness (zero NaN/Inf) & strict solid-body rigidity (max |u*_solid| == 0)
  (b) 3D Q-criterion coherent vortex structures (Q > 0 fraction, quantiles, enstrophy, divergence)
  (c) Wake spatial energy spectrum (1D streamwise & 3D isotropic shell-averaged inertial slope ~ -5/3,
      plus high-k aliasing/pile-up detection)
  (d) Spanwise two-point autocorrelation R_uu(dz*) & integral coherence length Lz* (3D vs 2D check)
  (e) Physical scale, metadata provenance & temporal SVD rank authenticity audit

Outputs a structured JSON certification report.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

import h5py
import numpy as np

BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(BENCH_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT / "src"))

from hydro_disturb_bench.vorticity_dynamics import (
    compute_divergence_3d,
    compute_q_criterion_3d,
    compute_vorticity_3d,
)


def _compute_1d_wake_spectrum(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x_star: np.ndarray,
    y_star: np.ndarray,
) -> dict[str, Any]:
    """Compute streamwise 1D TKE spatial spectrum E(k_x*) averaged over wake (z, y)."""
    ix_mask = (x_star >= 1.0) & (x_star <= 9.5)
    iy_mask = np.abs(y_star) <= 1.5
    dx = float(x_star[1] - x_star[0])

    u_w = u[:, iy_mask, :][:, :, ix_mask].astype(np.float64)
    v_w = v[:, iy_mask, :][:, :, ix_mask].astype(np.float64)
    w_w = w[:, iy_mask, :][:, :, ix_mask].astype(np.float64)

    # Remove mean along streamwise x for each (z, y) line & apply Hanning window
    nx_w = u_w.shape[2]
    win = np.hanning(nx_w)
    win_norm = float(np.sum(win ** 2))
    u_fluc = (u_w - np.mean(u_w, axis=2, keepdims=True)) * win[None, None, :]
    v_fluc = (v_w - np.mean(v_w, axis=2, keepdims=True)) * win[None, None, :]
    w_fluc = (w_w - np.mean(w_w, axis=2, keepdims=True)) * win[None, None, :]

    fu = np.fft.rfft(u_fluc, axis=2)
    fv = np.fft.rfft(v_fluc, axis=2)
    fw = np.fft.rfft(w_fluc, axis=2)

    # 1D PSD averaged over all (z, y) lines in wake
    psd = 0.5 * np.mean(np.abs(fu) ** 2 + np.abs(fv) ** 2 + np.abs(fw) ** 2, axis=(0, 1)) * (dx / win_norm)
    k_cyc = np.fft.rfftfreq(nx_w, d=dx)  # cycles per D
    k_rad = 2.0 * np.pi * k_cyc          # rad per D

    # Exclude k=0; fit inertial subrange over intermediate resolved band k_rad in [1.5, 12.0] rad/D
    # (wavelengths from ~4.2 D down to ~0.52 D = 8.4 grid cells; Nyquist is pi/0.0625 = 50.27 rad/D)
    k_nyq = float(k_rad[-1])
    fit_mask = (k_rad >= 1.5) & (k_rad <= 12.0) & (psd > 0)
    if np.sum(fit_mask) >= 4:
        lk = np.log10(k_rad[fit_mask])
        le = np.log10(psd[fit_mask])
        slope, intercept = np.polyfit(lk, le, 1)
        le_pred = slope * lk + intercept
        ss_res = float(np.sum((le - le_pred) ** 2))
        ss_tot = float(np.sum((le - np.mean(le)) ** 2))
        r2 = 1.0 - ss_res / max(ss_tot, 1e-30)
    else:
        slope, r2 = float("nan"), float("nan")

    # High-k aliasing / pile-up check: compare top 20% wavenumbers vs mid-band & peak
    pos_mask = k_rad > 0
    k_pos = k_rad[pos_mask]
    e_pos = psd[pos_mask]
    e_peak = float(np.max(e_pos))
    mid_band = (k_pos >= 2.0) & (k_pos <= 8.0)
    high_band = k_pos >= 0.75 * k_nyq
    e_mid = float(np.mean(e_pos[mid_band])) if np.any(mid_band) else float("nan")
    e_high = float(np.mean(e_pos[high_band])) if np.any(high_band) else float("nan")

    # Local slope in top 30% of spectrum (must stay negative, no pile-up)
    tail_mask = (k_pos >= 0.65 * k_nyq) & (e_pos > 0)
    if np.sum(tail_mask) >= 4:
        high_k_slope = float(np.polyfit(np.log10(k_pos[tail_mask]), np.log10(e_pos[tail_mask]), 1)[0])
    else:
        high_k_slope = float("nan")

    no_aliasing = bool(
        np.isfinite(e_high)
        and np.isfinite(e_mid)
        and (e_high < 0.1 * e_mid)
        and (e_high < 0.01 * e_peak)
        and (high_k_slope < -0.5)
    )

    return {
        "wake_roi_x_star": [float(x_star[ix_mask][0]), float(x_star[ix_mask][-1])],
        "wake_roi_y_star": [float(y_star[iy_mask][0]), float(y_star[iy_mask][-1])],
        "nx_wake": int(nx_w),
        "k_nyquist_rad_per_D": round(k_nyq, 4),
        "fit_band_rad_per_D": [1.5, 12.0],
        "inertial_slope_1d_kx": round(float(slope), 4),
        "inertial_fit_r2": round(float(r2), 4),
        "kolmogorov_ref_slope": -1.6667,
        "E_peak": float(e_peak),
        "E_mid_band_mean": float(e_mid),
        "E_high_k_top25pct_mean": float(e_high),
        "high_to_mid_energy_ratio": float(e_high / max(e_mid, 1e-30)),
        "high_k_tail_slope": round(float(high_k_slope), 4),
        "no_high_k_aliasing_blowup": no_aliasing,
    }


def _compute_3d_isotropic_spectrum(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x_star: np.ndarray,
    y_star: np.ndarray,
    z_star: np.ndarray,
) -> dict[str, Any]:
    """Compute 3D isotropic shell-averaged TKE spectrum E(k*) in the wake box."""
    ix_mask = (x_star >= 1.0) & (x_star <= 9.0)
    iy_mask = np.abs(y_star) <= 2.0
    dx = float(x_star[1] - x_star[0])
    dy = float(y_star[1] - y_star[0])
    dz = float(z_star[1] - z_star[0])

    u_b = u[:, iy_mask, :][:, :, ix_mask].astype(np.float64)
    v_b = v[:, iy_mask, :][:, :, ix_mask].astype(np.float64)
    w_b = w[:, iy_mask, :][:, :, ix_mask].astype(np.float64)
    nz, ny, nx = u_b.shape

    # 3D Hanning window to suppress non-periodic boundary leakage
    wz = np.hanning(nz)[:, None, None]
    wy = np.hanning(ny)[None, :, None]
    wx = np.hanning(nx)[None, None, :]
    win3d = wz * wy * wx

    u_f = (u_b - np.mean(u_b)) * win3d
    v_f = (v_b - np.mean(v_b)) * win3d
    w_f = (w_b - np.mean(w_b)) * win3d

    e_k = 0.5 * (
        np.abs(np.fft.fftn(u_f)) ** 2
        + np.abs(np.fft.fftn(v_f)) ** 2
        + np.abs(np.fft.fftn(w_f)) ** 2
    ) / (nx * ny * nz) ** 2

    kz = 2.0 * np.pi * np.fft.fftfreq(nz, d=dz)
    ky = 2.0 * np.pi * np.fft.fftfreq(ny, d=dy)
    kx = 2.0 * np.pi * np.fft.fftfreq(nx, d=dx)
    kmag = np.sqrt(kz[:, None, None] ** 2 + ky[None, :, None] ** 2 + kx[None, None, :] ** 2)

    dk = 2.0 * np.pi / min(nz * dz, ny * dy, nx * dx)
    k_max = np.pi / max(dx, dy, dz)
    n_bins = int(k_max / dk)
    k_bins = np.arange(1, n_bins + 1) * dk
    e_shell = np.zeros_like(k_bins)
    for i, kb in enumerate(k_bins):
        m = (kmag >= kb - 0.5 * dk) & (kmag < kb + 0.5 * dk)
        if np.any(m):
            e_shell[i] = float(np.sum(e_k[m])) / dk

    fit_m = (k_bins >= 3.0) & (k_bins <= 16.0) & (e_shell > 0)
    if np.sum(fit_m) >= 4:
        lk = np.log10(k_bins[fit_m])
        le = np.log10(e_shell[fit_m])
        slope, intercept = np.polyfit(lk, le, 1)
        pred = slope * lk + intercept
        r2 = 1.0 - float(np.sum((le - pred) ** 2)) / max(float(np.sum((le - np.mean(le)) ** 2)), 1e-30)
    else:
        slope, r2 = float("nan"), float("nan")

    tail_m = (k_bins >= 0.65 * k_max) & (e_shell > 0)
    tail_slope = float(np.polyfit(np.log10(k_bins[tail_m]), np.log10(e_shell[tail_m]), 1)[0]) if np.sum(tail_m) >= 3 else float("nan")

    return {
        "wake_box_shape_zyx": [int(nz), int(ny), int(nx)],
        "dk_rad_per_D": round(float(dk), 4),
        "fit_band_rad_per_D": [3.0, 16.0],
        "isotropic_3d_slope": round(float(slope), 4),
        "isotropic_3d_r2": round(float(r2), 4),
        "isotropic_high_k_tail_slope": round(float(tail_slope), 4),
    }


def _compute_spanwise_autocorrelation(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x_star: np.ndarray,
    y_star: np.ndarray,
    z_star: np.ndarray,
) -> dict[str, Any]:
    """Compute unbiased spanwise two-point autocorrelation R_uu(dz*) across wake stations."""
    nz = len(z_star)
    dz = float(z_star[1] - z_star[0])
    lags = np.arange(nz // 2 + 1)
    dz_lags = lags * dz  # 0 .. ~2.0 D

    def _ruu_for_region(field: np.ndarray, ix_sel: np.ndarray, iy_sel: np.ndarray) -> tuple[np.ndarray, float]:
        sub = field[:, iy_sel, :][:, :, ix_sel].astype(np.float64)
        # Fluctuation about spanwise mean at each (y, x)
        fluc = sub - np.mean(sub, axis=0, keepdims=True)
        var_mean = float(np.mean(fluc ** 2))
        if var_mean < 1e-16:
            return np.ones(len(lags)), 0.0
        r_vals = np.zeros(len(lags), dtype=np.float64)
        for li, lag in enumerate(lags):
            if lag == 0:
                r_vals[li] = 1.0
            else:
                num = np.mean(fluc[: nz - lag] * fluc[lag:])
                den = np.sqrt(np.mean(fluc[: nz - lag] ** 2) * np.mean(fluc[lag:] ** 2))
                r_vals[li] = float(num / den) if den > 1e-16 else 0.0
        return r_vals, float(np.sqrt(var_mean))

    ix_wake = np.where((x_star >= 1.0) & (x_star <= 6.0))[0]
    iy_wake = np.where(np.abs(y_star) <= 1.0)[0]

    r_uu, u_span_rms = _ruu_for_region(u, ix_wake, iy_wake)
    r_vv, v_span_rms = _ruu_for_region(v, ix_wake, iy_wake)
    r_ww, w_span_rms = _ruu_for_region(w, ix_wake, iy_wake)

    # Also compute R_uu at individual wake x* stations
    stations: dict[str, Any] = {}
    for x_target in (1.5, 2.5, 4.0, 6.0):
        ix = int(np.argmin(np.abs(x_star - x_target)))
        r_st, rms_st = _ruu_for_region(u, np.array([ix]), iy_wake)
        # Integral length up to first zero crossing
        zero_idx = np.where(r_st <= 0.0)[0]
        end_i = int(zero_idx[0]) if len(zero_idx) > 0 else len(r_st) - 1
        _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
        lz_st = float(_trapz(np.maximum(r_st[: end_i + 1], 0.0), dz_lags[: end_i + 1]))
        idx_1d = int(np.argmin(np.abs(dz_lags - 1.0)))
        idx_2d = int(np.argmin(np.abs(dz_lags - 2.0)))
        stations[f"x_star_{x_target:.1f}"] = {
            "x_star_actual": round(float(x_star[ix]), 4),
            "spanwise_u_rms": round(rms_st, 6),
            "R_uu_at_1D": round(float(r_st[idx_1d]), 4),
            "R_uu_at_2D": round(float(r_st[idx_2d]), 4),
            "R_uu_min": round(float(np.min(r_st)), 4),
            "Lz_star_integral": round(lz_st, 4),
        }

    # Global wake integral length Lz*
    _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
    zero_idx = np.where(r_uu <= 0.0)[0]
    end_i = int(zero_idx[0]) if len(zero_idx) > 0 else len(r_uu) - 1
    lz_star_uu = float(_trapz(np.maximum(r_uu[: end_i + 1], 0.0), dz_lags[: end_i + 1]))
    idx_05 = int(np.argmin(np.abs(dz_lags - 0.5)))
    idx_10 = int(np.argmin(np.abs(dz_lags - 1.0)))
    idx_15 = int(np.argmin(np.abs(dz_lags - 1.5)))
    idx_20 = int(np.argmin(np.abs(dz_lags - 2.0)))

    # 3D energy fraction: spanwise w* RMS relative to cross-stream v* RMS in wake
    u_wake_tot_std = float(np.std(u[:, iy_wake, :][:, :, ix_wake]))
    v_wake_tot_rms = float(np.sqrt(np.mean(v[:, iy_wake, :][:, :, ix_wake] ** 2)))
    w_wake_tot_rms = float(np.sqrt(np.mean(w[:, iy_wake, :][:, :, ix_wake] ** 2)))

    is_3d_decorrelated = bool(
        (u_span_rms > 1e-4)
        and (float(r_uu[idx_20]) < 0.85 or float(np.min(r_uu)) < 0.5)
        and (0.05 <= lz_star_uu <= 3.5)
    )

    return {
        "wake_x_star_range": [float(x_star[ix_wake[0]]), float(x_star[ix_wake[-1]])],
        "wake_y_star_range": [float(y_star[iy_wake[0]]), float(y_star[iy_wake[-1]])],
        "span_length_D": round(float(z_star[-1] - z_star[0] + dz), 4),
        "dz_star": round(dz, 5),
        "spanwise_u_rms": round(u_span_rms, 6),
        "spanwise_v_rms": round(v_span_rms, 6),
        "spanwise_w_rms": round(w_span_rms, 6),
        "spanwise_to_total_u_std_ratio": round(u_span_rms / max(u_wake_tot_std, 1e-12), 5),
        "w_span_to_v_cross_rms_ratio": round(w_wake_tot_rms / max(v_wake_tot_rms, 1e-12), 5),
        "R_uu_profile_sampled": {
            "dz_0.00D": 1.0,
            "dz_0.50D": round(float(r_uu[idx_05]), 4),
            "dz_1.00D": round(float(r_uu[idx_10]), 4),
            "dz_1.50D": round(float(r_uu[idx_15]), 4),
            "dz_2.00D": round(float(r_uu[idx_20]), 4),
        },
        "R_uu_min": round(float(np.min(r_uu)), 4),
        "R_vv_at_1D": round(float(r_vv[idx_10]), 4),
        "R_ww_at_1D": round(float(r_ww[idx_10]), 4),
        "Lz_star_integral_uu": round(lz_star_uu, 4),
        "stations": stations,
        "is_true_3d_decorrelated": is_3d_decorrelated,
    }


def _temporal_svd_and_provenance_audit(
    h5_path: Path,
    fields: np.ndarray,
    attrs: dict[str, Any],
    x_star: np.ndarray,
) -> dict[str, Any]:
    """Forensic audit of nondimensional scales, metadata provenance, and temporal rank."""
    T = fields.shape[0]
    inlet_u = fields[:, 0, :, :, :5]
    inlet_u_mean = float(np.mean(inlet_u))
    p_p99 = float(np.percentile(np.abs(fields[:, 3]), 99))
    u_frame_means = [round(float(fields[t, 0].mean()), 5) for t in range(T)]

    # Metadata provenance check
    src_model = str(attrs.get("source_model", ""))
    re_attr = float(attrs.get("reynolds_number", float("nan")))
    u0_attr = float(attrs.get("u0_m_s", float("nan")))
    provenance_mismatch = False
    provenance_notes: list[str] = []
    if "re5k" in src_model.lower() and abs(re_attr - 5000.0) > 1000.0:
        provenance_mismatch = True
        provenance_notes.append(
            f"Metadata mismatch: attrs claim Re={re_attr:,.0f}, U0={u0_attr} m/s, but source_model='{src_model}'."
        )

    scale_plausible = bool((0.70 <= inlet_u_mean <= 1.35) and (p_p99 <= 25.0))
    if not scale_plausible:
        provenance_notes.append(
            f"Unphysical nondimensional scale: inlet u*_mean={inlet_u_mean:.3f} (expected ~1.0), "
            f"|p*|_p99={p_p99:.1f} (expected O(1))."
        )

    # Temporal SVD rank check (if T >= 4)
    svd_info: dict[str, Any] = {"num_frames": int(T)}
    is_synthetic_low_rank = False
    if T >= 4:
        # Subsample spatially by stride 4 for exact fast SVD of (T, N_sub)
        u_sub = fields[:, 0, ::4, ::4, ::4].reshape(T, -1).astype(np.float64)
        _, svals, _ = np.linalg.svd(u_sub, full_matrices=False)
        energy = svals ** 2
        tot_e = float(np.sum(energy))
        top2_frac = float(np.sum(energy[:2]) / max(tot_e, 1e-30))
        s3_over_s1 = float(svals[2] / max(svals[0], 1e-30)) if len(svals) >= 3 else 0.0
        eff_rank = int(np.sum(svals > 1e-4 * svals[0]))
        svd_info.update(
            {
                "top5_singular_values": [round(float(x), 6) for x in svals[:5]],
                "top2_energy_fraction": top2_frac,
                "s3_over_s1_ratio": s3_over_s1,
                "effective_rank_1e4": eff_rank,
            }
        )
        if T >= 10 and eff_rank <= 2 and s3_over_s1 < 1e-5:
            is_synthetic_low_rank = True
            provenance_notes.append(
                f"Temporal trajectory of T={T} frames has effective SVD rank={eff_rank} "
                f"(s3/s1={s3_over_s1:.2e}, top-2 modes explain {top2_frac*100:.8f}% energy): "
                "proves frames are synthetic 2-endpoint interpolation, NOT independent COMSOL timesteps."
            )

    # Cross-check against sibling test_re5k_pilot_sample.h5 if present
    sibling = h5_path.parent / "test_re5k_pilot_sample.h5"
    sibling_match: dict[str, Any] | None = None
    if sibling.is_file() and sibling.resolve() != h5_path.resolve() and T >= 3:
        with h5py.File(str(sibling), "r") as fs:
            if "flow_fields" in fs and fs["flow_fields"].shape[0] >= 2:
                s0 = fs["flow_fields"][0, 0].astype(np.float64)
                s1 = fs["flow_fields"][1, 0].astype(np.float64)
                diff = s1 - s0
                denom = float(np.sum(diff * diff))
                if denom > 1e-12:
                    max_rel_err = 0.0
                    alphas = []
                    for k in range(T):
                        gk = fields[k, 0].astype(np.float64)
                        a = float(np.sum((gk - s0) * diff) / denom)
                        recon = (1.0 - a) * s0 + a * s1
                        rel = float(np.linalg.norm(gk - recon) / max(np.linalg.norm(gk), 1e-12))
                        max_rel_err = max(max_rel_err, rel)
                        alphas.append(round(a, 5))
                    if max_rel_err < 1e-4:
                        sibling_match = {
                            "reference_file": str(sibling),
                            "max_linear_blend_rel_l2_err": max_rel_err,
                            "blend_alphas_sample": [alphas[0], alphas[1], alphas[T // 2], alphas[-2], alphas[-1]],
                            "confirmed_interpolated_from_sibling": True,
                        }
                        is_synthetic_low_rank = True

    return {
        "inlet_u_star_mean": round(inlet_u_mean, 5),
        "p_star_abs_p99": round(p_p99, 4),
        "u_star_mean_first_mid_last": [u_frame_means[0], u_frame_means[T // 2], u_frame_means[-1]],
        "scale_plausible": scale_plausible,
        "provenance_mismatch": provenance_mismatch,
        "temporal_svd": svd_info,
        "is_synthetic_2frame_interpolation": is_synthetic_low_rank,
        "sibling_interpolation_proof": sibling_match,
        "authenticity_pass": bool(scale_plausible and (not provenance_mismatch) and (not is_synthetic_low_rank)),
        "notes": provenance_notes,
    }


def audit_hdf5_dataset(h5_path: Path) -> dict[str, Any]:
    """Run complete Tier 2 verification and forensic audit on a World Model HDF5 file."""
    h5_path = Path(h5_path).resolve()
    with h5py.File(str(h5_path), "r") as f:
        fields = f["flow_fields"][:]  # (T, 6, Nz, Ny, Nx)
        x_star = f["coordinates/x_star"][:]
        y_star = f["coordinates/y_star"][:]
        z_star = f["coordinates/z_star"][:]
        t_phys = f["time_physical_s"][:]
        t_star = f["time_nondim_ctu"][:]
        step_ids = f["step_indices"][:]
        attrs = {}
        for k, v in f.attrs.items():
            if isinstance(v, np.ndarray):
                attrs[k] = v.tolist()
            elif isinstance(v, (np.floating, np.integer)):
                attrs[k] = v.item()
            else:
                attrs[k] = str(v)

    T, C, Nz, Ny, Nx = fields.shape
    ch_names = ["u_star", "v_cross_star", "w_span_star", "p_star", "sdf_star", "solid_mask"]

    # (a) Finiteness & Solid-Body Rigidity
    channel_stats: dict[str, Any] = {}
    all_finite = True
    for c, cname in enumerate(ch_names):
        arr = fields[:, c]
        n_nan = int(np.isnan(arr).sum())
        n_inf = int(np.isinf(arr).sum())
        finite_c = (n_nan == 0) and (n_inf == 0)
        all_finite = all_finite and finite_c
        channel_stats[cname] = {
            "finite": finite_c,
            "nan_count": n_nan,
            "inf_count": n_inf,
            "min": round(float(np.min(arr)), 6),
            "max": round(float(np.max(arr)), 6),
            "mean": round(float(np.mean(arr)), 6),
            "std": round(float(np.std(arr)), 6),
        }

    solid_bool_all = fields[:, 5] > 0.5
    solid_3d = solid_bool_all[0]
    mask_constant_t = bool(np.all(solid_bool_all == solid_bool_all[0:1]))
    solid_max_abs = {
        cname: float(np.max(np.abs(fields[:, c][solid_bool_all])))
        for c, cname in enumerate(ch_names[:4])
    }
    rigidity_pass = bool(
        all_finite
        and mask_constant_t
        and all(v == 0.0 for v in solid_max_abs.values())
    )

    check_a = {
        "all_finite_zero_nans": all_finite,
        "solid_mask_constant_across_T": mask_constant_t,
        "solid_voxel_count_per_frame": int(solid_3d.sum()),
        "solid_volume_fraction_pct": round(100.0 * float(solid_3d.mean()), 4),
        "max_abs_solid_interior": solid_max_abs,
        "strict_solid_rigidity_pass": rigidity_pass,
        "channel_stats": channel_stats,
    }

    # Sample up to 3 representative frames (first, middle, last) for heavy 3D differential diagnostics
    sample_indices = sorted(set([0, T // 2, T - 1]))

    # (b) 3D Q-Criterion Coherent Vortex Structures
    wake_mask_3d = (~solid_3d) & (x_star[None, None, :] > 0.5) & (np.abs(y_star[None, :, None]) < 2.0)
    q_per_frame: list[dict[str, Any]] = []
    for ti in sample_indices:
        u = fields[ti, 0]
        v = fields[ti, 1]
        w = fields[ti, 2]
        q = compute_q_criterion_3d(u, v, w, x_star, y_star, z_star, solid_mask=solid_3d)
        div = compute_divergence_3d(u, v, w, x_star, y_star, z_star, solid_mask=solid_3d)
        ox, oy, oz = compute_vorticity_3d(u, v, w, x_star, y_star, z_star, solid_mask=solid_3d)

        qf = q[np.isfinite(q)]
        qw = q[wake_mask_3d & np.isfinite(q)]
        df = div[np.isfinite(div)]
        q_rms_w = float(np.sqrt(np.mean(qw ** 2)))
        ox_rms = float(np.sqrt(np.mean(ox[wake_mask_3d & np.isfinite(ox)] ** 2)))
        oy_rms = float(np.sqrt(np.mean(oy[wake_mask_3d & np.isfinite(oy)] ** 2)))
        oz_rms = float(np.sqrt(np.mean(oz[wake_mask_3d & np.isfinite(oz)] ** 2)))

        q_per_frame.append(
            {
                "frame_idx": int(ti),
                "step_index": int(step_ids[ti]),
                "t_physical_s": round(float(t_phys[ti]), 4),
                "q_positive_fraction_fluid_pct": round(100.0 * float(np.mean(qf > 0.0)), 3),
                "q_positive_fraction_wake_pct": round(100.0 * float(np.mean(qw > 0.0)), 3),
                "q_wake_rms": round(q_rms_w, 4),
                "q_wake_p90": round(float(np.percentile(qw, 90)), 4),
                "q_wake_p95": round(float(np.percentile(qw, 95)), 4),
                "q_wake_p99": round(float(np.percentile(qw, 99)), 4),
                "q_fluid_max": round(float(np.max(qf)), 4),
                "q_isosurface_gt_0p1_rms_wake_pct": round(100.0 * float(np.mean(qw > 0.1 * q_rms_w)), 3),
                "q_isosurface_gt_0p5_rms_wake_pct": round(100.0 * float(np.mean(qw > 0.5 * q_rms_w)), 3),
                "div_u_star_rms": round(float(np.sqrt(np.mean(df ** 2))), 4),
                "vorticity_3d_rms_xyz": [round(ox_rms, 4), round(oy_rms, 4), round(oz_rms, 4)],
                "streamwise_to_spanwise_vorticity_ratio": round(ox_rms / max(oz_rms, 1e-12), 4),
            }
        )

    q_pos_wake_mean = float(np.mean([x["q_positive_fraction_wake_pct"] for x in q_per_frame]))
    q_structures_detected = bool(q_pos_wake_mean >= 5.0 and all(x["q_wake_p99"] > 0.0 for x in q_per_frame))
    check_b = {
        "q_positive_fraction_wake_mean_pct": round(q_pos_wake_mean, 3),
        "coherent_vortex_structures_present": q_structures_detected,
        "sampled_frames": q_per_frame,
    }

    # (c) Wake Spatial Energy Spectrum (evaluate on final frame & middle frame)
    ti_spec = sample_indices[-1]
    spec_1d = _compute_1d_wake_spectrum(
        fields[ti_spec, 0], fields[ti_spec, 1], fields[ti_spec, 2], x_star, y_star
    )
    spec_3d = _compute_3d_isotropic_spectrum(
        fields[ti_spec, 0], fields[ti_spec, 1], fields[ti_spec, 2], x_star, y_star, z_star
    )
    # Inertial-subrange slope check (typical 3D URANS/SAS/LES falls in [-3.5, -1.0] depending on SGS dissipation;
    # Kolmogorov -5/3 = -1.667; pass strict inertial range [-2.5, -1.1] and zero high-k aliasing)
    slope_1d = spec_1d["inertial_slope_1d_kx"]
    slope_3d = spec_3d["isotropic_3d_slope"]
    slope_near_kolmogorov = bool(
        (np.isfinite(slope_1d) and (-2.5 <= slope_1d <= -1.0))
        or (np.isfinite(slope_3d) and (-2.5 <= slope_3d <= -1.0))
    )
    check_c = {
        "evaluated_frame_idx": int(ti_spec),
        "streamwise_1d_spectrum": spec_1d,
        "isotropic_3d_spectrum": spec_3d,
        "inertial_slope_pass_near_minus_5_3": slope_near_kolmogorov,
        "no_high_k_aliasing_blowup": spec_1d["no_high_k_aliasing_blowup"],
        "spectral_gate_pass": bool(slope_near_kolmogorov and spec_1d["no_high_k_aliasing_blowup"]),
    }

    # (d) Spanwise Two-Point Autocorrelation R_uu(dz*)
    check_d = _compute_spanwise_autocorrelation(
        fields[ti_spec, 0], fields[ti_spec, 1], fields[ti_spec, 2], x_star, y_star, z_star
    )
    check_d["evaluated_frame_idx"] = int(ti_spec)

    # (e) Physical Scale, Provenance & Temporal Rank Authenticity
    check_e = _temporal_svd_and_provenance_audit(h5_path, fields, attrs, x_star)

    # Overall Tier 2 Verdict
    gate_failures: list[str] = []
    if not rigidity_pass:
        gate_failures.append("FAIL_A_FINITENESS_OR_SOLID_RIGIDITY")
    if not q_structures_detected:
        gate_failures.append("FAIL_B_Q_CRITERION")
    if not check_c["no_high_k_aliasing_blowup"]:
        gate_failures.append("FAIL_C_HIGH_K_ALIASING")
    if not check_c["inertial_slope_pass_near_minus_5_3"]:
        gate_failures.append("FAIL_C_INERTIAL_SLOPE_DISSIPATED_OR_DISTORTED")
    if not check_d["is_true_3d_decorrelated"]:
        gate_failures.append("FAIL_D_SPANWISE_DECORRELATION")
    if not check_e["authenticity_pass"]:
        gate_failures.append("FAIL_E_PROVENANCE_SCALE_OR_SYNTHETIC_INTERPOLATION")

    overall_status = "PASS" if len(gate_failures) == 0 else "FAIL"

    return {
        "audited_at": datetime.datetime.now().isoformat(),
        "dataset_path": str(h5_path),
        "file_size_mb": round(h5_path.stat().st_size / (1024 ** 2), 2),
        "tensor_shape": [int(T), int(C), int(Nz), int(Ny), int(Nx)],
        "dtype": str(fields.dtype),
        "metadata_attrs": attrs,
        "time_range_physical_s": [round(float(t_phys[0]), 4), round(float(t_phys[-1]), 4)],
        "time_range_nondim_ctu": [round(float(t_star[0]), 4), round(float(t_star[-1]), 4)],
        "tier2_checks": {
            "a_finiteness_and_solid_rigidity": check_a,
            "b_q_criterion_3d_vortex_structures": check_b,
            "c_wake_spatial_energy_spectrum": check_c,
            "d_spanwise_autocorrelation_3d": check_d,
            "e_provenance_scale_and_temporal_authenticity": check_e,
        },
        "gate_summary": {
            "a_structural_rigidity": "PASS" if rigidity_pass else "FAIL",
            "b_q_criterion_coherent_vortices": "PASS" if q_structures_detected else "FAIL",
            "c_wake_energy_spectrum": "PASS" if check_c["spectral_gate_pass"] else "FAIL",
            "d_spanwise_3d_decorrelation": "PASS" if check_d["is_true_3d_decorrelated"] else "FAIL",
            "e_physical_provenance_and_authenticity": "PASS" if check_e["authenticity_pass"] else "FAIL",
        },
        "failed_gates": gate_failures,
        "overall_tier2_certification": overall_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run genuine Tier 2 3D flow-field certification on HDF5 dataset.")
    parser.add_argument(
        "--h5",
        default=r"E:\cfd_3d\03_world_model_dataset\c04_wm_25steps_golden.h5",
        help="Path to target HDF5 file",
    )
    parser.add_argument(
        "--compare-h5",
        default=r"E:\cfd_3d\03_world_model_dataset\test_re5k_step1000.h5",
        help="Optional secondary genuine HDF5 file for baseline comparison",
    )
    parser.add_argument(
        "--out-json",
        default=r"E:\cfd_3d\03_world_model_dataset\tier2_c04_wm_25steps_golden_certification.json",
        help="Output JSON report path",
    )
    args = parser.parse_args()

    primary_report = audit_hdf5_dataset(Path(args.h5))
    if args.compare_h5 and Path(args.compare_h5).is_file():
        primary_report["baseline_developed_frame_comparison"] = audit_hdf5_dataset(Path(args.compare_h5))

    out_p = Path(args.out_json).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(primary_report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[SAVED] Tier 2 Certification Report -> {out_p}")
    print("Primary Gate Summary:", json.dumps(primary_report["gate_summary"], indent=2))
    print("Overall Verdict:", primary_report["overall_tier2_certification"], "| Failed:", primary_report["failed_gates"])
    if "baseline_developed_frame_comparison" in primary_report:
        cmp_r = primary_report["baseline_developed_frame_comparison"]
        print("Comparison (test_re5k_step1000.h5) Gate Summary:", json.dumps(cmp_r["gate_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
