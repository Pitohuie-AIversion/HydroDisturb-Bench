"""Probabilistic Evaluation and Sparse-to-Full Reconstruction Metrics.

Provides:
- Continuous Ranked Probability Score (CRPS) for ensemble world model forecasts.
- Spread-Skill Ratio (SSR) for uncertainty calibration assessment.
- Full wake field reconstruction metrics from sparse observations (Rel-L2, PSNR, Pearson R)
  strictly restricted to the fluid subdomain.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def compute_crps_ensemble(
    ensemble_predictions: np.ndarray,
    ground_truth: np.ndarray,
    fluid_mask: np.ndarray | None = None,
) -> float:
    """Computes the Continuous Ranked Probability Score (CRPS) for an ensemble forecast.

    Args:
        ensemble_predictions: Shape (M, ..., N_points) where M is the ensemble size (M >= 1).
        ground_truth: Shape (..., N_points) ground truth field in physical units.
        fluid_mask: Boolean mask of shape matching ground_truth where True is valid fluid.

    Returns:
        crps_value: Mean CRPS in physical units over all fluid points.
    """
    if ensemble_predictions.ndim < ground_truth.ndim + 1:
        raise ValueError(
            f"ensemble_predictions must have at least one more leading dimension than ground_truth: "
            f"{ensemble_predictions.shape} vs {ground_truth.shape}"
        )

    m = ensemble_predictions.shape[0]
    if m == 1:
        # Deterministic degenerate case: CRPS equals absolute error
        diff = np.abs(ensemble_predictions[0] - ground_truth)
        if fluid_mask is not None:
            valid = fluid_mask & np.isfinite(diff)
            return float(np.mean(diff[valid])) if np.any(valid) else 0.0
        valid = np.isfinite(diff)
        return float(np.mean(diff[valid])) if np.any(valid) else 0.0

    # Term 1: E[|y - x|] = (1/M) * sum_m |x_m - y|
    abs_errors = np.abs(ensemble_predictions - ground_truth[None, ...])  # (M, ...)
    term1 = np.mean(abs_errors, axis=0)  # (...)

    # Term 2: 0.5 * E[|x - x'|] = (1 / (2 * M^2)) * sum_{i,j} |x_i - x_j|
    # Vectorized pairwise differences along axis 0
    diff_matrix = np.abs(ensemble_predictions[:, None, ...] - ensemble_predictions[None, :, ...])
    term2 = 0.5 * np.mean(diff_matrix, axis=(0, 1))  # (...)

    pointwise_crps = term1 - term2

    if fluid_mask is not None:
        valid = fluid_mask & np.isfinite(pointwise_crps)
        return float(np.mean(pointwise_crps[valid])) if np.any(valid) else 0.0

    valid = np.isfinite(pointwise_crps)
    return float(np.mean(pointwise_crps[valid])) if np.any(valid) else 0.0


def compute_spread_skill_ratio(
    ensemble_predictions: np.ndarray,
    ground_truth: np.ndarray,
    fluid_mask: np.ndarray | None = None,
) -> float:
    """Computes the Spread-Skill Ratio (SSR) for an ensemble forecast.

    SSR = sqrt(E[Var(x)]) / RMSE(mean(x), y)
    Well-calibrated ensembles have SSR ~ 1.0 (spread matches error).
    Overconfident ensembles have SSR < 1.0; underconfident have SSR > 1.0.
    """
    m = ensemble_predictions.shape[0]
    if m < 2:
        return 0.0

    ens_mean = np.mean(ensemble_predictions, axis=0)
    ens_var = np.var(ensemble_predictions, axis=0, ddof=1)
    sq_err = (ens_mean - ground_truth) ** 2

    if fluid_mask is not None:
        valid = fluid_mask & np.isfinite(ens_var) & np.isfinite(sq_err)
    else:
        valid = np.isfinite(ens_var) & np.isfinite(sq_err)

    if not np.any(valid):
        return 0.0

    mean_var = np.mean(ens_var[valid])
    spread = math.sqrt(max(0.0, float(mean_var)))

    mse = np.mean(sq_err[valid])
    rmse = math.sqrt(max(0.0, float(mse)))

    if rmse < 1e-12:
        return 1.0 if spread < 1e-12 else float("inf")

    return spread / rmse


def evaluate_field_reconstruction(
    reconstructed_field: np.ndarray,
    ground_truth_field: np.ndarray,
    fluid_mask: np.ndarray | None = None,
    channel_names: tuple[str, ...] = ("u", "v", "w", "p"),
) -> dict[str, Any]:
    """Computes fluid-restricted reconstruction metrics between reconstructed and true fields.

    Args:
        reconstructed_field: Shape (..., C, Ny, Nx) or (..., C, Nz, Ny, Nx).
        ground_truth_field: Shape matching reconstructed_field.
        fluid_mask: Spatial boolean mask where True indicates fluid domain.
        channel_names: Channel ordering matching dimension C.

    Returns:
        Structured evaluation metrics per channel and aggregate.
    """
    if reconstructed_field.shape != ground_truth_field.shape:
        raise ValueError(
            f"Shape mismatch: {reconstructed_field.shape} vs {ground_truth_field.shape}"
        )

    n_channels = reconstructed_field.shape[-len(channel_names) :]  # last C dims or channel dim
    # Channel dimension is indexed at -(spatial_dim + 1)
    # 2D has 2 spatial dims -> channel is at index -3
    # 3D has 3 spatial dims -> channel is at index -4
    spatial_dim = 2 if fluid_mask is None or fluid_mask.ndim == 2 else fluid_mask.ndim
    c_axis = -(spatial_dim + 1)
    c_count = reconstructed_field.shape[c_axis]

    metrics: dict[str, Any] = {}
    total_l2_sq_num = 0.0
    total_l2_sq_den = 0.0

    for c in range(min(c_count, len(channel_names))):
        c_name = channel_names[c]
        pred_c = np.take(reconstructed_field, c, axis=c_axis)
        true_c = np.take(ground_truth_field, c, axis=c_axis)

        if fluid_mask is not None:
            valid = fluid_mask & np.isfinite(pred_c) & np.isfinite(true_c)
        else:
            valid = np.isfinite(pred_c) & np.isfinite(true_c)

        if not np.any(valid):
            continue

        p_vals = pred_c[valid]
        t_vals = true_c[valid]

        diff = p_vals - t_vals
        mae = float(np.mean(np.abs(diff)))
        rmse = float(np.sqrt(np.mean(diff**2)))

        norm_diff = np.sum(diff**2)
        norm_true = np.sum(t_vals**2)
        rel_l2 = float(np.sqrt(norm_diff / max(norm_true, 1e-12)))

        total_l2_sq_num += norm_diff
        total_l2_sq_den += norm_true

        data_range = float(np.max(t_vals) - np.min(t_vals))
        psnr = (
            20.0 * math.log10(data_range / max(rmse, 1e-12))
            if data_range > 1e-12 and rmse > 0
            else 100.0
        )

        # Pearson correlation
        if np.std(p_vals) > 1e-12 and np.std(t_vals) > 1e-12:
            r = float(np.corrcoef(p_vals, t_vals)[0, 1])
        else:
            r = 1.0 if rmse < 1e-6 else 0.0

        metrics[c_name] = {
            "mae": mae,
            "rmse": rmse,
            "rel_l2": rel_l2,
            "psnr_db": psnr,
            "pearson_r": r,
        }

    overall_rel_l2 = float(np.sqrt(total_l2_sq_num / max(total_l2_sq_den, 1e-12)))
    metrics["overall_rel_l2"] = overall_rel_l2
    return metrics
