"""Unit tests for hydrodynamic disturbance inversion and parameter identification benchmark."""

import numpy as np
import pytest

from hydro_disturb_bench.disturbance_inversion import (
    AnomalyEnergyDetector,
    ConvectiveTimeOfFlightEstimator,
    DisturbanceGroundTruth,
    DisturbancePrediction,
    evaluate_disturbance_inversion,
    evaluate_inversion_batch,
)


def test_perfect_inversion():
    """Verify that a perfect prediction receives 1.0 composite score and zero errors."""
    truth = DisturbanceGroundTruth(
        active=True,
        t_on=181.0,
        t_off=183.0,
        center=(0.15, 0.12),
        amplitude=0.005,
        direction=(0.0, 1.0),
    )
    pred = DisturbancePrediction(
        detected=True,
        t_detect=181.1,
        t_on=181.0,
        t_off=183.0,
        center=(0.15, 0.12),
        amplitude=0.005,
        direction=(0.0, 1.0),
    )

    metrics = evaluate_disturbance_inversion(truth, pred, characteristic_length_D=0.01)
    assert metrics.detected_correctly is True
    assert metrics.false_alarm is False
    assert metrics.missed_detection is False
    assert metrics.detection_delay_sec == pytest.approx(0.1, abs=1e-6)
    assert metrics.localization_error_m == pytest.approx(0.0, abs=1e-6)
    assert metrics.amplitude_error_rel_pct == pytest.approx(0.0, abs=1e-6)
    assert metrics.direction_angle_error_deg == pytest.approx(0.0, abs=1e-6)
    assert metrics.composite_inversion_score == pytest.approx(1.0, abs=1e-4)


def test_imperfect_inversion_errors():
    """Verify localization, timing, and amplitude errors computation."""
    truth = DisturbanceGroundTruth(
        active=True,
        t_on=181.0,
        t_off=183.0,
        center=(0.15, 0.12),
        amplitude=0.010,
        direction=(0.0, 1.0),
    )
    # Predicted with 0.03 m spatial offset, 10% amplitude error, 0.2s timing offset, and 45 degree angle error
    pred = DisturbancePrediction(
        detected=True,
        t_detect=181.5,
        t_on=181.2,
        t_off=183.2,
        center=(0.18, 0.12),  # +0.03m in x
        amplitude=0.011,      # +10%
        direction=(1.0, 1.0), # 45 degrees relative to (0, 1)
    )

    metrics = evaluate_disturbance_inversion(truth, pred, characteristic_length_D=0.30)
    assert metrics.detected_correctly is True
    assert metrics.detection_delay_sec == pytest.approx(0.5, abs=1e-6)
    assert metrics.localization_error_m == pytest.approx(0.03, abs=1e-6)
    assert metrics.localization_error_D == pytest.approx(0.10, abs=1e-6)
    assert metrics.amplitude_error_rel_pct == pytest.approx(10.0, abs=1e-4)
    assert metrics.direction_angle_error_deg == pytest.approx(45.0, abs=1e-4)
    assert 0.0 < metrics.composite_inversion_score < 1.0


def test_missed_detection_and_false_alarm():
    """Verify handling of false alarms and missed detections."""
    truth_active = DisturbanceGroundTruth(active=True, t_on=181.0, t_off=183.0)
    pred_missed = DisturbancePrediction(detected=False)

    metrics_miss = evaluate_disturbance_inversion(truth_active, pred_missed)
    assert metrics_miss.missed_detection is True
    assert metrics_miss.detected_correctly is False
    assert metrics_miss.composite_inversion_score == 0.0

    truth_inactive = DisturbanceGroundTruth(active=False)
    pred_false_alarm = DisturbancePrediction(detected=True, t_detect=181.0)
    metrics_fa = evaluate_disturbance_inversion(truth_inactive, pred_false_alarm)
    assert metrics_fa.false_alarm is True
    assert metrics_fa.detected_correctly is False
    assert metrics_fa.composite_inversion_score == 0.0


def test_moving_disturbance_trajectory_error():
    """Verify trajectory tracking error for dynamic moving sources."""
    truth = DisturbanceGroundTruth(
        active=True,
        t_on=0.0,
        t_off=2.0,
        center=(0.0, 0.0),
        amplitude=0.005,
        velocity=(0.1, 0.0),  # moves 0.2m in x over 2s
    )
    # Prediction has correct velocity but offset center by 0.05m
    pred = DisturbancePrediction(
        detected=True,
        center=(0.05, 0.0),
        velocity=(0.1, 0.0),
    )

    metrics = evaluate_disturbance_inversion(truth, pred)
    assert metrics.trajectory_tracking_error_m == pytest.approx(0.05, abs=1e-6)


def test_anomaly_energy_detector_and_time_of_flight():
    """Verify baseline energy detector and convective time-of-flight estimation."""
    # 10 timestamps, 5 probes, 2 channels
    T, K, C = 10, 5, 2
    times = [180.0 + i * 0.5 for i in range(T)]

    # Baseline data has mean 0 and std 0.01
    baseline = np.zeros((K, C))
    baseline_std = np.full((K, C), 0.01)

    detector = AnomalyEnergyDetector(baseline, baseline_std, threshold_sigmas=3.0)

    # Sequence with quiet frames, then sudden spike at t = 182.0 (index 4)
    obs_seq = np.random.normal(0.0, 0.005, size=(T, K, C))
    obs_seq[4:, :, 1] += 0.08  # 8-sigma jump in v channel

    detected, t_det = detector.detect_sequence(obs_seq, times)
    assert detected is True
    assert t_det == pytest.approx(182.0, abs=1e-6)

    # Time-of-flight estimator: probe at x = 0.90 m, U0 = 0.50, U_c = 0.85 * 0.50 = 0.425 m/s
    tof = ConvectiveTimeOfFlightEstimator(u_infinity=0.50, convective_ratio=0.85)
    # If disturbance started at 181.0 and detected at 182.0 (dt = 1.0s):
    # x_source = 0.90 - 0.425 * 1.0 = 0.475 m
    est_x = tof.estimate_source_x(x_sensor=0.90, t_detect=182.0, t_on=181.0)
    assert est_x == pytest.approx(0.475, abs=1e-6)


def test_inversion_batch_aggregation():
    """Verify batch evaluation metrics aggregation."""
    truth_1 = DisturbanceGroundTruth(active=True, t_on=181.0, t_off=183.0, center=(0.1, 0.1), amplitude=0.01)
    pred_1 = DisturbancePrediction(detected=True, t_detect=181.2, t_on=181.0, t_off=183.0, center=(0.1, 0.1), amplitude=0.01)

    truth_2 = DisturbanceGroundTruth(active=False)
    pred_2 = DisturbancePrediction(detected=False)

    truth_3 = DisturbanceGroundTruth(active=True, t_on=181.0, t_off=183.0)
    pred_3 = DisturbancePrediction(detected=False)  # Miss

    batch_res = evaluate_inversion_batch([
        (truth_1, pred_1),
        (truth_2, pred_2),
        (truth_3, pred_3),
    ])

    assert batch_res["total_cases"] == 3
    assert batch_res["true_positives"] == 1
    assert batch_res["false_positives"] == 0
    assert batch_res["false_negatives"] == 1
    assert batch_res["true_negatives"] == 1
    assert batch_res["precision"] == 1.0
    assert batch_res["recall"] == 0.5
    assert batch_res["f1_score"] == pytest.approx(2.0 / 3.0, abs=1e-4)
