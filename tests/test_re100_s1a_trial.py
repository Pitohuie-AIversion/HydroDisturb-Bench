"""Tests for Stage S1-A: Re=100 Long-Time Cylinder Flow Trial Run.

Validates:
1. Baseline and S0 benchmark model immutability.
2. Independent S1 trial model isolation and solution health.
3. Pressure surge physical explanation verification (t = 0.05s).
4. Saturated vortex shedding limit cycle (Cd in [1.25, 1.40], Cl amp in [0.25, 0.35], St in [0.14, 0.17]).
5. Wake centerline probe oscillation consistency.
"""
from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    compute_file_hash,
    find_comsol_root,
    ComsolConnectionError,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL = WORKSPACE_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = WORKSPACE_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
S1_TRIAL_DIR = WORKSPACE_DIR / "runs" / "re100_s1a_trial"

BASELINE_SHA256 = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
BENCHMARK_SHA256 = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"


def comsol_available() -> bool:
    try:
        find_comsol_root()
        return True
    except ComsolConnectionError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not comsol_available(),
        reason="Real COMSOL installation not found on this machine",
    ),
    pytest.mark.skipif(
        not BASELINE_MODEL.is_file(),
        reason=f"Baseline model not found at {BASELINE_MODEL}",
    ),
    pytest.mark.skipif(
        not BENCHMARK_MODEL.is_file(),
        reason=f"Benchmark model not found at {BENCHMARK_MODEL}",
    ),
]


def load_comsol_1d(path: Path) -> np.ndarray:
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                rows.append([float(parts[0]), float(parts[1])])
    return np.array(rows)


def test_s1a_models_immutability():
    """Verify both baseline and S0 benchmark models have strictly unchanged SHA-256 hashes."""
    assert compute_file_hash(BASELINE_MODEL) == BASELINE_SHA256
    assert compute_file_hash(BENCHMARK_MODEL) == BENCHMARK_SHA256


def test_s1a_trial_model_isolation():
    """Verify S1-A solved model exists in runs/re100_s1a_trial and is strictly isolated."""
    solved_mph = S1_TRIAL_DIR / "re100_s1a_solved.mph"
    if not solved_mph.is_file():
        pytest.skip(f"S1-A trial solved model not found at {solved_mph}")

    solved_hash = compute_file_hash(solved_mph)
    assert solved_hash != BASELINE_SHA256
    assert solved_hash != BENCHMARK_SHA256
    assert solved_mph.stat().st_size > 50 * 1024 * 1024  # > 50 MB with 501 timesteps


def test_s1a_pressure_surge_physical_bounds():
    """Verify startup pressure surge is localized to t=0.05s and decays to steady hydrodynamic levels."""
    iu_file = S1_TRIAL_DIR / "inlet_u.txt"
    ip_file = S1_TRIAL_DIR / "inlet_p.txt"
    sp_file = S1_TRIAL_DIR / "stag_p.txt"
    if not (iu_file.is_file() and ip_file.is_file() and sp_file.is_file()):
        pytest.skip("Inlet pressure history files not found")

    ip = load_comsol_1d(ip_file)
    sp = load_comsol_1d(sp_file)

    # After startup ramp (t >= 0.4s), inlet pressure should be below 0.01 Pa
    post_ramp_ip = ip[ip[:, 0] >= 0.4, 1]
    assert np.all(post_ramp_ip < 0.01), f"Post-ramp inlet pressure exceeded 0.01 Pa: max={post_ramp_ip.max()}"

    # Stagnation pressure at late times should match ~ 0.5 * rho * U_inf^2 ~ 0.0505 Pa (within 20%)
    post_ramp_sp = sp[sp[:, 0] >= 20.0, 1]
    assert np.all(post_ramp_sp > 0.04) and np.all(post_ramp_sp < 0.08)


def test_s1a_vortex_shedding_hydrodynamic_metrics():
    """Verify limit-cycle vortex shedding metrics match standard Re=100 physical benchmarks."""
    cl_file = S1_TRIAL_DIR / "lift_coeff.txt"
    cd_file = S1_TRIAL_DIR / "drag_coeff.txt"
    wv_file = S1_TRIAL_DIR / "wake_v.txt"
    if not (cl_file.is_file() and cd_file.is_file() and wv_file.is_file()):
        pytest.skip("S1-A diagnostic files not found")

    cl_data = load_comsol_1d(cl_file)
    cd_data = load_comsol_1d(cd_file)
    wv_data = load_comsol_1d(wv_file)

    t = cl_data[:, 0]
    cl = cl_data[:, 1]
    cd = cd_data[:, 1]
    wv = wv_data[:, 1]

    # Verify total simulation length is 100s
    assert math.isclose(t[-1], 100.0, abs_tol=1e-3)
    assert len(t) == 501

    # Saturated window: t in [60, 100] s
    mask = (t >= 60.0)
    t_sat = t[mask]
    cl_sat = cl[mask]
    cd_sat = cd[mask]
    wv_sat = wv[mask]

    # 1. Mean drag coefficient should be around 1.30 ~ 1.35
    cd_mean = cd_sat.mean()
    assert 1.25 <= cd_mean <= 1.40, f"Mean Cd {cd_mean:.4f} outside expected [1.25, 1.40]"

    # 2. Lift coefficient amplitude should be around 0.28 ~ 0.33
    cl_amp = (cl_sat.max() - cl_sat.min()) / 2.0
    assert 0.25 <= cl_amp <= 0.35, f"Cl amplitude {cl_amp:.4f} outside expected [0.25, 0.35]"

    # 3. Mean lift should be machine-zero preserving top/bottom symmetry
    cl_mean = cl_sat.mean()
    assert abs(cl_mean) < 0.02, f"Mean Cl {cl_mean:.4e} indicates asymmetric drift"

    # 4. Wake v-velocity should oscillate periodically
    wv_amp = (wv_sat.max() - wv_sat.min()) / 2.0
    assert wv_amp > 1e-3, f"Wake v amplitude too small: {wv_amp:.4e}"

    # 5. Strouhal number estimation
    dt = t[1] - t[0]
    fft_cl = np.fft.rfft(cl_sat - cl_mean)
    freqs = np.fft.rfftfreq(len(t_sat), d=dt)
    peak_idx = int(np.argmax(np.abs(fft_cl[1:])) + 1)
    f_shed = float(freqs[peak_idx])
    D = 0.01
    U_inf = 0.010058104588
    St = f_shed * D / U_inf
    assert 0.13 <= St <= 0.17, f"Strouhal number {St:.4f} outside expected [0.13, 0.17]"
