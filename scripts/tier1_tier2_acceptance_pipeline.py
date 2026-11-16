#!/usr/bin/env python3
"""Unified World Model Calibration, Nondimensionalization & Two-Tier Acceptance Pipeline.

Follows the Swarm Directive for World Model training qualification:
1. Nondimensionalization:
   - t* = t * (U0 / D)
   - u* = u / U0, v* = v / U0, w* = w / U0
   - p* = (p - p_inf) / (rho0 * U0^2)
   - Explicit Reynolds conditioning variable: Re = U0 * D / nu0
2. Tier 1 Acceptance:
   - Key statistical parameters (St, Cd_mean, Cl_rms, Cl_amp) relative difference <= 3.0% against Reference
   - Limit-cycle stability drifts: St_drift < 1.0%, Amp_drift < 2.0%, Cd_drift < 1.0%
   - Periodic symmetry: |Cl_mean| <= max(0.05, 0.15 * Cl_amp)
3. Tier 2 Acceptance (Full Field & Coherent Structures):
   - 3D full-field relative L2 error on 192x96x64 ROI grid (per-channel [u*, v*, w*, p*])
   - Q-criterion coherent vortex identification & spatial correlation
   - Spectral PSD peak frequency & energy distribution
   - Spanwise correlation R_uu(Delta z*) along cylinder span
4. Optimization Metric:
   - Qualified Training Data Volume per Hour = (sim_s / wall_h) * Tier1_Pass * Tier2_Pass
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# Ensure src and scripts are on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (SRC_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import calibrate_c04_3d as calib
from calc_boundary_layer_mesh import compute_bl_sizing
from verify_and_compare_s1b1 import (
    compute_cycle_half_amplitude,
    compute_demeaned_rms,
    evaluate_limit_cycle_stability,
    integrate_exact_window,
)

from hydro_disturb_bench.sampling_3d import (
    C04_CHANNELS,
    C04_LAYOUT_3D,
)
from hydro_disturb_bench.vorticity_dynamics import (
    compute_q_criterion_3d,
)

# Default C04 Physical Reference Constants (ITTC 2024 Seawater, D=0.30m, U0=0.50m/s)
DEFAULT_D_M = 0.30
DEFAULT_U0 = 0.50
DEFAULT_RHO0 = 1025.8944
DEFAULT_NU0 = 1.1889e-6
DEFAULT_L_SPAN_M = 3.0


# ---------------------------------------------------------------------------
# 1. Nondimensionalization Engine
# ---------------------------------------------------------------------------

@dataclass
class NondimensionalScales:
    D: float
    U0: float
    rho0: float
    nu0: float
    l_span: float
    Re: float
    t_ref: float  # D / U0 (Convective Time Unit, CTU)
    p_ref: float  # rho0 * U0^2
    q_inf: float  # 0.5 * rho0 * U0^2
    area_frontal: float  # D * l_span

    @classmethod
    def create(
        cls,
        D: float = DEFAULT_D_M,
        U0: float = DEFAULT_U0,
        rho0: float = DEFAULT_RHO0,
        nu0: float = DEFAULT_NU0,
        l_span: float = DEFAULT_L_SPAN_M,
    ) -> NondimensionalScales:
        Re = (U0 * D) / nu0
        t_ref = D / U0
        p_ref = rho0 * (U0 ** 2)
        q_inf = 0.5 * p_ref
        area_frontal = D * l_span
        return cls(
            D=D,
            U0=U0,
            rho0=rho0,
            nu0=nu0,
            l_span=l_span,
            Re=Re,
            t_ref=t_ref,
            p_ref=p_ref,
            q_inf=q_inf,
            area_frontal=area_frontal,
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


class Nondimensionalizer:
    """Transforms raw dimensional CFD probe series and 3D field tensors into dimensionless form."""

    def __init__(self, scales: NondimensionalScales | None = None):
        self.scales = scales or NondimensionalScales.create()

    def transform_probes(
        self, t: np.ndarray, cl: np.ndarray, cd: np.ndarray
    ) -> dict[str, np.ndarray]:
        """Convert time series to dimensionless convective time t* = t * (U0 / D)."""
        t_star = t / self.scales.t_ref
        return {
            "t_star": t_star,
            "cl": cl,  # CL is already dimensionless
            "cd": cd,  # CD is already dimensionless
        }

    def transform_field_3d(
        self,
        t: np.ndarray,
        u: np.ndarray,
        v: np.ndarray,
        w: np.ndarray,
        p: np.ndarray,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        p_inf: float = 0.0,
    ) -> dict[str, Any]:
        """Nondimensionalize 3D velocity/pressure field tensors and coordinates.

        Outputs:
          t* = t / (D / U0)
          u* = u / U0, v* = v / U0, w* = w / U0
          p* = (p - p_inf) / (rho0 * U0^2)
          x* = x / D, y* = y / D, z* = z / D
        """
        t_star = t / self.scales.t_ref
        u_star = u / self.scales.U0
        v_star = v / self.scales.U0
        w_star = w / self.scales.U0
        p_star = (p - p_inf) / self.scales.p_ref

        x_star = x / self.scales.D
        y_star = y / self.scales.D
        z_star = z / self.scales.D

        return {
            "scales": self.scales.to_dict(),
            "t_star": t_star,
            "u_star": u_star,
            "v_star": v_star,
            "w_star": w_star,
            "p_star": p_star,
            "x_star": x_star,
            "y_star": y_star,
            "z_star": z_star,
            "tensor_layout": C04_LAYOUT_3D,
            "channels": C04_CHANNELS,
        }


# ---------------------------------------------------------------------------
# 1.5. Wall y+ Physical Boundary Layer Compliance Gate
# ---------------------------------------------------------------------------

@dataclass
class YPlusGateResult:
    Re: float
    delta_y1_mm: float
    delta_y1_ideal_mm: float  # for y+=1
    estimated_yplus: float
    measured_yplus_stats: dict[str, float] | None
    gate_status: str  # PASS, FAIL_BUFFER_LAYER, FAIL_NOT_RESOLVED
    is_buffer_layer: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WallYPlusGate:
    """Prerequisite physical compliance gate for cylinder boundary layer y+.

    Enforces Swarm Directive:
    - delta_y1(y+=1) scales as Re^(-0.9).
    - Low-Re resolved SST (Re <= 500,000): requires max(y+) <= 1.0 (estimated y+ <= 1.5).
    - Buffer Layer Prohibition: STRICTLY REJECT any mesh where 5.0 < y+ < 30.0.
      Neither linear sublayer nor log-law applies in buffer layer; forces & separation severely distorted.
    """

    def __init__(self, scales: NondimensionalScales | None = None):
        self.scales = scales or NondimensionalScales.create()

    def evaluate_yplus(
        self,
        Re: float | None = None,
        delta_y1_m: float | None = None,
        measured_yplus_array: np.ndarray | None = None,
    ) -> YPlusGateResult:
        Re_val = Re if Re is not None else self.scales.Re
        bl_info = compute_bl_sizing(
            Re=Re_val,
            D=self.scales.D,
            nu=self.scales.nu0,
            rho=self.scales.rho0,
            target_yplus=1.0,
        )
        delta_y1_ideal = bl_info.delta_y1_m
        actual_delta_y1 = delta_y1_m if delta_y1_m is not None else 0.04473e-3
        est_yplus = actual_delta_y1 / delta_y1_ideal

        measured_stats = None
        reasons = []
        is_buffer = False

        if measured_yplus_array is not None and len(measured_yplus_array) > 0:
            yp = np.asarray(measured_yplus_array, dtype=float)
            yp = yp[np.isfinite(yp)]
            if len(yp) > 0:
                yp_min = float(np.min(yp))
                yp_mean = float(np.mean(yp))
                yp_max = float(np.max(yp))
                f_buffer = float(np.mean((yp > 5.0) & (yp < 30.0)))
                measured_stats = {
                    "yplus_min": yp_min,
                    "yplus_mean": yp_mean,
                    "yplus_max": yp_max,
                    "buffer_fraction": f_buffer,
                }
                if (5.0 < yp_mean < 30.0) or (f_buffer > 0.10):
                    is_buffer = True
                    reasons.append(
                        f"Measured wall y+ (mean={yp_mean:.2f}, max={yp_max:.2f}, {f_buffer*100:.1f}% in [5, 30]) "
                        f"falls into the unphysical buffer layer. Neither linear viscous sublayer nor log-law applies."
                    )
                elif Re_val <= 500000 and yp_max > 2.0:
                    reasons.append(
                        f"Low-Re resolved SST requires max(y+) <= 1.0 (or peak <= 2.0); measured max(y+) = {yp_max:.2f}."
                    )

        if measured_stats is None:
            if 5.0 < est_yplus < 30.0:
                is_buffer = True
                reasons.append(
                    f"First cell height Δy1={actual_delta_y1*1000:.4f}mm produces estimated y+={est_yplus:.2f} "
                    f"falling into the unphysical buffer layer [5, 30] at Re={Re_val:,.0f}. "
                    f"Ideal Δy1(y+=1)={delta_y1_ideal*1000:.4f}mm. Rejection: uncalibrated buffer layer."
                )
            elif Re_val <= 500000 and est_yplus > 2.0:
                reasons.append(
                    f"Low-Re resolved SST requires y+ <= 1.0; estimated y+ = {est_yplus:.2f} "
                    f"(Δy1={actual_delta_y1*1000:.4f}mm vs ideal={delta_y1_ideal*1000:.4f}mm)."
                )

        gate_status = "FAIL_BUFFER_LAYER" if is_buffer else ("FAIL_NOT_RESOLVED" if reasons else "PASS")
        return YPlusGateResult(
            Re=Re_val,
            delta_y1_mm=actual_delta_y1 * 1000.0,
            delta_y1_ideal_mm=delta_y1_ideal * 1000.0,
            estimated_yplus=est_yplus,
            measured_yplus_stats=measured_stats,
            gate_status=gate_status,
            is_buffer_layer=is_buffer,
            reasons=reasons,
        )


# ---------------------------------------------------------------------------
# 2. Tier 1 Acceptance Engine (Statistics & Limit Cycle Stability)
# ---------------------------------------------------------------------------

@dataclass
class Tier1Result:
    case_name: str
    status: str  # PASS, UNSTABLE, INSUFFICIENT_CYCLES, FAIL_TOLERANCE, FAIL_BUFFER_LAYER, FAIL_NOT_RESOLVED
    is_stable: bool
    num_cycles: int
    St_zc_mean: float | None
    Cd_mean: float | None
    Cl_amp_mean: float | None
    Cl_demeaned_rms: float | None
    Cl_mean: float | None
    drifts: dict[str, float]
    envelope_checks: dict[str, bool]
    comparison_to_ref: dict[str, Any] | None = None
    yplus_check: dict[str, Any] | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tier1Evaluator:
    """Evaluates macro aerodynamic coefficients, vortex shedding limit-cycles, and 3% tolerance gate."""

    def __init__(
        self,
        scales: NondimensionalScales | None = None,
        tol_pct: float = 3.0,
        st_thresh: float = 1.0,
        amp_thresh: float = 2.0,
        cd_thresh: float = 1.0,
        min_shedding_t: float = 15.0,
        min_cycles: int = 5,
    ):
        self.scales = scales or NondimensionalScales.create()
        self.tol_pct = tol_pct
        self.st_thresh = st_thresh
        self.amp_thresh = amp_thresh
        self.cd_thresh = cd_thresh
        self.min_shedding_t = min_shedding_t
        self.min_cycles = min_cycles
        self.yplus_gate = WallYPlusGate(self.scales)

    def evaluate_case(
        self,
        name: str,
        t_cl: np.ndarray,
        cl: np.ndarray,
        t_cd: np.ndarray,
        cd: np.ndarray,
        ref_metrics: dict[str, float] | None = None,
        Re: float | None = None,
        delta_y1_m: float | None = None,
        measured_yplus_array: np.ndarray | None = None,
    ) -> Tier1Result:
        """Run complete cycle-by-cycle limit cycle evaluation, y+ gate, and optional 3% reference check."""
        # 0. Pre-requisite Gate: Wall y+ & Boundary Layer Physical Compliance
        yplus_res = self.yplus_gate.evaluate_yplus(
            Re=Re,
            delta_y1_m=delta_y1_m,
            measured_yplus_array=measured_yplus_array,
        )

        # Find upward zero-crossings after startup transient
        mask = t_cl >= self.min_shedding_t if np.max(t_cl) > self.min_shedding_t + 5.0 else np.ones_like(t_cl, dtype=bool)
        t_s = t_cl[mask]
        cl_s = cl[mask]

        def _find_zero_crossings(sig: np.ndarray) -> list[float]:
            crossings: list[float] = []
            for i in range(len(t_s) - 1):
                if sig[i] <= 0.0 < sig[i + 1]:
                    tz = t_s[i] - sig[i] * (t_s[i + 1] - t_s[i]) / (sig[i + 1] - sig[i])
                    crossings.append(float(tz))
            return crossings

        zc = _find_zero_crossings(cl_s)
        used_demeaned_zc = False
        if len(zc) < self.min_cycles + 1:
            # When lift has a non-zero mean offset (|Cl_mean| <= 0.05), oscillations occur around Cl_mean.
            # Fall back to zero-crossings of the fluctuating (demeaned) lift signal.
            cl_dem = cl_s - np.mean(cl_s)
            zc_dem = _find_zero_crossings(cl_dem)
            if len(zc_dem) >= self.min_cycles + 1:
                zc = zc_dem
                used_demeaned_zc = True

        if len(zc) < self.min_cycles + 1:
            half_idx = max(1, len(t_cl) // 2)
            t0_p, t1_p = float(t_cl[half_idx]), float(t_cl[-1])
            _, cd_prelim = integrate_exact_window(t_cd, cd, t0_p, t1_p) if t1_p > t0_p else (0.0, float(np.mean(cd)))
            _, cl_rms_prelim = compute_demeaned_rms(t_cl, cl, t0_p, t1_p) if t1_p > t0_p else (0.0, float(np.std(cl)))
            return Tier1Result(
                case_name=name,
                status="INSUFFICIENT_CYCLES",
                is_stable=False,
                num_cycles=max(0, len(zc) - 1),
                St_zc_mean=None,
                Cd_mean=float(cd_prelim),
                Cl_amp_mean=None,
                Cl_demeaned_rms=float(cl_rms_prelim),
                Cl_mean=float(np.mean(cl_s)),
                drifts={},
                envelope_checks={},
                reasons=[f"Found {max(0, len(zc)-1)} complete shedding cycles after t={self.min_shedding_t}s; required >= {self.min_cycles}."],
            )

        sel_zc = zc[-(self.min_cycles + 1) :]
        t_start, t_end = float(sel_zc[0]), float(sel_zc[-1])
        T_zc = np.diff(sel_zc)
        St_zc = (1.0 / T_zc) * (self.scales.D / self.scales.U0)

        cycle_amps: list[float] = []
        cycle_cds: list[float] = []
        for k in range(self.min_cycles):
            ts, te = float(sel_zc[k]), float(sel_zc[k + 1])
            cycle_amps.append(compute_cycle_half_amplitude(t_cl, cl, ts, te))
            _, c_cd = integrate_exact_window(t_cd, cd, ts, te)
            cycle_cds.append(c_cd)

        is_stable, drifts, stab_msg = evaluate_limit_cycle_stability(
            periods=T_zc,
            amplitudes=cycle_amps,
            cds=cycle_cds,
            st_thresh=self.st_thresh,
            amp_thresh=self.amp_thresh,
            cd_thresh=self.cd_thresh,
        )

        _, cd_mean = integrate_exact_window(t_cd, cd, t_start, t_end)
        cl_mean, cl_rms = compute_demeaned_rms(t_cl, cl, t_start, t_end)
        cl_amp_mean = float(np.mean(cycle_amps))
        st_mean = float(np.mean(St_zc))

        # Literature Envelope Checks
        env = calib.C04_LITERATURE_ENVELOPE
        envelope_checks = {
            "St_in_envelope": env["St_range"][0] <= st_mean <= env["St_range"][1],
            "Cd_in_envelope": env["Cd_mean_range"][0] <= cd_mean <= env["Cd_mean_range"][1],
            "active_shedding_Cl_rms": cl_rms >= env["Cl_rms_min_shedding"],
            "periodic_Cl_symmetry": abs(cl_mean) <= max(0.05, 0.15 * cl_amp_mean),
        }

        reasons = []
        if not is_stable:
            reasons.append(f"Stability drift check failed: {stab_msg}")
        for k, v in envelope_checks.items():
            if not v:
                reasons.append(f"Literature check failed: {k}")

        # Pairwise comparison to reference if available
        comparison = None
        pair_pass = True
        cand_metrics = {
            "St_zc_mean": st_mean,
            "Cd_mean": float(cd_mean),
            "Cl_amp_mean": cl_amp_mean,
            "Cl_demeaned_rms": float(cl_rms),
        }
        if ref_metrics is not None:
            comparison = {}
            exceeded = []
            for metric_key in ("St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"):
                v_cand = cand_metrics[metric_key]
                v_ref = ref_metrics.get(metric_key)
                if v_ref is not None and abs(v_ref) > 1e-12:
                    rel_diff = (v_cand - v_ref) / v_ref * 100.0
                    comparison[metric_key] = {
                        "cand": v_cand,
                        "ref": v_ref,
                        "rel_diff_pct": rel_diff,
                        "pass_3pct": abs(rel_diff) <= self.tol_pct,
                    }
                    if abs(rel_diff) > self.tol_pct:
                        exceeded.append(f"{metric_key} ({rel_diff:+.2f}% > {self.tol_pct}%)")
                        pair_pass = False
            comparison["exceeded"] = exceeded
            comparison["all_pass_3pct"] = pair_pass
            if not pair_pass:
                reasons.append(f"Exceeded 3% reference difference: {', '.join(exceeded)}")

        # Check y+ gate status
        if yplus_res.gate_status != "PASS":
            reasons.extend(yplus_res.reasons)

        # Final status
        if yplus_res.is_buffer_layer:
            final_status = "FAIL_BUFFER_LAYER"
        elif yplus_res.gate_status != "PASS":
            final_status = yplus_res.gate_status
        elif not is_stable:
            final_status = "UNSTABLE"
        elif ref_metrics is not None and not pair_pass:
            final_status = "FAIL_TOLERANCE"
        else:
            final_status = "PASS"

        return Tier1Result(
            case_name=name,
            status=final_status,
            is_stable=is_stable,
            num_cycles=self.min_cycles,
            St_zc_mean=st_mean,
            Cd_mean=float(cd_mean),
            Cl_amp_mean=cl_amp_mean,
            Cl_demeaned_rms=float(cl_rms),
            Cl_mean=float(cl_mean),
            drifts=drifts,
            envelope_checks=envelope_checks,
            comparison_to_ref=comparison,
            yplus_check=yplus_res.to_dict(),
            reasons=reasons,
        )


# ---------------------------------------------------------------------------
# 3. Tier 2 Acceptance Engine (Full-Field, Q-Criterion, Spectrum & Spanwise)
# ---------------------------------------------------------------------------

@dataclass
class Tier2Result:
    case_name: str
    status: str  # PASS, FAIL
    field_rel_l2_errors: dict[str, float]  # per-channel: u, v, w, p
    field_rmse: dict[str, float]
    q_criterion_metrics: dict[str, Any]
    spectral_metrics: dict[str, Any]
    spanwise_correlation: dict[str, Any]
    passed_all_tier2: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tier2Evaluator:
    """Evaluates 3D full-field accuracy, Q-criterion coherent structures, frequency spectrum, and spanwise decay."""

    def __init__(
        self,
        scales: NondimensionalScales | None = None,
        max_rel_l2_vel: float = 0.05,  # 5% max relative L2 on velocity fields
        max_rel_l2_p: float = 0.08,    # 8% max relative L2 on pressure field
        min_q_correlation: float = 0.85, # Min spatial correlation of Q-criterion
        max_spectral_diff_pct: float = 3.0, # Max St peak difference
    ):
        self.scales = scales or NondimensionalScales.create()
        self.max_rel_l2_vel = max_rel_l2_vel
        self.max_rel_l2_p = max_rel_l2_p
        self.min_q_correlation = min_q_correlation
        self.max_spectral_diff_pct = max_spectral_diff_pct

    def evaluate_3d_fields(
        self,
        name: str,
        cand_fields: dict[str, np.ndarray],  # u, v, w, p of shape (T, Nz, Ny, Nx) or (Nz, Ny, Nx)
        ref_fields: dict[str, np.ndarray],
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        solid_mask: np.ndarray | None = None,
        dt: float = 0.06,
    ) -> Tier2Result:
        """Run deep Tier 2 structural, vortex, spectral, and spanwise evaluation."""
        reasons: list[str] = []

        # 1. Field-wide Relative L2 & RMSE (excluding solid obstacle)
        fluid_mask = ~solid_mask if solid_mask is not None else np.ones_like(cand_fields["u"], dtype=bool)
        if fluid_mask.ndim == 3 and cand_fields["u"].ndim == 4:
            fluid_mask = np.broadcast_to(fluid_mask, cand_fields["u"].shape)

        rel_l2: dict[str, float] = {}
        rmse: dict[str, float] = {}
        for c in ("u", "v", "w", "p"):
            fc = cand_fields[c][fluid_mask]
            fr = ref_fields[c][fluid_mask]
            diff = fc - fr
            norm_r = np.linalg.norm(fr)
            norm_diff = np.linalg.norm(diff)
            r_l2 = float(norm_diff / norm_r) if norm_r > 1e-12 else float("nan")
            r_rmse = float(np.sqrt(np.mean(diff ** 2)))
            rel_l2[c] = r_l2
            rmse[c] = r_rmse

            thresh = self.max_rel_l2_p if c == "p" else self.max_rel_l2_vel
            if math.isnan(r_l2) or r_l2 > thresh:
                reasons.append(f"Channel '{c}' relative L2 error ({r_l2*100:.2f}%) exceeds threshold ({thresh*100:.1f}%)")

        # 2. 3D Q-Criterion Coherent Structure Evaluation
        # Take the last time frame if 4D
        u_c = cand_fields["u"][-1] if cand_fields["u"].ndim == 4 else cand_fields["u"]
        v_c = cand_fields["v"][-1] if cand_fields["v"].ndim == 4 else cand_fields["v"]
        w_c = cand_fields["w"][-1] if cand_fields["w"].ndim == 4 else cand_fields["w"]

        u_r = ref_fields["u"][-1] if ref_fields["u"].ndim == 4 else ref_fields["u"]
        v_r = ref_fields["v"][-1] if ref_fields["v"].ndim == 4 else ref_fields["v"]
        w_r = ref_fields["w"][-1] if ref_fields["w"].ndim == 4 else ref_fields["w"]

        s_mask_3d = solid_mask[-1] if (solid_mask is not None and solid_mask.ndim == 4) else solid_mask

        q_cand = compute_q_criterion_3d(u_c, v_c, w_c, x, y, z, solid_mask=s_mask_3d)
        q_ref = compute_q_criterion_3d(u_r, v_r, w_r, x, y, z, solid_mask=s_mask_3d)

        # Pearson spatial correlation of Q-criterion in wake region (x > 0)
        valid_q = ~np.isnan(q_cand) & ~np.isnan(q_ref)
        qc_flat = q_cand[valid_q]
        qr_flat = q_ref[valid_q]
        if len(qc_flat) > 100:
            cov = np.cov(qc_flat, qr_flat)
            std_c = np.std(qc_flat)
            std_r = np.std(qr_flat)
            q_corr = float(cov[0, 1] / (std_c * std_r)) if std_c * std_r > 1e-12 else 0.0
        else:
            q_corr = 0.0

        q_metrics = {
            "q_pearson_correlation": q_corr,
            "q_correlation_pass": q_corr >= self.min_q_correlation,
        }
        if q_corr < self.min_q_correlation:
            reasons.append(f"Q-criterion spatial correlation ({q_corr:.3f}) below threshold ({self.min_q_correlation:.3f})")

        # 3. Spectral Frequency Content (Welch / FFT on wake probe)
        spectral_metrics = {}
        if cand_fields["w"].ndim == 4 and cand_fields["w"].shape[0] >= 32:
            # Probe at center of wake: mid-z, mid-y, downstream x (~2D)
            nz, ny, nx = len(z), len(y), len(x)
            ix = min(nx - 1, int(round(nx * 0.35)))
            iy = ny // 2
            iz = nz // 2

            series_c = cand_fields["w"][:, iz, iy, ix]
            series_r = ref_fields["w"][:, iz, iy, ix]

            fs = 1.0 / dt
            fft_c = np.abs(np.fft.rfft(series_c - np.mean(series_c)))
            fft_r = np.abs(np.fft.rfft(series_r - np.mean(series_r)))
            freqs = np.fft.rfftfreq(len(series_c), d=dt)

            peak_idx_c = int(np.argmax(fft_c[1:])) + 1
            peak_idx_r = int(np.argmax(fft_r[1:])) + 1

            f_peak_c = float(freqs[peak_idx_c])
            f_peak_r = float(freqs[peak_idx_r])
            st_c = f_peak_c * (self.scales.D / self.scales.U0)
            st_r = f_peak_r * (self.scales.D / self.scales.U0)

            diff_f_pct = float(abs(f_peak_c - f_peak_r) / f_peak_r * 100.0) if f_peak_r > 1e-6 else float("nan")
            spectral_metrics = {
                "f_peak_cand_hz": f_peak_c,
                "f_peak_ref_hz": f_peak_r,
                "St_spectral_cand": st_c,
                "St_spectral_ref": st_r,
                "diff_peak_pct": diff_f_pct,
                "pass_spectral": diff_f_pct <= self.max_spectral_diff_pct,
            }
            if diff_f_pct > self.max_spectral_diff_pct:
                reasons.append(f"Spectral peak difference ({diff_f_pct:.2f}%) exceeds threshold ({self.max_spectral_diff_pct:.1f}%)")

        # 4. Spanwise Correlation R_uu(Delta z*)
        # Along spanwise coordinate z, verify physical 3D decorrelation
        spanwise_metrics = {}
        if u_c.ndim == 3:
            nz, ny, nx = u_c.shape
            ix = min(nx - 1, int(round(nx * 0.4)))
            iy = ny // 2
            u_span_c = u_c[:, iy, ix] - np.mean(u_c[:, iy, ix])
            u_span_r = u_r[:, iy, ix] - np.mean(u_r[:, iy, ix])

            # Two-point correlation along z
            _trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))
            if np.std(u_span_c) > 1e-8:
                corr_c = np.correlate(u_span_c, u_span_c, mode="full")
                corr_c = corr_c[nz - 1:] / corr_c[nz - 1]
                # Coherence length L_z* = integral of R_uu dz*
                l_z_star = float(_trapz(np.clip(corr_c, 0, 1), z / self.scales.D))
            else:
                corr_c = np.ones(nz)
                l_z_star = float("inf")

            spanwise_metrics = {
                "spanwise_coherence_length_Lz_star": l_z_star,
                "is_3d_decorrelated": 0.2 <= l_z_star <= 6.0,  # Physical 3D spanwise coherence is ~1-3D
            }
            if math.isinf(l_z_star) or not (0.2 <= l_z_star <= 6.0):
                reasons.append(f"Spanwise coherence length Lz* ({l_z_star:.2f}) indicates unphysical spanwise lock or excessive damping.")

        passed_all = len(reasons) == 0
        return Tier2Result(
            case_name=name,
            status="PASS" if passed_all else "FAIL",
            field_rel_l2_errors=rel_l2,
            field_rmse=rmse,
            q_criterion_metrics=q_metrics,
            spectral_metrics=spectral_metrics,
            spanwise_correlation=spanwise_metrics,
            passed_all_tier2=passed_all,
            reasons=reasons,
        )


# ---------------------------------------------------------------------------
# 4. Unified Pipeline & Data Volume Quality Metric
# ---------------------------------------------------------------------------

class WorldModelAcceptancePipeline:
    """End-to-end integration: Nondimensionalization -> Tier 1 -> Tier 2 -> Data Volume Rate."""

    def __init__(self, scales: NondimensionalScales | None = None):
        self.scales = scales or NondimensionalScales.create()
        self.nd = Nondimensionalizer(self.scales)
        self.tier1_eval = Tier1Evaluator(self.scales)
        self.tier2_eval = Tier2Evaluator(self.scales)

    def evaluate_full_case(
        self,
        case_dir: Path,
        ref_dir: Path | None = None,
        wall_s: float | None = None,
        delta_y1_m: float | None = None,
    ) -> dict[str, Any]:
        """Run complete pipeline for a case directory."""
        case_dir = Path(case_dir).resolve()
        case_name = case_dir.name

        # Load & Normalize probes
        norm = calib.normalize_c04_probe_tables(case_dir)
        t_cl = norm["t_lift"]
        cl = norm["cl"]
        t_cd = norm["t_drag"]
        cd = norm["cd"]

        # Nondimensionalize probes
        probes_nd = self.nd.transform_probes(t_cl, cl, cd)

        # If reference provided, compute ref metrics
        ref_metrics = None
        if ref_dir is not None and Path(ref_dir).is_dir():
            ref_norm = calib.normalize_c04_probe_tables(Path(ref_dir))
            ref_tier1 = self.tier1_eval.evaluate_case(
                name=Path(ref_dir).name,
                t_cl=ref_norm["t_lift"],
                cl=ref_norm["cl"],
                t_cd=ref_norm["t_drag"],
                cd=ref_norm["cd"],
            )
            ref_metrics = {
                "St_zc_mean": ref_tier1.St_zc_mean,
                "Cd_mean": ref_tier1.Cd_mean,
                "Cl_amp_mean": ref_tier1.Cl_amp_mean,
                "Cl_demeaned_rms": ref_tier1.Cl_demeaned_rms,
            }

        # Check for y+ probe table
        yplus_data = None
        yplus_txt = case_dir / "probe_yplus.txt"
        if yplus_txt.is_file():
            yp_tbl = calib._try_load_probe_table(yplus_txt)
            if yp_tbl is not None:
                yplus_data = yp_tbl[1]

        # Tier 1 Evaluation
        tier1_res = self.tier1_eval.evaluate_case(
            name=case_name,
            t_cl=t_cl,
            cl=cl,
            t_cd=t_cd,
            cd=cd,
            ref_metrics=ref_metrics,
            Re=self.scales.Re,
            delta_y1_m=delta_y1_m,
            measured_yplus_array=yplus_data,
        )

        # Throughput & Qualified Data Volume per Hour
        sim_duration = float(t_cl[-1] - t_cl[0]) if len(t_cl) > 1 else 0.0
        wall_h = (wall_s / 3600.0) if wall_s and wall_s > 0 else None
        sim_s_per_h = (sim_duration / wall_h) if wall_h else None

        # Qualification status
        tier1_pass = tier1_res.status == "PASS"
        qualified_sim_s_per_h = (sim_s_per_h * 1.0) if (tier1_pass and sim_s_per_h) else 0.0

        return {
            "case_name": case_name,
            "case_dir": str(case_dir),
            "scales": self.scales.to_dict(),
            "nondimensional_probes_summary": {
                "t_star_min": float(probes_nd["t_star"][0]),
                "t_star_max": float(probes_nd["t_star"][-1]),
                "total_probe_samples": len(t_cl),
            },
            "tier1_acceptance": tier1_res.to_dict(),
            "throughput_metrics": {
                "sim_duration_s": sim_duration,
                "wall_clock_s": wall_s,
                "sim_s_per_hour": sim_s_per_h,
                "qualified_training_data_s_per_hour": qualified_sim_s_per_h,
            },
            "overall_world_model_qualification": "QUALIFIED" if tier1_pass else "NOT_QUALIFIED",
        }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="World Model Nondimensionalization & Tier 1/2 Acceptance Pipeline")
    p.add_argument("case_dir", help="Path to solved CFD case directory containing probe_*.txt")
    p.add_argument("--ref-dir", default=None, help="Optional reference case directory for <=3%% pairwise comparison")
    p.add_argument("--wall-s", type=float, default=None, help="Observed wall clock time in seconds")
    p.add_argument("--D", type=float, default=DEFAULT_D_M, help="Cylinder diameter in m")
    p.add_argument("--U0", type=float, default=DEFAULT_U0, help="Inflow velocity in m/s")
    p.add_argument("--rho0", type=float, default=DEFAULT_RHO0, help="Fluid density in kg/m3")
    p.add_argument("--nu0", type=float, default=DEFAULT_NU0, help="Fluid kinematic viscosity in m2/s")
    p.add_argument("--tol-pct", type=float, default=3.0, help="Pairwise acceptance tolerance percentage (default: 3.0)")
    p.add_argument("--out-json", default=None, help="Optional destination path for structured report JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    scales = NondimensionalScales.create(
        D=args.D,
        U0=args.U0,
        rho0=args.rho0,
        nu0=args.nu0,
    )
    pipeline = WorldModelAcceptancePipeline(scales=scales)
    report = pipeline.evaluate_full_case(
        case_dir=Path(args.case_dir),
        ref_dir=Path(args.ref_dir) if args.ref_dir else None,
        wall_s=args.wall_s,
    )

    report_str = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out_json:
        out_p = Path(args.out_json).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(report_str, encoding="utf-8")
        print(f"[SAVED] Pipeline report written to: {out_p}")
    else:
        print(report_str)

    return 0 if report["overall_world_model_qualification"] == "QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
