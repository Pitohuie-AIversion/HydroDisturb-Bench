"""History-only direct forecasts and channelwise sampled-node error sums.

No fitting, normalization, pressure recentering or future-field input is used.
Errors retain each channel's physical unit; velocity and pressure are never
combined into a dimensionally mixed score. Spatial points have equal weight.
"""
from __future__ import annotations

import numpy as np

from .comsol_java.errors import InvalidParameterError, ModelValidationError

METHODS = ("persistence", "last_two_linear")


def _require(condition, message):
    if not condition:
        raise ModelValidationError(message)


def _numeric_array(value, label):
    try:
        raw = np.asarray(value)
        _require(raw.dtype.kind in "fiu", f"{label} must contain real numbers, not bool or objects")
        with np.errstate(over="ignore", invalid="ignore"):
            return np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ModelValidationError(f"Invalid {label} array") from exc


def _fields_and_mask(values, mask, label):
    values = _numeric_array(values, label)
    _require(values.ndim == 4 and values.shape[0] >= 1 and values.shape[1] == 3
             and values.shape[2] >= 1 and values.shape[3] >= 1,
             f"{label} must have nonempty shape (T, 3, Ny, Nx)")
    try:
        mask = np.asarray(mask)
    except (TypeError, ValueError) as exc:
        raise ModelValidationError(f"Invalid {label} mask") from exc
    _require(mask.dtype.kind == "b" and mask.shape == (values.shape[0], *values.shape[2:]),
             f"{label} mask must be bool with shape (T, Ny, Nx)")
    _require(np.all(mask == mask[:1]), f"{label} requires a fixed spatial mask")
    _require(np.any(~mask[0]), f"{label} has no valid fluid points")
    expanded = np.broadcast_to(mask[:, None], values.shape)
    _require(np.isnan(values[expanded]).all(), f"{label} masked values must remain NaN")
    _require(np.isfinite(values[~expanded]).all(), f"{label} fluid values must be finite")
    return values, mask


def _times(value, count, label):
    result = _numeric_array(value, label)
    _require(result.ndim == 1 and result.size == count and count >= 1
             and np.isfinite(result).all(), f"{label} must be a finite one-dimensional time axis")
    with np.errstate(over="ignore", invalid="ignore"):
        increments = np.diff(result)
    _require(np.isfinite(increments).all() and np.all(increments > 0),
             f"{label} must be strictly increasing with finite intervals")
    return result


def forecast_from_history(history, history_mask, history_times, prediction_times, *, method):
    """Predict saved times directly from observed states and a known time schedule.

    ``persistence`` repeats the final observed field. ``last_two_linear`` uses
    the final two observed fields divided by their actual elapsed seconds.
    All targets must follow the last observation. The function cannot receive
    target fields, forcing labels, trajectory identities or source filenames.
    """
    if not isinstance(method, str) or method not in METHODS:
        raise InvalidParameterError(f"Unknown forecast method: {method!r}")
    history, mask = _fields_and_mask(history, history_mask, "history")
    times = _times(history_times, history.shape[0], "history_times")
    requested = _numeric_array(prediction_times, "prediction_times")
    _require(requested.ndim == 1, "prediction_times must be a one-dimensional time axis")
    requested = _times(requested, requested.size, "prediction_times")
    with np.errstate(over="ignore", invalid="ignore"):
        leads = requested - times[-1]
    _require(np.isfinite(leads).all() and np.all(leads > 0),
             "Every prediction time must be strictly after the history with a finite lead")
    if method == "last_two_linear" and history.shape[0] < 2:
        raise InvalidParameterError("last_two_linear requires at least two observed frames")

    result = np.broadcast_to(history[-1], (len(leads), *history.shape[1:])).copy()
    if method == "last_two_linear":
        slope = np.zeros(history.shape[1:], dtype=np.float64)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            np.subtract(history[-1], history[-2], out=slope, where=~mask[-1][None])
            slope /= times[-1] - times[-2]
            result += leads[:, None, None, None] * slope[None]
    prediction_mask = np.broadcast_to(mask[-1], (len(leads), *mask.shape[1:]))
    _fields_and_mask(result, prediction_mask, "prediction")
    return result


def _error_sums(prediction, target, mask):
    prediction, mask = _fields_and_mask(prediction, mask, "prediction")
    target, _ = _fields_and_mask(target, mask, "target")
    _require(prediction.shape == target.shape, "Prediction and target shapes differ")
    errors = np.zeros_like(prediction)
    with np.errstate(over="ignore", invalid="ignore"):
        np.subtract(prediction, target, out=errors, where=~mask[:, None])
        sae = np.sum(np.abs(errors), axis=(2, 3), dtype=np.float64)
        sse = np.sum(np.square(errors), axis=(2, 3), dtype=np.float64)
    _require(np.isfinite(sae).all() and np.isfinite(sse).all(), "Forecast error arithmetic overflowed")
    count = np.repeat(np.count_nonzero(~mask, axis=(1, 2))[:, None], 3, axis=1)
    return sae, sse, count


def _metrics(sae, sse, count):
    return {"mae": (sae / count).tolist(), "rmse": np.sqrt(sse / count).tolist(),
            "sae": sae.tolist(), "sse": sse.tolist(), "count": count.tolist()}


def score_forecast(prediction, target, mask):
    """Return MAE/RMSE and SAE/SSE/count as JSON lists shaped (lead, channel).

    Every unmasked sampled node counts once. Masked NaNs are excluded explicitly;
    unmasked non-finite values reject evaluation rather than disappearing from
    the denominator. This is not a quadrature estimate of a domain integral.
    """
    return _metrics(*_error_sums(prediction, target, mask))


class ForecastMetricAccumulator:
    """Pool error sums over forecast assignments without retaining field arrays.

    The caller must group matching lead times and channels, and account for
    repeated windows or target frames. This class counts every supplied forecast
    assignment; it neither deduplicates targets nor asserts sample independence.
    """

    def __init__(self):
        self._sae = self._sse = self._count = None

    def add(self, prediction, target, mask):
        sae, sse, count = _error_sums(prediction, target, mask)
        if self._sae is not None:
            _require(sae.shape == self._sae.shape, "Accumulated forecast lead/channel shapes differ")
            _require(np.all(count <= np.iinfo(np.int64).max - self._count),
                     "Forecast metric count overflowed")
            with np.errstate(over="ignore", invalid="ignore"):
                sae, sse = self._sae + sae, self._sse + sse
            count = self._count + count
            _require(np.isfinite(sae).all() and np.isfinite(sse).all(), "Forecast metric sums overflowed")
        self._sae, self._sse, self._count = sae, sse, count

    def result(self):
        """Return fresh JSON-ready lists; empty collections have no defined score."""
        _require(self._sae is not None, "Cannot score an empty forecast collection")
        return _metrics(self._sae, self._sse, self._count)
