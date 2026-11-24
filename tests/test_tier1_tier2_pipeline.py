#!/usr/bin/env python3
"""Unit and integration tests for Tier 1 & Tier 2 World Model Acceptance Pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _p in (SRC_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tier1_tier2_acceptance_pipeline import (
    DEFAULT_D_M,
    DEFAULT_NU0,
    DEFAULT_RHO0,
    DEFAULT_U0,
    Nondimensionalizer,
    NondimensionalScales,
    Tier1Evaluator,
    Tier2Evaluator,
    WorldModelAcceptancePipeline,
)


def test_nondimensional_scales_and_transform():
    scales = NondimensionalScales.create(
        D=DEFAULT_D_M,
        U0=DEFAULT_U0,
        rho0=DEFAULT_RHO0,
        nu0=DEFAULT_NU0,
    )
    assert np.isclose(scales.Re, 126167.38, rtol=1e-3)
    assert np.isclose(scales.t_ref, 0.60, atol=1e-6)
    assert np.isclose(scales.p_ref, 1025.8944 * 0.25, atol=1e-4)

    nd = Nondimensionalizer(scales)
    t = np.array([0.0, 0.6, 1.2, 1.8])
    cl = np.array([0.0, 0.1, -0.1, 0.0])
    cd = np.array([1.2, 1.2, 1.2, 1.2])

    p_trans = nd.transform_probes(t, cl, cd)
    assert np.allclose(p_trans["t_star"], [0.0, 1.0, 2.0, 3.0])


def test_tier1_evaluator_synthetic_stable_shedding():
    scales = NondimensionalScales.create(D=0.30, U0=0.50)
    evaluator = Tier1Evaluator(scales=scales, tol_pct=3.0, min_shedding_t=5.0, min_cycles=5)

    # Generate 30s of synthetic regular shedding: f = 0.3333 Hz -> T = 3.0s -> St = (1/3)*0.3/0.5 = 0.20
    t = np.linspace(0, 30, 3001)
    f_shed = 0.333333333333
    cl = 0.50 * np.sin(2.0 * np.pi * f_shed * t)
    cd = 1.20 + 0.10 * np.sin(4.0 * np.pi * f_shed * t)

    res = evaluator.evaluate_case("synthetic_case", t, cl, t, cd)
    assert res.is_stable is True
    assert res.num_cycles == 5
    assert np.isclose(res.St_zc_mean, 0.20, atol=1e-3)
    assert np.isclose(res.Cd_mean, 1.20, atol=1e-2)
    assert np.isclose(res.Cl_amp_mean, 0.50, atol=1e-2)
    assert res.status == "PASS"

    # Test pairwise comparison against self
    ref_metrics = {
        "St_zc_mean": res.St_zc_mean,
        "Cd_mean": res.Cd_mean,
        "Cl_amp_mean": res.Cl_amp_mean,
        "Cl_demeaned_rms": res.Cl_demeaned_rms,
    }
    res_pair = evaluator.evaluate_case("synthetic_case_pair", t, cl, t, cd, ref_metrics=ref_metrics)
    assert res_pair.comparison_to_ref["all_pass_3pct"] is True
    assert res_pair.status == "PASS"


def test_tier2_evaluator_identical_and_deviated_fields():
    scales = NondimensionalScales.create(D=0.30, U0=0.50)
    evaluator = Tier2Evaluator(scales=scales, max_rel_l2_vel=0.05, max_rel_l2_p=0.08)

    # 3D spatial grid: 16 x 24 x 32
    nz, ny, nx = 16, 24, 32
    x = np.linspace(-0.6, 3.0, nx)
    y = np.linspace(-0.9, 0.9, ny)
    z = np.linspace(0.9, 2.1, nz)

    Z, Y, X = np.meshgrid(z, y, x, indexing="ij")
    u = 0.50 * (1.0 - 0.2 * np.exp(-((X - 0.5)**2 + Y**2) / 0.1)) + 0.05 * np.sin(2 * np.pi * Z / 0.6)
    v = 0.05 * np.sin(2 * np.pi * X / 0.5)
    w = 0.02 * np.cos(2 * np.pi * Z / 0.5)
    p = 100.0 * np.exp(-((X - 0.5)**2 + Y**2) / 0.2)

    fields_ref = {"u": u, "v": v, "w": w, "p": p}
    fields_cand = {"u": u.copy(), "v": v.copy(), "w": w.copy(), "p": p.copy()}

    # Identical fields must have 0 L2 error, 1.0 correlation, and pass
    res_ident = evaluator.evaluate_3d_fields("identical_test", fields_cand, fields_ref, x, y, z)
    assert res_ident.passed_all_tier2 is True
    assert res_ident.field_rel_l2_errors["u"] < 1e-12
    assert res_ident.q_criterion_metrics["q_pearson_correlation"] > 0.999

    # Deviated fields: 15% velocity perturbation must fail
    fields_cand_bad = {
        "u": u * 1.15,
        "v": v * 1.15,
        "w": w * 1.15,
        "p": p * 1.15,
    }
    res_bad = evaluator.evaluate_3d_fields("deviated_test", fields_cand_bad, fields_ref, x, y, z)
    assert res_bad.passed_all_tier2 is False
    assert any("exceeds threshold" in r for r in res_bad.reasons)


import pytest


@pytest.mark.artifact
def test_real_case_mesh1_b():
    case_path = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3")
    if not case_path.is_dir():
        print(f"Case directory {case_path} not found, skipping")
        return

    pipeline = WorldModelAcceptancePipeline()
    report = pipeline.evaluate_full_case(case_path, wall_s=34831.0)
    assert report["case_name"] == "B_rtol1e3"
    assert report["tier1_acceptance"]["num_cycles"] == 5
    assert np.isclose(report["tier1_acceptance"]["St_zc_mean"], 0.2070, atol=1e-3)
    assert report["throughput_metrics"]["sim_s_per_hour"] > 6.0


def test_yplus_gate_compliance_and_buffer_layer_rejection():
    from tier1_tier2_acceptance_pipeline import WallYPlusGate

    gate = WallYPlusGate()

    # 1. Re=126,167 with proper C04 mesh (Δy1 = 0.0447 mm) -> PASS (y+ ~ 1.0)
    res_c04 = gate.evaluate_yplus(Re=126167, delta_y1_m=0.0447e-3)
    assert res_c04.gate_status == "PASS"
    assert np.isclose(res_c04.estimated_yplus, 1.0, atol=0.05)
    assert res_c04.is_buffer_layer is False

    # 2. Fatal user error scenario: naively reusing C04 mesh (Δy1=0.045mm) at Re=3,000,000
    # Ideal Δy1(y+=1) = 0.0026 mm -> estimated y+ = 0.045 / 0.0026 = 17.31 (Buffer Layer [5, 30]!)
    res_fatal = gate.evaluate_yplus(Re=3000000, delta_y1_m=0.045e-3)
    assert res_fatal.gate_status == "FAIL_BUFFER_LAYER"
    assert res_fatal.is_buffer_layer is True
    assert 5.0 < res_fatal.estimated_yplus < 30.0
    assert any("buffer layer" in r for r in res_fatal.reasons)

    # 3. Re=3,000,000 properly scaled mesh (Δy1 = 0.0026 mm) -> PASS
    res_scaled = gate.evaluate_yplus(Re=3000000, delta_y1_m=0.0026e-3)
    assert res_scaled.gate_status == "PASS"
    assert res_scaled.is_buffer_layer is False

    # 4. Measured y+ distribution in buffer layer
    yp_buffer = np.linspace(8.0, 16.0, 100)
    res_meas_buf = gate.evaluate_yplus(Re=126167, measured_yplus_array=yp_buffer)
    assert res_meas_buf.gate_status == "FAIL_BUFFER_LAYER"
    assert res_meas_buf.is_buffer_layer is True
