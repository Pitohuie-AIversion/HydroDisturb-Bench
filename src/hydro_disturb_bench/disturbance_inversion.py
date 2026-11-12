"""Hydrodynamic disturbance inversion, parameter identification, and anomaly detection benchmark.

This module formalizes the inverse problem for HydroDisturb-Bench:
Given sparse sensor observations y(t) = H(q(t)) or reconstructed wake fields,
invert and estimate the hidden hydrodynamic disturbance properties:
- Disturbance event detection and detection latency (t_detect - t_on)
- Spatial localization error ||c_pred - c_true|| in meters and dimensionless (x/D)
- Temporal onset and duration errors (|t_on_pred - t_on|, |Delta_t_pred - Delta_t|)
- Acceleration amplitude error (|A_pred - A_true| / A_true)
- Direction vector alignment error arccos(n_pred . n_true) in degrees
- Dynamic trajectory tracking error for moving disturbance sources

Includes baseline inverse estimators:
- AnomalyEnergyDetector (thresholded probe energy anomaly detector)
- ConvectiveTimeOfFlightEstimator (estimates source location via wake convection velocity)
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass
class DisturbanceGroundTruth:
    """Ground truth hydrodynamic disturbance definition."""

    active: bool
    t_on: float = 0.0
    t_off: float = 0.0
    center: tuple[float, ...] = (0.0, 0.0)
    amplitude: float = 0.0
    direction: tuple[float, ...] = (0.0, 1.0)
    velocity: tuple[float, ...] | None = None
    sigma: float = 0.01
    dim: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DisturbanceGroundTruth:
        return cls(
            active=bool(data.get("active", True)),
            t_on=float(data.get("t_on", 0.0)),
            t_off=float(data.get("t_off", 0.0)),
            center=tuple(float(c) for c in data.get("center", (0.0, 0.0))),
            amplitude=float(data.get("amplitude", 0.0)),
            direction=tuple(float(d) for d in data.get("direction", (0.0, 1.0))),
            velocity=tuple(float(v) for v in data["velocity"]) if data.get("velocity") else None,
            sigma=float(data.get("sigma", 0.01)),
            dim=int(data.get("dim", 2)),
        )


@dataclass
class DisturbancePrediction:
    """Estimated / inverted disturbance parameters from an AI model or inverse algorithm."""

    detected: bool
    t_detect: float | None = None
    t_on: float | None = None
    t_off: float | None = None
    center: tuple[float, ...] | None = None
    amplitude: float | None = None
    direction: tuple[float, ...] | None = None
    velocity: tuple[float, ...] | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DisturbancePrediction:
        return cls(
            detected=bool(data.get("detected", False)),
            t_detect=float(data["t_detect"]) if data.get("t_detect") is not None else None,
            t_on=float(data["t_on"]) if data.get("t_on") is not None else None,
            t_off=float(data["t_off"]) if data.get("t_off") is not None else None,
            center=tuple(float(c) for c in data["center"]) if data.get("center") is not None else None,
            amplitude=float(data["amplitude"]) if data.get("amplitude") is not None else None,
            direction=tuple(float(d) for d in data["direction"]) if data.get("direction") is not None else None,
            velocity=tuple(float(v) for v in data["velocity"]) if data.get("velocity") is not None else None,
            confidence=float(data.get("confidence", 1.0)),
        )


@dataclass
class InversionMetrics:
    """Standardized performance metrics for disturbance parameter identification."""

    # Event detection metrics
    detected_correctly: bool
    false_alarm: bool
    missed_detection: bool
    detection_delay_sec: float | None

    # Spatial localization
    localization_error_m: float | None
    localization_error_D: float | None

    # Temporal parameters
    t_on_error_sec: float | None
    duration_error_sec: float | None

    # Amplitude and direction
    amplitude_error_abs: float | None
    amplitude_error_rel_pct: float | None
    direction_angle_error_deg: float | None

    # Dynamic trajectory (for moving disturbance)
    trajectory_tracking_error_m: float | None

    # Overall composite inversion score in [0.0, 1.0]
    composite_inversion_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_disturbance_inversion(
    truth: DisturbanceGroundTruth,
    pred: DisturbancePrediction,
    characteristic_length_D: float = 0.30,
) -> InversionMetrics:
    """Compute rigorous inversion and parameter identification metrics.

    Parameters
    ----------
    truth : DisturbanceGroundTruth
        True physical disturbance parameters.
    pred : DisturbancePrediction
        Predicted disturbance parameters from an AI world model or inverse solver.
    characteristic_length_D : float
        Characteristic cylinder diameter D in meters (0.30 for C04, 0.01 for Re=100 M3).

    Returns
    -------
    InversionMetrics
        Structured evaluation metrics.
    """
    # 1. Detection classification
    if not truth.active:
        # Negative case (no disturbance)
        detected_correctly = not pred.detected
        false_alarm = pred.detected
        missed = False
        delay = None
        composite_score = 1.0 if detected_correctly else 0.0
        return InversionMetrics(
            detected_correctly=detected_correctly,
            false_alarm=false_alarm,
            missed_detection=missed,
            detection_delay_sec=delay,
            localization_error_m=None,
            localization_error_D=None,
            t_on_error_sec=None,
            duration_error_sec=None,
            amplitude_error_abs=None,
            amplitude_error_rel_pct=None,
            direction_angle_error_deg=None,
            trajectory_tracking_error_m=None,
            composite_inversion_score=composite_score,
        )

    # Positive case (disturbance occurred)
    if not pred.detected:
        return InversionMetrics(
            detected_correctly=False,
            false_alarm=False,
            missed_detection=True,
            detection_delay_sec=None,
            localization_error_m=None,
            localization_error_D=None,
            t_on_error_sec=None,
            duration_error_sec=None,
            amplitude_error_abs=None,
            amplitude_error_rel_pct=None,
            direction_angle_error_deg=None,
            trajectory_tracking_error_m=None,
            composite_inversion_score=0.0,
        )

    # Detection succeeded
    detected_correctly = True
    false_alarm = False
    missed = False

    # Detection latency
    delay = (pred.t_detect - truth.t_on) if pred.t_detect is not None else None

    # Spatial localization error
    loc_err_m: float | None = None
    loc_err_D: float | None = None
    if pred.center is not None:
        dim_min = min(len(pred.center), len(truth.center))
        c_pred = np.array(pred.center[:dim_min], dtype=float)
        c_true = np.array(truth.center[:dim_min], dtype=float)
        loc_err_m = float(np.linalg.norm(c_pred - c_true))
        loc_err_D = float(loc_err_m / characteristic_length_D) if characteristic_length_D > 0 else None

    # Timing errors
    t_on_err: float | None = None
    dur_err: float | None = None
    if pred.t_on is not None:
        t_on_err = abs(float(pred.t_on - truth.t_on))
    if pred.t_on is not None and pred.t_off is not None:
        true_dur = truth.t_off - truth.t_on
        pred_dur = pred.t_off - pred.t_on
        dur_err = abs(float(pred_dur - true_dur))

    # Amplitude errors
    amp_abs: float | None = None
    amp_rel_pct: float | None = None
    if pred.amplitude is not None:
        amp_abs = abs(float(pred.amplitude - truth.amplitude))
        if truth.amplitude > 0:
            amp_rel_pct = float(amp_abs / truth.amplitude * 100.0)

    # Direction angle error
    dir_angle_deg: float | None = None
    if pred.direction is not None and truth.direction is not None:
        dim_min = min(len(pred.direction), len(truth.direction))
        d_pred = np.array(pred.direction[:dim_min], dtype=float)
        d_true = np.array(truth.direction[:dim_min], dtype=float)
        norm_p = np.linalg.norm(d_pred)
        norm_t = np.linalg.norm(d_true)
        if norm_p > 1e-12 and norm_t > 1e-12:
            dot_prod = np.clip(np.dot(d_pred, d_true) / (norm_p * norm_t), -1.0, 1.0)
            dir_angle_deg = float(math.degrees(math.acos(dot_prod)))

    # Trajectory tracking error (if moving disturbance)
    traj_err: float | None = None
    if truth.velocity is not None and pred.velocity is not None and pred.center is not None:
        # Sample 10 time points over [t_on, t_off]
        ts = np.linspace(truth.t_on, truth.t_off, 10)
        c_true_0 = np.array(truth.center, dtype=float)
        v_true = np.array(truth.velocity, dtype=float)
        c_pred_0 = np.array(pred.center, dtype=float)
        v_pred = np.array(pred.velocity, dtype=float)

        dim_min = min(len(c_true_0), len(c_pred_0), len(v_true), len(v_pred))
        diffs = []
        for t in ts:
            dt = t - truth.t_on
            pos_true = c_true_0[:dim_min] + v_true[:dim_min] * dt
            pos_pred = c_pred_0[:dim_min] + v_pred[:dim_min] * dt
            diffs.append(np.linalg.norm(pos_pred - pos_true))
        traj_err = float(np.mean(diffs))

    # Composite normalized inversion score in [0.0, 1.0]
    # Penalizes localization error (> 2D -> 0), amplitude error (> 100% -> 0),
    # timing error (> duration -> 0), and angle error (> 90 deg -> 0)
    score_components = []

    if loc_err_D is not None:
        score_loc = max(0.0, 1.0 - loc_err_D / 2.0)
        score_components.append(score_loc)

    if amp_rel_pct is not None:
        score_amp = max(0.0, 1.0 - amp_rel_pct / 100.0)
        score_components.append(score_amp)

    if t_on_err is not None:
        true_dur = max(0.1, truth.t_off - truth.t_on)
        score_time = max(0.0, 1.0 - t_on_err / true_dur)
        score_components.append(score_time)

    if dir_angle_deg is not None:
        score_dir = max(0.0, 1.0 - dir_angle_deg / 90.0)
        score_components.append(score_dir)

    composite_score = float(np.mean(score_components)) if score_components else 1.0

    return InversionMetrics(
        detected_correctly=detected_correctly,
        false_alarm=false_alarm,
        missed_detection=missed,
        detection_delay_sec=delay,
        localization_error_m=loc_err_m,
        localization_error_D=loc_err_D,
        t_on_error_sec=t_on_err,
        duration_error_sec=dur_err,
        amplitude_error_abs=amp_abs,
        amplitude_error_rel_pct=amp_rel_pct,
        direction_angle_error_deg=dir_angle_deg,
        trajectory_tracking_error_m=traj_err,
        composite_inversion_score=composite_score,
    )


class AnomalyEnergyDetector:
    """Baseline heuristic: energy anomaly detector across sparse probe measurements.

    Monitors deviation of sensor readings from the unperturbed baseline limit.
    Triggers detection when the normalized anomaly score exceeds a threshold.
    """

    def __init__(
        self,
        baseline_mean: np.ndarray,
        baseline_std: np.ndarray,
        threshold_sigmas: float = 3.5,
    ):
        self.mean = np.asarray(baseline_mean, dtype=float)
        self.std = np.maximum(np.asarray(baseline_std, dtype=float), 1e-6)
        self.threshold = float(threshold_sigmas)

    def score_frame(self, observation: np.ndarray) -> float:
        """Compute normalized anomaly energy for a single time frame.

        Parameters
        ----------
        observation : np.ndarray
            Probe observation array of shape (K, C).

        Returns
        -------
        float
            Normalized anomaly z-score.
        """
        obs = np.asarray(observation, dtype=float)
        z = (obs - self.mean) / self.std
        # Mean squared z-score
        return float(np.mean(z**2))

    def detect_sequence(
        self,
        observations: np.ndarray,
        times: Sequence[float],
    ) -> tuple[bool, float | None]:
        """Detect occurrence time across a sequence of observations.

        Parameters
        ----------
        observations : np.ndarray
            Sequence of shape (T, K, C).
        times : Sequence[float]
            Timestamps of length T.

        Returns
        -------
        tuple[bool, float | None]
            (detected, t_detect)
        """
        obs_seq = np.asarray(observations, dtype=float)
        for i, t in enumerate(times):
            score = self.score_frame(obs_seq[i])
            if score >= self.threshold:
                return True, float(t)
        return False, None


class ConvectiveTimeOfFlightEstimator:
    """Estimates upstream disturbance source position via convective wake transport.

    In cylinder wake flow, vortex structures convect downstream at U_c ~ 0.85 * U_0.
    Given detection at sensor location x_sensor at time t_detect, and known onset t_on:
    x_source = x_sensor - U_c * (t_detect - t_on).
    """

    def __init__(self, u_infinity: float = 0.50, convective_ratio: float = 0.85):
        self.u_c = float(u_infinity * convective_ratio)

    def estimate_source_x(
        self,
        x_sensor: float,
        t_detect: float,
        t_on: float,
    ) -> float:
        """Estimate streamwise x coordinate of the source."""
        dt = max(0.0, float(t_detect - t_on))
        return float(x_sensor - self.u_c * dt)


def evaluate_inversion_batch(
    evaluations: Sequence[tuple[DisturbanceGroundTruth, DisturbancePrediction]],
    characteristic_length_D: float = 0.30,
) -> dict[str, Any]:
    """Aggregate inversion performance across a batch of test cases.

    Computes:
    - Precision, Recall, F1 score
    - Mean detection delay (s)
    - Mean localization error (m and normalized by D)
    - Mean relative amplitude error (%)
    - Mean direction angular error (deg)
    - Mean composite inversion score
    """
    total = len(evaluations)
    if total == 0:
        return {"total_cases": 0}

    metrics_list = [
        evaluate_disturbance_inversion(truth, pred, characteristic_length_D)
        for truth, pred in evaluations
    ]

    tp = sum(1 for m in metrics_list if m.detected_correctly and not m.false_alarm and m.detection_delay_sec is not None)
    fp = sum(1 for m in metrics_list if m.false_alarm)
    fn = sum(1 for m in metrics_list if m.missed_detection)
    tn = sum(1 for m in metrics_list if m.detected_correctly and not m.false_alarm and m.detection_delay_sec is None)

    precision = float(tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = float(tp / (tp + fn)) if (tp + fn) > 0 else 1.0
    f1 = float(2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    delays = [m.detection_delay_sec for m in metrics_list if m.detection_delay_sec is not None]
    loc_errs_m = [m.localization_error_m for m in metrics_list if m.localization_error_m is not None]
    loc_errs_D = [m.localization_error_D for m in metrics_list if m.localization_error_D is not None]
    amp_errs = [m.amplitude_error_rel_pct for m in metrics_list if m.amplitude_error_rel_pct is not None]
    dir_errs = [m.direction_angle_error_deg for m in metrics_list if m.direction_angle_error_deg is not None]
    scores = [m.composite_inversion_score for m in metrics_list]

    return {
        "total_cases": total,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "mean_detection_delay_sec": float(np.mean(delays)) if delays else None,
        "mean_localization_error_m": float(np.mean(loc_errs_m)) if loc_errs_m else None,
        "mean_localization_error_D": float(np.mean(loc_errs_D)) if loc_errs_D else None,
        "mean_amplitude_error_rel_pct": float(np.mean(amp_errs)) if amp_errs else None,
        "mean_direction_angle_error_deg": float(np.mean(dir_errs)) if dir_errs else None,
        "mean_composite_inversion_score": float(np.mean(scores)),
    }
