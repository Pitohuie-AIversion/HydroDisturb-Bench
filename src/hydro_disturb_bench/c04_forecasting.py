"""C04 World Model Baseline Forecasting and Evaluation.

Implements history-only direct forecasting baselines (persistence, last-two-linear)
and evaluation metrics for both C04 2D slice sequences (T, 4, Ny, Nx) and
3D volume sequences (T, 4, Nz, Ny, Nx).

Preserves physical units: velocity channels (u, v, w in m/s) and pressure (p in Pa)
are evaluated independently without dimensional mixing. Fluid-domain metrics
explicitly account for the cylinder solid mask.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .c04_windows import C04WindowIndex
from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .sampling_3d import C04_CHANNELS, C04_UNITS

C04_METHODS = ("persistence", "last_two_linear")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelValidationError(message)


def _input(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidParameterError(message)


def c04_forecast_from_history(
    history: np.ndarray,
    history_times: np.ndarray,
    prediction_times: np.ndarray,
    *,
    method: str = "persistence",
) -> np.ndarray:
    """Predict future flow fields directly from observed history states.

    Parameters
    ----------
    history : np.ndarray
        Array shaped (L, 4, Ny, Nx) or (L, 4, Nz, Ny, Nx).
    history_times : np.ndarray
        1D array of shape (L,) containing strictly increasing physical times.
    prediction_times : np.ndarray
        1D array of shape (H,) containing strictly increasing prediction times,
        all strictly greater than history_times[-1].
    method : str
        'persistence' or 'last_two_linear'.

    Returns
    -------
    np.ndarray
        Predicted fields shaped (H, 4, ...).
    """
    _input(
        method in C04_METHODS, f"Unknown forecast method: {method!r}, must be one of {C04_METHODS}"
    )

    history = np.asarray(history, dtype=np.float32)
    history_times = np.asarray(history_times, dtype=np.float64)
    prediction_times = np.asarray(prediction_times, dtype=np.float64)

    _require(
        history.ndim in (4, 5), f"history must have 4 or 5 dimensions, got shape {history.shape}"
    )
    _require(
        history.shape[1] == 4, f"history must have 4 channels (u, v, w, p), got {history.shape[1]}"
    )
    _require(
        history.shape[0] == len(history_times), "history frames must match history_times length"
    )
    _require(len(history_times) >= 1, "history must contain at least 1 frame")
    _require(len(prediction_times) >= 1, "prediction_times must contain at least 1 frame")

    _require(np.isfinite(history).all(), "history contains non-finite (NaN/Inf) values")
    _require(np.isfinite(history_times).all(), "history_times contains non-finite values")
    _require(np.isfinite(prediction_times).all(), "prediction_times contains non-finite values")

    if len(history_times) > 1:
        _require(np.all(np.diff(history_times) > 0), "history_times must be strictly increasing")
    if len(prediction_times) > 1:
        _require(
            np.all(np.diff(prediction_times) > 0), "prediction_times must be strictly increasing"
        )

    leads = prediction_times - history_times[-1]
    _require(
        np.all(leads > 0), "All prediction_times must be strictly greater than last history time"
    )

    num_preds = len(prediction_times)
    spatial_shape = history.shape[2:]
    out_shape = (num_preds, 4, *spatial_shape)

    if method == "persistence":
        # Repeat the last observed state
        return np.broadcast_to(history[-1:], out_shape).copy()

    if method == "last_two_linear":
        _input(len(history_times) >= 2, "last_two_linear requires at least 2 history frames")
        dt_hist = history_times[-1] - history_times[-2]
        _require(dt_hist > 0, f"History time step must be positive, got {dt_hist}")

        slope = (history[-1] - history[-2]) / dt_hist
        # Expand leads to match broadcast shape (H, 1, 1, ...)
        lead_reshape = (num_preds,) + (1,) * (history.ndim - 1)
        leads_arr = leads.reshape(lead_reshape).astype(np.float32)

        pred = np.broadcast_to(history[-1:], out_shape).copy()
        pred += leads_arr * slope[None]
        return pred

    raise InvalidParameterError(f"Unhandled method: {method}")


def score_c04_forecast(
    prediction: np.ndarray,
    target: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute channel-wise evaluation metrics for C04 forecasts.

    Evaluates fluid points (excluding solid obstacle). Metrics retain physical
    units (u, v, w in m/s, p in Pa).

    Returns
    -------
    dict
        Dictionary containing per-channel MAE, RMSE, RelL2, SAE, SSE, and point count.
    """
    prediction = np.asarray(prediction, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)

    _require(
        prediction.shape == target.shape, f"Shape mismatch: {prediction.shape} vs {target.shape}"
    )
    _require(prediction.ndim in (4, 5) and prediction.shape[1] == 4, "Must have shape (H, 4, ...)")
    _require(np.isfinite(prediction).all(), "prediction contains non-finite values")
    _require(np.isfinite(target).all(), "target contains non-finite values")

    spatial_shape = target.shape[2:]
    H = target.shape[0]

    if solid_mask is not None:
        mask = np.asarray(solid_mask, dtype=bool)
        _require(
            mask.shape == spatial_shape,
            f"solid_mask shape {mask.shape} != spatial shape {spatial_shape}",
        )
        fluid_mask = ~mask
    else:
        fluid_mask = np.ones(spatial_shape, dtype=bool)

    num_fluid_pts = int(np.count_nonzero(fluid_mask))
    _require(num_fluid_pts > 0, "No valid fluid points to evaluate")

    channel_metrics: dict[str, dict[str, Any]] = {}
    errors = prediction - target

    for c_idx, ch_name in enumerate(C04_CHANNELS):
        err_c = errors[:, c_idx]  # Shape: (H, ...)
        tgt_c = target[:, c_idx]  # Shape: (H, ...)

        # Extract fluid points for all lead frames
        # Reshape to (H, total_spatial) and slice with flattened fluid mask
        fluid_flat = fluid_mask.ravel()
        err_flat = err_c.reshape((H, -1))[:, fluid_flat]
        tgt_flat = tgt_c.reshape((H, -1))[:, fluid_flat]

        sae_per_lead = np.sum(np.abs(err_flat), axis=1)  # (H,)
        sse_per_lead = np.sum(np.square(err_flat), axis=1)  # (H,)
        tgt_sq_sum = np.sum(np.square(tgt_flat), axis=1)  # (H,)

        mae_per_lead = sae_per_lead / num_fluid_pts
        rmse_per_lead = np.sqrt(sse_per_lead / num_fluid_pts)
        rel_l2_per_lead = np.where(tgt_sq_sum > 1e-12, np.sqrt(sse_per_lead / tgt_sq_sum), 0.0)

        channel_metrics[ch_name] = {
            "unit": C04_UNITS[c_idx],
            "fluid_points": num_fluid_pts,
            "mae_per_lead": mae_per_lead.tolist(),
            "rmse_per_lead": rmse_per_lead.tolist(),
            "rel_l2_per_lead": rel_l2_per_lead.tolist(),
            "mean_mae": float(np.mean(mae_per_lead)),
            "mean_rmse": float(np.mean(rmse_per_lead)),
            "mean_rel_l2": float(np.mean(rel_l2_per_lead)),
        }

    return {
        "num_leads": H,
        "fluid_points": num_fluid_pts,
        "channels": channel_metrics,
    }


def evaluate_c04_dataset_baselines(
    window_index: C04WindowIndex,
    method: str = "persistence",
) -> dict[str, Any]:
    """Evaluate baseline forecasting performance over all windows in a C04WindowIndex.

    Returns pooled metrics across all windows.
    """
    _input(len(window_index) > 0, "window_index contains no windows to evaluate")
    _input(method in C04_METHODS, f"Unknown method: {method}")

    H = window_index.spec.forecast_frames
    total_windows = len(window_index)

    # Accumulators per channel: sae, sse, target_sq_sum, count
    accum = {
        ch: {
            "sae": np.zeros(H, dtype=np.float64),
            "sse": np.zeros(H, dtype=np.float64),
            "tgt_sq": np.zeros(H, dtype=np.float64),
            "count": 0,
        }
        for ch in C04_CHANNELS
    }

    evaluated_window_ids = []

    for window in window_index:
        w_id = window["window_id"]
        evaluated_window_ids.append(w_id)

        history = window["history"]
        future = window["future"]
        hist_times = window["history_times"]
        fut_times = window["future_times"]
        solid_mask = window["solid_mask"]

        pred = c04_forecast_from_history(history, hist_times, fut_times, method=method)
        scores = score_c04_forecast(pred, future, solid_mask=solid_mask)

        for ch in C04_CHANNELS:
            ch_data = scores["channels"][ch]
            pts = ch_data["fluid_points"]
            mae_lead = np.array(ch_data["mae_per_lead"], dtype=np.float64)
            rmse_lead = np.array(ch_data["rmse_per_lead"], dtype=np.float64)
            rel_l2_lead = np.array(ch_data["rel_l2_per_lead"], dtype=np.float64)

            accum[ch]["sae"] += mae_lead * pts
            accum[ch]["sse"] += (rmse_lead**2) * pts
            accum[ch]["count"] += pts

    results: dict[str, Any] = {
        "method": method,
        "total_windows": total_windows,
        "history_frames": window_index.spec.history_frames,
        "forecast_frames": window_index.spec.forecast_frames,
        "channels": {},
    }

    for ch in C04_CHANNELS:
        c_pts = accum[ch]["count"]
        mean_mae_lead = accum[ch]["sae"] / c_pts
        mean_rmse_lead = np.sqrt(accum[ch]["sse"] / c_pts)
        c_idx = C04_CHANNELS.index(ch)

        results["channels"][ch] = {
            "unit": C04_UNITS[c_idx],
            "mae_per_lead": mean_mae_lead.tolist(),
            "rmse_per_lead": mean_rmse_lead.tolist(),
            "overall_mae": float(np.mean(mean_mae_lead)),
            "overall_rmse": float(np.mean(mean_rmse_lead)),
        }

    return results
