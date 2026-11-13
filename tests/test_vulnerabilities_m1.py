"""Unit tests for Milestone 1 (M1) vulnerability and numerical stability remediation (F1 - F5).

Features covered:
- F1: Disturbance inversion TP/TN/FP/FN confusion matrix classification and F1 metrics.
- F2: Continuous Ranked Probability Score (CRPS) O(M) sorted identity equivalence and scaling.
- F3: Zero-energy lead division numerical stability in C04 forecasting metrics.
- F4: COMSOL extruded-xz 3D coordinate mapping with dynamic disturbance velocity.
- F5: Safe NPZ file descriptor context management and Windows file lock release.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.c04_disturbance import (
    C04DisturbanceConfig,
    generate_c04_comsol_extruded_xz_force_expressions,
)
from hydro_disturb_bench.c04_forecasting import (
    C04_CHANNELS,
    score_c04_forecast,
)
from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.disturbance_inversion import (
    DisturbanceGroundTruth,
    DisturbancePrediction,
    compute_inversion_confusion_metrics,
    evaluate_disturbance_inversion,
    evaluate_inversion_batch,
)
from hydro_disturb_bench.probabilistic_metrics import (
    compute_crps_ensemble,
    compute_ensemble_crps,
)

# ==============================================================================
# Feature F1: Disturbance Inversion Confusion Matrix & Classification Metrics
# ==============================================================================


def test_f1_inversion_confusion_metrics():
    """Master verification test for Feature F1: Disturbance Inversion Confusion Metrics.

    Requirements verified:
    1. Active disturbance detected with t_detect=None yields true_positives == 1, true_negatives == 0.
    2. Inactive disturbance (active=False) without detection yields true_negatives == 1, true_positives == 0.
    3. False alarms (active=False, pred.detected=True) yields false_positives == 1.
    4. Missed detections (active=True, pred.detected=False) yields false_negatives == 1.
    5. Mixed batch evaluation correctly calculates precision, recall, and F1 score.
    """
    # 1. Active disturbance detected with t_detect=None
    m_tp_notime = evaluate_disturbance_inversion(
        DisturbanceGroundTruth(active=True, t_on=10.0, t_off=15.0),
        DisturbancePrediction(detected=True, t_detect=None),
    )
    assert m_tp_notime.detected_correctly is True
    assert m_tp_notime.false_alarm is False
    assert m_tp_notime.missed_detection is False
    assert m_tp_notime.true_negative is False
    c_tp = compute_inversion_confusion_metrics([m_tp_notime])
    assert c_tp.true_positives == 1
    assert c_tp.true_negatives == 0

    # 2. Inactive disturbance without detection
    m_tn = evaluate_disturbance_inversion(
        DisturbanceGroundTruth(active=False),
        DisturbancePrediction(detected=False),
    )
    assert m_tn.detected_correctly is False
    assert m_tn.true_negative is True
    c_tn = compute_inversion_confusion_metrics([m_tn])
    assert c_tn.true_negatives == 1
    assert c_tn.true_positives == 0

    # 3. False alarm
    m_fp = evaluate_disturbance_inversion(
        DisturbanceGroundTruth(active=False),
        DisturbancePrediction(detected=True, t_detect=12.0),
    )
    assert m_fp.false_alarm is True
    assert m_fp.true_negative is False
    c_fp = compute_inversion_confusion_metrics([m_fp])
    assert c_fp.false_positives == 1

    # 4. Missed detection
    m_fn = evaluate_disturbance_inversion(
        DisturbanceGroundTruth(active=True, t_on=5.0, t_off=8.0),
        DisturbancePrediction(detected=False),
    )
    assert m_fn.missed_detection is True
    assert m_fn.detected_correctly is False
    c_fn = compute_inversion_confusion_metrics([m_fn])
    assert c_fn.false_negatives == 1

    # 5. Mixed batch verification
    mixed_batch = [
        # TP: 2 cases (one with timestamp, one without timestamp)
        (
            DisturbanceGroundTruth(active=True, t_on=1.0, t_off=3.0),
            DisturbancePrediction(detected=True, t_detect=1.5),
        ),
        (
            DisturbanceGroundTruth(active=True, t_on=2.0, t_off=4.0),
            DisturbancePrediction(detected=True, t_detect=None),
        ),
        # TN: 2 cases
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=False),
        ),
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=False),
        ),
        # FP: 1 case
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=True, t_detect=5.0),
        ),
        # FN: 1 case
        (
            DisturbanceGroundTruth(active=True, t_on=10.0, t_off=12.0),
            DisturbancePrediction(detected=False),
        ),
    ]
    batch_metrics = evaluate_inversion_batch(mixed_batch)
    assert batch_metrics["total_cases"] == 6
    assert batch_metrics["true_positives"] == 2
    assert batch_metrics["true_negatives"] == 2
    assert batch_metrics["false_positives"] == 1
    assert batch_metrics["false_negatives"] == 1

    # Precision = 2 / (2 + 1) = 2/3
    assert batch_metrics["precision"] == pytest.approx(2.0 / 3.0, abs=1e-6)
    # Recall = 2 / (2 + 1) = 2/3
    assert batch_metrics["recall"] == pytest.approx(2.0 / 3.0, abs=1e-6)
    # F1 = 2 * (2/3 * 2/3) / (4/3) = 2/3
    assert batch_metrics["f1_score"] == pytest.approx(2.0 / 3.0, abs=1e-6)


def test_f1_active_detected_without_timestamp_is_true_positive():
    """Verify that a detected disturbance with t_detect=None is classified as TP (not TN)."""
    truth = DisturbanceGroundTruth(
        active=True,
        t_on=10.0,
        t_off=15.0,
        center=(0.15, 0.0),
        amplitude=0.01,
        direction=(0.0, 1.0),
    )
    pred = DisturbancePrediction(
        detected=True,
        t_detect=None,
        t_on=10.2,
        t_off=15.1,
        center=(0.15, 0.0),
        amplitude=0.01,
        direction=(0.0, 1.0),
    )

    metrics = evaluate_disturbance_inversion(truth, pred)
    assert metrics.detected_correctly is True
    assert metrics.false_alarm is False
    assert metrics.missed_detection is False
    assert metrics.true_negative is False
    assert metrics.detection_delay_sec is None

    confusion = compute_inversion_confusion_metrics([metrics])
    assert confusion.true_positives == 1
    assert confusion.true_negatives == 0
    assert confusion.false_positives == 0
    assert confusion.false_negatives == 0


def test_f1_inactive_not_detected_is_true_negative():
    """Verify that an inactive disturbance (active=False) with detected=False yields TN."""
    truth = DisturbanceGroundTruth(active=False)
    pred = DisturbancePrediction(detected=False)

    metrics = evaluate_disturbance_inversion(truth, pred)
    assert metrics.detected_correctly is False
    assert metrics.true_negative is True
    assert metrics.false_alarm is False
    assert metrics.missed_detection is False
    assert metrics.composite_inversion_score == pytest.approx(1.0)

    confusion = compute_inversion_confusion_metrics([metrics])
    assert confusion.true_positives == 0
    assert confusion.true_negatives == 1
    assert confusion.false_positives == 0
    assert confusion.false_negatives == 0


def test_f1_inactive_detected_is_false_alarm():
    """Verify that an inactive disturbance with detected=True yields FP."""
    truth = DisturbanceGroundTruth(active=False)
    pred = DisturbancePrediction(detected=True, t_detect=12.5)

    metrics = evaluate_disturbance_inversion(truth, pred)
    assert metrics.detected_correctly is False
    assert metrics.true_negative is False
    assert metrics.false_alarm is True
    assert metrics.missed_detection is False
    assert metrics.composite_inversion_score == pytest.approx(0.0)

    confusion = compute_inversion_confusion_metrics([metrics])
    assert confusion.true_positives == 0
    assert confusion.true_negatives == 0
    assert confusion.false_positives == 1
    assert confusion.false_negatives == 0


def test_f1_active_missed_is_false_negative():
    """Verify that an active disturbance with detected=False yields FN."""
    truth = DisturbanceGroundTruth(active=True, t_on=5.0, t_off=10.0)
    pred = DisturbancePrediction(detected=False)

    metrics = evaluate_disturbance_inversion(truth, pred)
    assert metrics.detected_correctly is False
    assert metrics.true_negative is False
    assert metrics.false_alarm is False
    assert metrics.missed_detection is True
    assert metrics.composite_inversion_score == pytest.approx(0.0)

    confusion = compute_inversion_confusion_metrics([metrics])
    assert confusion.true_positives == 0
    assert confusion.true_negatives == 0
    assert confusion.false_positives == 0
    assert confusion.false_negatives == 1


def test_f1_batch_confusion_metrics_and_f1_score():
    """Verify precision, recall, and F1 calculations across a heterogeneous evaluation batch."""
    batch_cases: list[tuple[DisturbanceGroundTruth, DisturbancePrediction]] = [
        # TP 1: Active, detected with timestamp
        (
            DisturbanceGroundTruth(active=True, t_on=10.0, t_off=12.0),
            DisturbancePrediction(detected=True, t_detect=10.5),
        ),
        # TP 2: Active, detected without timestamp (the specific bug in F1)
        (
            DisturbanceGroundTruth(active=True, t_on=20.0, t_off=22.0),
            DisturbancePrediction(detected=True, t_detect=None),
        ),
        # TP 3: Active, detected with exact timestamp
        (
            DisturbanceGroundTruth(active=True, t_on=30.0, t_off=35.0),
            DisturbancePrediction(detected=True, t_detect=30.0),
        ),
        # TN 1: Inactive, not detected
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=False),
        ),
        # TN 2: Inactive, not detected
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=False),
        ),
        # FP 1: Inactive, false alarm
        (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=True, t_detect=40.0),
        ),
        # FN 1: Active, missed detection
        (
            DisturbanceGroundTruth(active=True, t_on=50.0, t_off=55.0),
            DisturbancePrediction(detected=False),
        ),
        # FN 2: Active, missed detection
        (
            DisturbanceGroundTruth(active=True, t_on=60.0, t_off=65.0),
            DisturbancePrediction(detected=False),
        ),
    ]

    results = evaluate_inversion_batch(batch_cases)

    assert results["total_cases"] == 8
    assert results["true_positives"] == 3
    assert results["false_positives"] == 1
    assert results["false_negatives"] == 2
    assert results["true_negatives"] == 2

    # Precision = TP / (TP + FP) = 3 / (3 + 1) = 0.75
    expected_precision = 3.0 / 4.0
    assert results["precision"] == pytest.approx(expected_precision, abs=1e-6)

    # Recall = TP / (TP + FN) = 3 / (3 + 2) = 0.60
    expected_recall = 3.0 / 5.0
    assert results["recall"] == pytest.approx(expected_recall, abs=1e-6)

    # F1 = 2 * (P * R) / (P + R) = 2 * (0.45) / 1.35 = 0.90 / 1.35 = 2/3
    expected_f1 = (2.0 * expected_precision * expected_recall) / (expected_precision + expected_recall)
    assert results["f1_score"] == pytest.approx(expected_f1, abs=1e-6)


# ==============================================================================
# Feature F2: O(M) Scalable Ensemble CRPS Sorted Identity
# ==============================================================================


def _naive_crps_reference(
    ensemble: np.ndarray,
    ground_truth: np.ndarray,
    fluid_mask: np.ndarray | None = None,
) -> float:
    """Exact ground truth reference computation using the pairwise O(M^2) difference definition."""
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

    # Term 2: 0.5 * E[|x - x'|] = (1 / (2 * M^2)) * sum_i sum_j |x_i - x_j|
    pairwise_diffs = np.abs(ensemble[:, None, ...] - ensemble[None, :, ...])
    term2 = 0.5 * np.mean(pairwise_diffs, axis=(0, 1))

    pointwise = term1 - term2
    if fluid_mask is not None:
        valid = fluid_mask & np.isfinite(pointwise)
        return float(np.mean(pointwise[valid])) if np.any(valid) else 0.0

    valid = np.isfinite(pointwise)
    return float(np.mean(pointwise[valid])) if np.any(valid) else 0.0


def test_f2_crps_om_equivalence_and_scaling():
    """Master verification test for Feature F2: O(M) Ensemble CRPS equivalence and scaling.

    Requirements verified:
    1. Mathematical equivalence between naive pairwise differences and O(M) sorted identity
       across random 1D, 2D, and 3D tensors within 10^-12 tolerance.
    2. Deterministic M=1 ensemble case equals MAE.
    3. Identical ensemble members equals MAE (0.0 spread penalty).
    4. Fluid mask and NaN filtering.
    5. Function alias compute_ensemble_crps is available and identical to compute_crps_ensemble.
    6. Large 3D tensor scaling without memory exhaustion (shape (40, 32, 32, 16)).
    """
    rng = np.random.default_rng(seed=42)

    # 1. Equivalence across 1D, 2D, 3D
    # 1D
    e1, t1 = rng.standard_normal((5, 20)), rng.standard_normal(20)
    assert abs(compute_crps_ensemble(e1, t1) - _naive_crps_reference(e1, t1)) < 1e-12
    # 2D
    e2, t2 = rng.standard_normal((7, 8, 10)), rng.standard_normal((8, 10))
    assert abs(compute_crps_ensemble(e2, t2) - _naive_crps_reference(e2, t2)) < 1e-12
    # 3D
    e3, t3 = rng.standard_normal((6, 4, 5, 6)), rng.standard_normal((4, 5, 6))
    assert abs(compute_crps_ensemble(e3, t3) - _naive_crps_reference(e3, t3)) < 1e-12

    # 2. Deterministic M=1 case equals MAE
    e_m1, t_m1 = rng.standard_normal((1, 8, 8)), rng.standard_normal((8, 8))
    mae_m1 = float(np.mean(np.abs(e_m1[0] - t_m1)))
    assert abs(compute_crps_ensemble(e_m1, t_m1) - mae_m1) < 1e-12

    # 3. Identical members (zero spread penalty) equals MAE
    single_p = rng.standard_normal((6, 6))
    e_ident = np.broadcast_to(single_p[None, ...], (10, 6, 6)).copy()
    mae_ident = float(np.mean(np.abs(single_p - t_m1[:6, :6])))
    assert abs(compute_crps_ensemble(e_ident, t_m1[:6, :6]) - mae_ident) < 1e-12

    # 4. Fluid mask and NaN filtering
    mask = np.ones((8, 10), dtype=bool)
    mask[0:2, 0:2] = False
    e2_nan = e2.copy()
    e2_nan[:, 0:2, 0:2] = np.nan
    crps_mask = compute_crps_ensemble(e2_nan, t2, fluid_mask=mask)
    crps_mask_ref = _naive_crps_reference(e2_nan, t2, fluid_mask=mask)
    assert np.isfinite(crps_mask)
    assert abs(crps_mask - crps_mask_ref) < 1e-12

    # 5. Alias availability and identity
    assert compute_ensemble_crps is compute_crps_ensemble
    assert compute_ensemble_crps(e1, t1) == compute_crps_ensemble(e1, t1)

    # 6. Large 3D tensor scaling (40, 32, 32, 16)
    large_ens = rng.standard_normal((40, 32, 32, 16), dtype=np.float32)
    large_truth = rng.standard_normal((32, 32, 16), dtype=np.float32)
    large_crps = compute_crps_ensemble(large_ens, large_truth)
    assert np.isfinite(large_crps)
    assert large_crps > 0.0


def test_f2_crps_om_equivalence_across_dimensions():
    """Verify exact equivalence between O(M) sorted identity and O(M^2) pairwise definition."""
    rng = np.random.default_rng(seed=42)

    # 1D test case: M=5, N=25
    ens_1d = rng.standard_normal((5, 25))
    truth_1d = rng.standard_normal(25)
    crps_om_1d = compute_crps_ensemble(ens_1d, truth_1d)
    crps_naive_1d = _naive_crps_reference(ens_1d, truth_1d)
    assert abs(crps_om_1d - crps_naive_1d) < 1e-12

    # 2D test case: M=8, Ny=12, Nx=15
    ens_2d = rng.standard_normal((8, 12, 15))
    truth_2d = rng.standard_normal((12, 15))
    crps_om_2d = compute_crps_ensemble(ens_2d, truth_2d)
    crps_naive_2d = _naive_crps_reference(ens_2d, truth_2d)
    assert abs(crps_om_2d - crps_naive_2d) < 1e-12

    # 3D test case: M=6, Nz=5, Ny=6, Nx=7
    ens_3d = rng.standard_normal((6, 5, 6, 7))
    truth_3d = rng.standard_normal((5, 6, 7))
    crps_om_3d = compute_crps_ensemble(ens_3d, truth_3d)
    crps_naive_3d = _naive_crps_reference(ens_3d, truth_3d)
    assert abs(crps_om_3d - crps_naive_3d) < 1e-12


def test_f2_crps_deterministic_m1_equals_mae():
    """Verify that an ensemble with M=1 degenerates exactly to Mean Absolute Error."""
    rng = np.random.default_rng(seed=123)
    ens = rng.standard_normal((1, 16, 16))
    truth = rng.standard_normal((16, 16))

    crps_val = compute_crps_ensemble(ens, truth)
    mae_val = float(np.mean(np.abs(ens[0] - truth)))
    assert abs(crps_val - mae_val) < 1e-12


def test_f2_crps_identical_members_equals_mae():
    """Verify that an ensemble with M > 1 identical members has zero spread penalty and equals MAE."""
    rng = np.random.default_rng(seed=456)
    single_pred = rng.standard_normal((10, 10))
    truth = rng.standard_normal((10, 10))
    # Broadcast identical prediction across 12 ensemble members
    ens = np.broadcast_to(single_pred[None, ...], (12, 10, 10)).copy()

    crps_val = compute_crps_ensemble(ens, truth)
    mae_val = float(np.mean(np.abs(single_pred - truth)))
    assert abs(crps_val - mae_val) < 1e-12


def test_f2_crps_fluid_mask_and_nan_filtering():
    """Verify that fluid_mask correctly excludes solid domains and NaNs."""
    rng = np.random.default_rng(seed=789)
    m, ny, nx = 5, 10, 10
    ens = rng.standard_normal((m, ny, nx))
    truth = rng.standard_normal((ny, nx))

    # Mask: True for valid fluid, False for obstacle
    mask = np.ones((ny, nx), dtype=bool)
    mask[0:3, 0:3] = False  # Solid cylinder obstacle

    # Inject NaNs inside the obstacle domain
    ens[:, 0:2, 0:2] = np.nan
    truth[0:2, 0:2] = np.nan

    crps_om = compute_crps_ensemble(ens, truth, fluid_mask=mask)
    crps_ref = _naive_crps_reference(ens, truth, fluid_mask=mask)

    assert np.isfinite(crps_om)
    assert abs(crps_om - crps_ref) < 1e-12


def test_f2_crps_alias_identity():
    """Verify compute_ensemble_crps is a direct alias for compute_crps_ensemble."""
    assert compute_ensemble_crps is compute_crps_ensemble

    ens = np.array([[[1.0, 2.0]], [[3.0, 4.0]]])
    truth = np.array([[1.5, 2.5]])
    assert compute_ensemble_crps(ens, truth) == compute_crps_ensemble(ens, truth)


def test_f2_crps_large_3d_tensor_scaling_without_oom():
    """Verify that large 3D flow tensors (e.g. M=40, Nz=32, Ny=32, Nx=16) compute cleanly in O(M)."""
    shape = (40, 32, 32, 16)
    rng = np.random.default_rng(seed=999)
    ens = rng.standard_normal(shape, dtype=np.float32)
    truth = rng.standard_normal(shape[1:], dtype=np.float32)

    crps_val = compute_crps_ensemble(ens, truth)
    assert np.isfinite(crps_val)
    assert crps_val > 0.0


# ==============================================================================
# Feature F3: Safe Vectorized Lead Division in Forecasting Metrics
# ==============================================================================


def test_f3_forecasting_zero_energy_lead_division():
    """Master verification test for Feature F3: Safe Vectorized Lead Division on Zero-Energy Target.

    Requirements verified:
    1. score_c04_forecast when target is all zeros (zero energy in all leads).
    2. Executed under warnings.catch_warnings() with warnings.simplefilter('error', RuntimeWarning).
    3. Verify zero warnings emitted and rel_l2_per_lead is correctly returned as 0.0.
    """
    num_leads = 6
    shape = (num_leads, 4, 12, 12)
    pred = np.ones(shape, dtype=np.float64) * 0.10
    target_all_zeros = np.zeros(shape, dtype=np.float64)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        scores = score_c04_forecast(pred, target_all_zeros)

    assert scores["num_leads"] == num_leads
    for ch_name in C04_CHANNELS:
        ch_metric = scores["channels"][ch_name]
        assert ch_metric["rel_l2_per_lead"] == [0.0] * num_leads
        assert ch_metric["mean_rel_l2"] == pytest.approx(0.0, abs=1e-12)
        assert ch_metric["mean_mae"] > 0.0
        assert ch_metric["mean_rmse"] > 0.0


def test_f3_forecasting_zero_energy_target_zero_warnings():
    """Verify that score_c04_forecast evaluates zero-energy target without RuntimeWarning."""
    num_leads = 8
    spatial_shape = (16, 16)
    pred_shape = (num_leads, 4, *spatial_shape)

    pred = np.ones(pred_shape, dtype=np.float64) * 0.05
    target_zeros = np.zeros(pred_shape, dtype=np.float64)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        metrics = score_c04_forecast(pred, target_zeros)

    assert metrics["num_leads"] == num_leads
    for ch_name in C04_CHANNELS:
        ch_metric = metrics["channels"][ch_name]
        assert ch_metric["rel_l2_per_lead"] == [0.0] * num_leads
        assert ch_metric["mean_rel_l2"] == pytest.approx(0.0, abs=1e-12)
        assert ch_metric["mean_mae"] > 0.0
        assert ch_metric["mean_rmse"] > 0.0


def test_f3_forecasting_both_zero_energy():
    """Verify that when both prediction and target are zero, relative L2 is zero without warning."""
    num_leads = 4
    spatial_shape = (8, 8)
    shape = (num_leads, 4, *spatial_shape)

    pred = np.zeros(shape, dtype=np.float64)
    target = np.zeros(shape, dtype=np.float64)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        metrics = score_c04_forecast(pred, target)

    for ch_name in C04_CHANNELS:
        ch_metric = metrics["channels"][ch_name]
        assert ch_metric["rel_l2_per_lead"] == [0.0] * num_leads
        assert ch_metric["mean_rel_l2"] == 0.0
        assert ch_metric["mean_mae"] == 0.0
        assert ch_metric["mean_rmse"] == 0.0


def test_f3_forecasting_normal_energy_accuracy():
    """Verify that relative L2 calculation on standard target matches manual norm."""
    num_leads = 2
    shape = (num_leads, 4, 4, 4)
    pred = np.ones(shape, dtype=np.float64) * 2.0
    target = np.ones(shape, dtype=np.float64) * 1.0

    metrics = score_c04_forecast(pred, target)
    for ch_name in C04_CHANNELS:
        ch_metric = metrics["channels"][ch_name]
        for val in ch_metric["rel_l2_per_lead"]:
            assert val == pytest.approx(1.0, abs=1e-9)


# ==============================================================================
# Feature F4: Extruded-xz 3D Disturbance Force Expressions with Velocity
# ==============================================================================


def test_f4_c04_extruded_3d_force_expressions():
    """Master verification test for Feature F4: Extruded 3D Force Expressions.

    Requirements verified:
    1. Stationary baseline velocity_m_s=(0.0, 0.0, 0.0) produces exact expected baseline expression.
    2. Moving disturbance velocity_m_s=(0.20, -0.10, 0.05) correctly incorporates:
       - vx -> x
       - vz -> y (spanwise)
       - vy -> z (crossflow)
       with t_rel = (t - on_s) in the Gaussian expression strings for Fx, Fy, Fz.
    """
    # 1. Stationary baseline
    cfg_stat = C04DisturbanceConfig(
        center_m=(0.50, 0.10, 1.50),
        velocity_m_s=(0.0, 0.0, 0.0),
        direction=(0.0, 1.0, 0.0),
        on_s=2.0,
        off_s=4.0,
        acceleration_m_s2=0.05,
        sigma_m=0.03,
    )
    exprs_stat = generate_c04_comsol_extruded_xz_force_expressions(cfg_stat, span_offset_m=3.0)
    assert "[m/s]*(t-" not in exprs_stat["Fx"]
    assert "0.500000[m]" in exprs_stat["Fx"]
    assert "-1.500000[m]" in exprs_stat["Fx"]
    assert "0.100000[m]" in exprs_stat["Fx"]

    # 2. Moving disturbance
    vx, vy, vz = 0.20, -0.10, 0.05
    cfg_mov = C04DisturbanceConfig(
        center_m=(0.50, 0.10, 1.50),
        velocity_m_s=(vx, vy, vz),
        direction=(1.0, 0.0, 0.0),
        on_s=2.5,
        off_s=4.5,
        acceleration_m_s2=0.08,
        sigma_m=0.04,
    )
    exprs_mov = generate_c04_comsol_extruded_xz_force_expressions(cfg_mov, span_offset_m=3.0)
    fx_mov = exprs_mov["Fx"]

    # Verify vx -> x, vz -> y, vy -> z
    assert "(x-(0.500000[m]+0.200000[m/s]*(t-2.5000[s])))^2" in fx_mov
    assert "(y-((-1.500000[m]+0.050000[m/s]*(t-2.5000[s]))))^2" in fx_mov
    assert "(z-((0.100000[m]+-0.100000[m/s]*(t-2.5000[s]))))^2" in fx_mov


def test_f4_stationary_extruded_3d_force_expressions():
    """Verify stationary baseline (velocity=(0, 0, 0)) produces expressions without time drift."""
    config = C04DisturbanceConfig(
        center_m=(0.50, 0.10, 1.50),
        velocity_m_s=(0.0, 0.0, 0.0),
        direction=(0.0, 1.0, 0.0),
        on_s=2.0,
        off_s=4.0,
        acceleration_m_s2=0.05,
        sigma_m=0.03,
    )

    exprs = generate_c04_comsol_extruded_xz_force_expressions(config, span_offset_m=3.0)

    assert "Fx" in exprs and "Fy" in exprs and "Fz" in exprs
    fx = exprs["Fx"]
    assert "0.500000[m]" in fx
    assert "-1.500000[m]" in fx
    assert "0.100000[m]" in fx
    assert "[m/s]*(t-" not in fx

    # Direction: (0, 1, 0) maps to COMSOL VolumeForce (Fx=0, Fy=0, Fz=1) because (ex, ey, ez) -> (ex, ez, ey)
    assert fx.startswith("0.000000*")
    assert exprs["Fy"].startswith("0.000000*")
    assert exprs["Fz"].startswith("1.000000*")


def test_f4_moving_extruded_3d_force_expressions_velocity_mapping():
    """Verify moving disturbance velocity components map vx->x, vz->y, and vy->z with t_rel."""
    vx, vy, vz = 0.20, -0.10, 0.05
    config = C04DisturbanceConfig(
        center_m=(0.50, 0.10, 1.50),
        velocity_m_s=(vx, vy, vz),
        direction=(1.0, 0.0, 0.0),
        on_s=2.5,
        off_s=4.5,
        acceleration_m_s2=0.08,
        sigma_m=0.04,
    )

    exprs = generate_c04_comsol_extruded_xz_force_expressions(config, span_offset_m=3.0)
    fx = exprs["Fx"]

    # Canonical -> Extruded XZ COMSOL Mapping:
    expected_x = "(x-(0.500000[m]+0.200000[m/s]*(t-2.5000[s])))^2"
    assert expected_x in fx

    expected_y = "(y-((-1.500000[m]+0.050000[m/s]*(t-2.5000[s]))))^2"
    assert expected_y in fx

    expected_z = "(z-((0.100000[m]+-0.100000[m/s]*(t-2.5000[s]))))^2"
    assert expected_z in fx

    assert fx.startswith("1.000000*")
    assert exprs["Fy"].startswith("0.000000*")
    assert exprs["Fz"].startswith("0.000000*")


# ==============================================================================
# Feature F5: Safe NPZ File Context Management & File Descriptor Release
# ==============================================================================


def test_f5_cli_safe_npz_loading(tmp_path: Path):
    """Master verification test for Feature F5: Safe NPZ loading and file lock release.

    Requirements verified:
    1. Safe loading of NPZ files with context manager releasing Windows file locks immediately.
    2. Protection against unsafe pickle deserialization via allow_pickle=False.
    3. CLI commands observation-sample and reconstruction-eval release file locks immediately.
    """
    # 1. Direct NPZ context manager file lock release
    npz_file = tmp_path / "lock_check.npz"
    np.savez(npz_file, arr=np.ones((5, 5)))
    with np.load(npz_file, allow_pickle=False) as data:
        assert data["arr"].shape == (5, 5)
    # File must be immediately unlinked on Windows without PermissionError
    npz_file.unlink()
    assert not npz_file.exists()

    # 2. allow_pickle=False protection
    pickled_file = tmp_path / "pickle_guard.npz"
    np.savez(pickled_file, obj=np.array([{"test": 123}], dtype=object))
    with pytest.raises(ValueError, match="Object arrays cannot be loaded when allow_pickle=False"):
        with np.load(pickled_file, allow_pickle=False) as pdata:
            _ = pdata["obj"]
    pickled_file.unlink()

    # 3. CLI execution lock release
    ds_file = tmp_path / "cli_ds.npz"
    layout_file = tmp_path / "cli_layout.json"
    out_file = tmp_path / "cli_out.json"

    data = np.ones((2, 3, 10, 10), dtype=np.float32)
    np.savez(ds_file, data=data, x=np.linspace(0, 1, 10), y=np.linspace(0, 1, 10), channel_names=["u", "v", "p"])
    layout_file.write_text(
        json.dumps({
            "name": "grid",
            "dim": 2,
            "probes": [{"probe_id": "p0", "coords": [0.5, 0.5]}],
        }),
        encoding="utf-8",
    )
    rc = cli_main([
        "observation-sample",
        "--dataset", str(ds_file),
        "--layout", str(layout_file),
        "--out", str(out_file),
    ])
    assert rc == 0
    ds_file.unlink()
    assert not ds_file.exists()


def test_f5_npz_context_manager_releases_windows_file_lock(tmp_path: Path):
    """Verify that with np.load(...) context manager releases Windows file locks immediately."""
    npz_path = tmp_path / "test_sample.npz"
    test_data = np.arange(100, dtype=np.float64).reshape((10, 10))

    np.savez(npz_path, data=test_data, x=np.linspace(0, 1, 10), y=np.linspace(0, 1, 10))

    with np.load(npz_path, allow_pickle=False) as npz:
        loaded_data = np.array(npz["data"])
        assert loaded_data.shape == (10, 10)

    npz_path.unlink()
    assert not npz_path.exists()


def test_f5_npz_disallows_pickle(tmp_path: Path):
    """Verify that allow_pickle=False protects against insecure deserialization."""
    pickle_npz_path = tmp_path / "object_array.npz"
    obj_array = np.array([{"key": "value"}], dtype=object)
    np.savez(pickle_npz_path, data=obj_array)

    with pytest.raises(ValueError, match="Object arrays cannot be loaded when allow_pickle=False"):
        with np.load(pickle_npz_path, allow_pickle=False) as npz:
            _ = npz["data"]


def test_f5_cli_observation_sample_releases_file_lock(tmp_path: Path):
    """Verify cli observation-sample subcmd loads NPZ and releases file lock without descriptor leak."""
    dataset_path = tmp_path / "dataset_2d.npz"
    layout_path = tmp_path / "layout_2d.json"
    out_path = tmp_path / "sample_out.json"

    nx, ny = 20, 15
    data = np.ones((5, 3, ny, nx), dtype=np.float32)
    x = np.linspace(0.0, 2.0, nx)
    y = np.linspace(-0.5, 0.5, ny)
    np.savez(dataset_path, data=data, x=x, y=y, channel_names=["u", "v", "p"])

    layout_content = {
        "name": "test_grid",
        "dim": 2,
        "probes": [
            {"probe_id": "p0", "coords": [0.5, 0.0]},
            {"probe_id": "p1", "coords": [1.0, 0.1]},
        ],
    }
    layout_path.write_text(json.dumps(layout_content), encoding="utf-8")

    rc = cli_main([
        "observation-sample",
        "--dataset", str(dataset_path),
        "--layout", str(layout_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()

    dataset_path.unlink()
    assert not dataset_path.exists()


def test_f5_cli_reconstruction_eval_releases_file_lock(tmp_path: Path):
    """Verify cli reconstruction-eval subcmd loads both pred and true NPZ with safe context."""
    pred_path = tmp_path / "pred_field.npz"
    true_path = tmp_path / "true_field.npz"
    out_path = tmp_path / "recon_metrics.json"

    field = np.ones((3, 10, 10), dtype=np.float32)
    mask = np.ones((10, 10), dtype=bool)
    np.savez(pred_path, data=field, channel_names=["u", "v", "p"])
    np.savez(true_path, data=field, fluid_mask=mask, channel_names=["u", "v", "p"])

    rc = cli_main([
        "reconstruction-eval",
        "--pred", str(pred_path),
        "--true", str(true_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()

    pred_path.unlink()
    true_path.unlink()
    assert not pred_path.exists()
    assert not true_path.exists()
