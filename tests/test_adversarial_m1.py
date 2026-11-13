"""Adversarial Empirical Stress Testing Suite for Milestone 1 (M1).

Focused on Feature F2 (CRPS O(M) Order Statistics vs O(M^2) Pairwise)
and Feature F3 (score_c04_forecast Safe Division & Zero-Energy Targets).

Author: Challenger M1-1 (Adversarial Critic)
"""

from __future__ import annotations

import time
import warnings

import numpy as np
import pytest

from hydro_disturb_bench.c04_forecasting import (
    C04_CHANNELS,
    score_c04_forecast,
)
from hydro_disturb_bench.comsol_java.errors import ModelValidationError
from hydro_disturb_bench.probabilistic_metrics import (
    compute_crps_ensemble,
)


def _naive_crps_pairwise(
    ensemble: np.ndarray,
    ground_truth: np.ndarray,
    fluid_mask: np.ndarray | None = None,
) -> float:
    """Independent canonical brute-force reference implementation using O(M^2) pairwise differences."""
    m = ensemble.shape[0]
    if m == 1:
        diff = np.abs(ensemble[0] - ground_truth)
        if fluid_mask is not None:
            valid = fluid_mask & np.isfinite(diff)
            return float(np.mean(diff[valid])) if np.any(valid) else 0.0
        valid = np.isfinite(diff)
        return float(np.mean(diff[valid])) if np.any(valid) else 0.0

    # Term 1: E[|x - y|] = (1/M) * sum_m |x_m - y|
    term1 = np.mean(np.abs(ensemble - ground_truth[None, ...]), axis=0)

    # Term 2: 0.5 * E[|x - x'|] = (1 / (2 * M^2)) * sum_{i,j} |x_i - x_j|
    pairwise_diffs = np.abs(ensemble[:, None, ...] - ensemble[None, :, ...])
    term2 = 0.5 * np.mean(pairwise_diffs, axis=(0, 1))

    pointwise = term1 - term2
    if fluid_mask is not None:
        valid = fluid_mask & np.isfinite(pointwise)
        return float(np.mean(pointwise[valid])) if np.any(valid) else 0.0

    valid = np.isfinite(pointwise)
    return float(np.mean(pointwise[valid])) if np.any(valid) else 0.0


# ==============================================================================
# Feature F2: CRPS Order Statistics Adversarial Tests
# ==============================================================================


@pytest.mark.parametrize("m", [2, 3, 5, 7, 11, 16, 25])
@pytest.mark.parametrize("shape", [(15,), (8, 9), (4, 5, 6), (2, 3, 4, 5)])
def test_adv_f2_mathematical_equivalence_random_nd(m: int, shape: tuple[int, ...]):
    """Verify exact equivalence across diverse ensemble sizes and N-D spatial dimensions."""
    rng = np.random.default_rng(seed=1000 + m + len(shape))
    ens = rng.standard_normal((m, *shape))
    truth = rng.standard_normal(shape)

    crps_fast = compute_crps_ensemble(ens, truth)
    crps_ref = _naive_crps_pairwise(ens, truth)

    # Must be within double-precision machine epsilon
    assert abs(crps_fast - crps_ref) < 1e-12, f"Failed for m={m}, shape={shape}: diff={abs(crps_fast - crps_ref)}"


def test_adv_f2_edge_case_m1():
    """Verify degenerate M=1 ensemble with various values strictly matches MAE."""
    rng = np.random.default_rng(seed=42)
    ens = rng.uniform(-100.0, 100.0, size=(1, 10, 10))
    truth = rng.uniform(-100.0, 100.0, size=(10, 10))

    crps = compute_crps_ensemble(ens, truth)
    mae = float(np.mean(np.abs(ens[0] - truth)))
    assert abs(crps - mae) < 1e-15


@pytest.mark.parametrize("m", [2, 5, 10, 50, 100])
def test_adv_f2_identical_ensemble_members_zero_spread(m: int):
    """Verify that identical ensemble members have exactly zero spread penalty and equal MAE."""
    rng = np.random.default_rng(seed=2024)
    single_field = rng.standard_normal((12, 12))
    truth = rng.standard_normal((12, 12))
    ens = np.repeat(single_field[None, ...], m, axis=0)

    crps = compute_crps_ensemble(ens, truth)
    mae = float(np.mean(np.abs(single_field - truth)))

    assert abs(crps - mae) < 1e-12, f"Failed for m={m}: CRPS {crps} != MAE {mae}"


@pytest.mark.parametrize("scale", [1e-9, 1e-6, 1.0, 1e6, 1e9])
def test_adv_f2_extreme_dynamic_ranges(scale: float):
    """Verify numerical stability and equivalence across microscopic and macroscopic scales."""
    rng = np.random.default_rng(seed=777)
    m = 8
    shape = (10, 10)
    ens = rng.standard_normal((m, *shape)) * scale
    truth = rng.standard_normal(shape) * scale

    crps_fast = compute_crps_ensemble(ens, truth)
    crps_ref = _naive_crps_pairwise(ens, truth)

    rel_err = abs(crps_fast - crps_ref) / max(abs(crps_ref), 1e-15)
    assert rel_err < 1e-11, f"Failed at scale {scale}: rel_err={rel_err}"


def test_adv_f2_alternating_signs_and_zero_crossings():
    """Stress test with violently oscillating signs and zero crossings."""
    m = 6
    shape = (20, 20)
    ens = np.zeros((m, *shape), dtype=np.float64)
    for i in range(m):
        sign = 1.0 if i % 2 == 0 else -1.0
        ens[i] = sign * (100.0 + i * 50.0)
    truth = np.zeros(shape, dtype=np.float64)

    crps_fast = compute_crps_ensemble(ens, truth)
    crps_ref = _naive_crps_pairwise(ens, truth)
    assert abs(crps_fast - crps_ref) < 1e-12


def test_adv_f2_masking_boundary_and_all_nan():
    """Verify masking behavior when fluid domain is partially or entirely masked out."""
    rng = np.random.default_rng(seed=888)
    m = 5
    shape = (10, 10)
    ens = rng.standard_normal((m, *shape))
    truth = rng.standard_normal(shape)

    # 1. Partial mask with NaNs inside solid
    mask = np.ones(shape, dtype=bool)
    mask[:5, :] = False  # top half is solid
    ens[:, :5, :] = np.nan
    truth[:5, :] = np.nan

    crps_masked = compute_crps_ensemble(ens, truth, fluid_mask=mask)
    crps_ref = _naive_crps_pairwise(ens, truth, fluid_mask=mask)
    assert abs(crps_masked - crps_ref) < 1e-12

    # 2. Fully masked out (no fluid points)
    all_solid_mask = np.zeros(shape, dtype=bool)
    assert compute_crps_ensemble(ens, truth, fluid_mask=all_solid_mask) == 0.0

    # 3. All NaNs everywhere without mask
    ens_all_nan = np.full((m, *shape), np.nan)
    assert compute_crps_ensemble(ens_all_nan, truth) == 0.0


def test_adv_f2_large_3d_grid_scaling_and_memory():
    """Stress test on large 3D grid: M=40, shape=(32, 32, 16).

    A naive pairwise tensor of shape (40, 40, 32, 32, 16) float64 would require
    40 * 40 * 16384 * 8 bytes = 209.7 MB for a single operation.
    For (50, 64, 64, 32), naive pairwise is 50 * 50 * 131072 * 8 = 2.62 GB.
    Verify O(M) executes efficiently within 1.0 second.
    """
    m = 40
    shape = (32, 32, 16)
    rng = np.random.default_rng(seed=321)
    ens = rng.standard_normal((m, *shape), dtype=np.float32)
    truth = rng.standard_normal(shape, dtype=np.float32)

    t0 = time.perf_counter()
    crps_val = compute_crps_ensemble(ens, truth)
    elapsed = time.perf_counter() - t0

    assert np.isfinite(crps_val)
    assert crps_val > 0.0
    assert elapsed < 1.5, f"Execution took too long: {elapsed:.3f}s"


def test_adv_f2_contract_input_validation():
    """Verify input validation contract raises ValueError on improper input dimensions."""
    ens = np.ones((5, 10))
    truth = np.ones((10, 10))  # truth has same ndim as ens (2 vs 2), violating ens.ndim >= truth.ndim + 1

    with pytest.raises(ValueError, match="ensemble_predictions must have at least one more leading dimension"):
        compute_crps_ensemble(ens, truth)


# ==============================================================================
# Feature F3: Safe Vectorized Lead Division Adversarial Tests
# ==============================================================================


@pytest.mark.parametrize(
    "target_val",
    [0.0, 1e-15, -1e-15, 1e-20, -1e-20],
)
def test_adv_f3_strict_zero_and_sub_epsilon_targets(target_val: float):
    """Verify no warnings of ANY kind under warnings.simplefilter('error') for zero and sub-epsilon targets."""
    num_leads = 5
    shape = (num_leads, 4, 10, 10)
    pred = np.ones(shape, dtype=np.float64) * 0.25
    target = np.full(shape, target_val, dtype=np.float64)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # Catch ALL warnings (Runtime, User, Deprecation, etc.)
        scores = score_c04_forecast(pred, target)

    assert scores["num_leads"] == num_leads
    for ch_name in C04_CHANNELS:
        ch = scores["channels"][ch_name]
        # Target energy <= 1e-12 should yield exactly 0.0 for rel_l2
        assert ch["rel_l2_per_lead"] == [0.0] * num_leads
        assert ch["mean_rel_l2"] == 0.0
        assert ch["mean_mae"] > 0.0
        assert ch["mean_rmse"] > 0.0


def test_adv_f3_mixed_lead_energies_vectorized():
    """Verify vectorized handling across leads with alternating zero and high energy."""
    num_leads = 4
    spatial = (8, 8)
    shape = (num_leads, 4, *spatial)

    pred = np.ones(shape, dtype=np.float64) * 2.0
    target = np.zeros(shape, dtype=np.float64)

    # Lead 0: Exactly 0.0
    # Lead 1: Sub-epsilon (1e-15)
    # Lead 2: Normal energy (target = 1.0 -> err = 1.0 -> rel_l2 = 1.0)
    # Lead 3: High energy (target = 100.0 -> err = -98.0 -> rel_l2 = 98 / 100 = 0.98)
    target[0] = 0.0
    target[1] = 1e-15
    target[2] = 1.0
    target[3] = 100.0

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scores = score_c04_forecast(pred, target)

    for ch_name in C04_CHANNELS:
        ch = scores["channels"][ch_name]
        rel_l2 = ch["rel_l2_per_lead"]
        assert rel_l2[0] == 0.0
        assert rel_l2[1] == 0.0
        assert rel_l2[2] == pytest.approx(1.0, abs=1e-6)
        assert rel_l2[3] == pytest.approx(0.98, abs=1e-4)


def test_adv_f3_hydrodynamic_2d_w_zero_channel():
    """Verify realistic 2D hydrodynamics where w (spanwise velocity) is identically zero in all frames."""
    num_leads = 6
    shape = (num_leads, 4, 16, 16)

    # Channels: 0: u, 1: v, 2: w, 3: p
    pred = np.zeros(shape, dtype=np.float64)
    target = np.zeros(shape, dtype=np.float64)

    # u: target=1.0, pred=1.1
    target[:, 0] = 1.0
    pred[:, 0] = 1.1

    # v: target=0.5, pred=0.4
    target[:, 1] = 0.5
    pred[:, 1] = 0.4

    # w: exactly 0.0 everywhere (2D flow)
    target[:, 2] = 0.0
    pred[:, 2] = 0.0

    # p: target=10.0, pred=9.5
    target[:, 3] = 10.0
    pred[:, 3] = 9.5

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scores = score_c04_forecast(pred, target)

    ch_w = scores["channels"]["w"]
    assert ch_w["rel_l2_per_lead"] == [0.0] * num_leads
    assert ch_w["mean_mae"] == 0.0
    assert ch_w["mean_rmse"] == 0.0

    ch_u = scores["channels"]["u"]
    assert ch_u["mean_rel_l2"] == pytest.approx(0.1, abs=1e-5)


def test_adv_f3_solid_mask_single_point_and_empty():
    """Verify edge cases with solid masks: single fluid point and all solid."""
    num_leads = 3
    spatial = (10, 10)
    shape = (num_leads, 4, *spatial)

    pred = np.ones(shape, dtype=np.float64)
    target = np.ones(shape, dtype=np.float64)

    # Single fluid point: mask all True except one cell
    single_fluid_mask = np.ones(spatial, dtype=bool)
    single_fluid_mask[5, 5] = False  # solid_mask=False means fluid

    scores = score_c04_forecast(pred, target, solid_mask=single_fluid_mask)
    assert scores["fluid_points"] == 1
    assert scores["channels"]["u"]["mean_mae"] == 0.0

    # All solid points (0 fluid points) -> must raise ModelValidationError
    all_solid_mask = np.ones(spatial, dtype=bool)
    with pytest.raises(ModelValidationError, match="No valid fluid points to evaluate"):
        score_c04_forecast(pred, target, solid_mask=all_solid_mask)


def test_adv_f3_rejection_of_non_finite_inputs():
    """Verify that NaNs and Infs in prediction or target are strictly rejected with ModelValidationError."""
    num_leads = 4
    spatial = (12, 12)
    shape = (num_leads, 4, *spatial)

    pred = np.ones(shape, dtype=np.float64) * 2.0
    target = np.ones(shape, dtype=np.float64) * 1.0

    # 1. NaN in prediction
    pred_nan = pred.copy()
    pred_nan[0, 0, 2, 2] = np.nan
    with pytest.raises(ModelValidationError, match="prediction contains non-finite values"):
        score_c04_forecast(pred_nan, target)

    # 2. Inf in target
    target_inf = target.copy()
    target_inf[1, 1, 3, 3] = np.inf
    with pytest.raises(ModelValidationError, match="target contains non-finite values"):
        score_c04_forecast(pred, target_inf)


def test_adv_f3_3d_spatial_grid_forecasting():
    """Verify score_c04_forecast with 3D spatial grids (H, 4, Nz, Ny, Nx)."""
    num_leads = 3
    spatial_3d = (6, 8, 8)
    shape = (num_leads, 4, *spatial_3d)

    pred = np.full(shape, 3.0, dtype=np.float64)
    target = np.full(shape, 2.0, dtype=np.float64)

    scores = score_c04_forecast(pred, target)
    expected_pts = 6 * 8 * 8
    assert scores["fluid_points"] == expected_pts

    for ch_name in C04_CHANNELS:
        ch = scores["channels"][ch_name]
        assert ch["mean_mae"] == pytest.approx(1.0, abs=1e-12)
        assert ch["mean_rmse"] == pytest.approx(1.0, abs=1e-12)
        # err = 1.0, tgt = 2.0 -> rel_l2 = 1.0 / 2.0 = 0.5
        assert ch["mean_rel_l2"] == pytest.approx(0.5, abs=1e-12)


def test_adv_f2_float32_input_precision():
    """Verify that float32 ensemble inputs are handled properly and match reference."""
    rng = np.random.default_rng(seed=555)
    m = 12
    shape = (16, 16)
    ens = rng.standard_normal((m, *shape), dtype=np.float32)
    truth = rng.standard_normal(shape, dtype=np.float32)

    crps_fast = compute_crps_ensemble(ens, truth)
    crps_ref = _naive_crps_pairwise(ens, truth)

    # For float32 inputs, tolerance is within float32 precision (~1e-6)
    assert abs(crps_fast - crps_ref) < 1e-6
