"""Independent analytic checks for direct forecasts and physical-unit metrics."""
from __future__ import annotations

import inspect
import math

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.errors import InvalidParameterError, ModelValidationError
from hydro_disturb_bench.forecast_baselines import (
    ForecastMetricAccumulator,
    forecast_from_history,
    score_forecast,
)


def _signal(times):
    times = np.asarray(times, dtype=float)
    intercept = np.arange(18, dtype=float).reshape(3, 2, 3) / 7
    slope = np.array([.2, -.5, 7.])[:, None, None] * np.ones((3, 2, 3))
    values = intercept[None] + times[:, None, None, None] * slope[None]
    mask = np.zeros((len(times), 2, 3), dtype=bool)
    mask[:, 1, 2] = True
    values[:, :, 1, 2] = np.nan
    return values, mask


@pytest.mark.parametrize("method", ["persistence", "last_two_linear"])
def test_constant_history_is_exact_and_mask_and_inputs_are_preserved(method):
    history, mask = _signal([0., 0., 0.])
    before = history.copy()
    prediction = forecast_from_history(history, mask, [1., 2., 4.], [4.2, 5.], method=method)
    np.testing.assert_array_equal(prediction, np.repeat(history[-1:], 2, axis=0))
    np.testing.assert_array_equal(history, before)
    assert prediction.dtype == np.float64 and not np.shares_memory(prediction, history)
    assert np.isnan(prediction[:, :, 1, 2]).all()


def test_linear_forecast_uses_actual_uneven_time_intervals_and_physical_channel_units():
    history_times, target_times = np.array([1., 1.7, 3.]), np.array([3.2, 3.9, 5.])
    history, mask = _signal(history_times)
    target, future_mask = _signal(target_times)
    prediction = forecast_from_history(history, mask, history_times, target_times, method="last_two_linear")
    np.testing.assert_allclose(prediction, target, rtol=1e-14, atol=1e-14)
    persisted = forecast_from_history(history, mask, history_times, target_times, method="persistence")
    metrics = score_forecast(persisted, target, future_mask)
    expected = (target_times - history_times[-1])[:, None] * np.array([.2, .5, 7.])[None]
    np.testing.assert_allclose(metrics["mae"], expected, rtol=1e-14, atol=1e-14)
    np.testing.assert_allclose(metrics["rmse"], expected, rtol=1e-14, atol=1e-14)
    assert metrics["count"] == [[5, 5, 5]] * 3


def test_last_two_linear_ignores_earlier_values_and_accepts_no_future_or_labels():
    history, mask = _signal([1., 2., 3., 4.])
    kwargs = {"history_mask": mask, "history_times": [1., 2., 3., 4.],
              "prediction_times": [4.2, 5.], "method": "last_two_linear"}
    before = forecast_from_history(history, **kwargs)
    history[:2, :, 0, :] += 12345.
    np.testing.assert_array_equal(forecast_from_history(history, **kwargs), before)
    assert set(inspect.signature(forecast_from_history).parameters) == {
        "history", "history_mask", "history_times", "prediction_times", "method"}
    for hidden in ("future", "future_mask", "amplitude", "source_model", "trajectory_id"):
        with pytest.raises(TypeError):
            forecast_from_history(history, **kwargs, **{hidden: "withheld"})


def test_one_observation_supports_persistence_but_not_linear_and_unknown_method_rejects():
    history, mask = _signal([3.])
    result = forecast_from_history(history, mask, [3.], [4.], method="persistence")
    np.testing.assert_array_equal(result, history)
    for method in ("last_two_linear", "trained_model"):
        with pytest.raises(InvalidParameterError):
            forecast_from_history(history, mask, [3.], [4.], method=method)


@pytest.mark.parametrize("method", [None, True, ["persistence"], np.array(["persistence"]),
                                    np.array(["persistence", "last_two_linear"])])
def test_method_must_be_one_explicit_string(method):
    history, mask = _signal([1., 2.])
    with pytest.raises(InvalidParameterError):
        forecast_from_history(history, mask, [1., 2.], [3.], method=method)


@pytest.mark.parametrize("history_times,prediction_times", [
    ([1., 1.], [2.]), ([2., 1.], [3.]), ([1., np.nan], [3.]),
    ([1.], [3.]), ([1., 2.], []), ([1., 2.], [2.]), ([1., 2.], [1.9]),
    ([1., 2.], [3., 3.]), ([1., 2.], [4., 3.]), ([1., 2.], [np.inf]),
    ([1., 2.], [[3.]]), ([False, True], [3.]),
    ([-1e308, 1e308], [1.5e308]), ([-1e308, -9e307], [1e308]),
])
def test_invalid_or_overflowing_time_axes_are_rejected(history_times, prediction_times):
    history, mask = _signal([1., 2.])
    with pytest.raises(ModelValidationError):
        forecast_from_history(history, mask, history_times, prediction_times, method="persistence")


@pytest.mark.parametrize("fault", ["mask_dtype", "changing_mask", "mask_shape", "no_fluid",
                                   "fluid_nan", "fluid_inf", "masked_finite", "masked_inf",
                                   "wrong_channels", "complex_values", "boolean_values"])
def test_history_contract_rejects_invalid_fields_instead_of_filling_or_silently_dropping(fault):
    history, mask = _signal([1., 2.])
    if fault == "mask_dtype":
        mask = mask.astype(int)
    elif fault == "changing_mask":
        mask[0, 0, 0] = True
        history[0, :, 0, 0] = np.nan
    elif fault == "mask_shape":
        mask = mask[0]
    elif fault == "no_fluid":
        mask[:] = True
        history[:] = np.nan
    elif fault in ("fluid_nan", "fluid_inf"):
        history[0, 1, 0, 0] = np.nan if fault == "fluid_nan" else np.inf
    elif fault in ("masked_finite", "masked_inf"):
        history[0, 1, 1, 2] = 0. if fault == "masked_finite" else np.inf
    elif fault == "wrong_channels":
        history = history[:, :2]
    elif fault == "complex_values":
        history = history.astype(complex)
    else:
        history = np.zeros(history.shape, dtype=bool)
    with pytest.raises(ModelValidationError):
        forecast_from_history(history, mask, [1., 2.], [3.], method="last_two_linear")


def test_forecast_rejects_nonfinite_extrapolation_even_when_history_is_finite():
    history, mask = _signal([1., 2.])
    history[0, :, 0, 0] = -np.finfo(float).max
    history[1, :, 0, 0] = np.finfo(float).max
    with pytest.raises(ModelValidationError, match="prediction.*finite"):
        forecast_from_history(history, mask, [1., 2.], [3.], method="last_two_linear")


def test_hand_computed_channelwise_metrics_keep_pressure_separate_and_masks_out_of_denominator():
    target = np.zeros((2, 3, 2, 2), dtype=float)
    mask = np.zeros((2, 2, 2), dtype=bool)
    mask[:, 1, 1] = True
    target[:, :, 1, 1] = np.nan
    prediction = target.copy()
    errors = np.array([[1., -2., 3.], [0., 4., -4.], [-10., 20., 0.]])
    prediction[0, :, ~mask[0]] = errors.T
    prediction[1, :, ~mask[1]] = (2 * errors).T
    actual = score_forecast(prediction, target, mask)
    expected_sae = np.array([[6., 8., 30.], [12., 16., 60.]])
    expected_sse = np.array([[14., 32., 500.], [56., 128., 2000.]])
    np.testing.assert_array_equal(actual["sae"], expected_sae)
    np.testing.assert_array_equal(actual["sse"], expected_sse)
    np.testing.assert_allclose(actual["mae"], expected_sae / 3)
    np.testing.assert_allclose(actual["rmse"], np.sqrt(expected_sse / 3))
    assert actual["count"] == [[3, 3, 3], [3, 3, 3]]


def test_constant_pressure_offset_is_scored_in_pa_without_recentering():
    target, mask = _signal([1., 2.])
    prediction = target.copy()
    prediction[:, 2] += 10.
    result = score_forecast(prediction, target, mask)
    np.testing.assert_array_equal(result["mae"], [[0., 0., 10.], [0., 0., 10.]])
    np.testing.assert_array_equal(result["rmse"], [[0., 0., 10.], [0., 0., 10.]])


@pytest.mark.parametrize("fault", ["target_shape", "prediction_nan", "target_inf", "mask_shape",
                                   "masked_prediction", "masked_target", "difference_overflow", "square_overflow"])
def test_scoring_rejects_invalid_or_overflowing_errors(fault):
    target, mask = _signal([1., 2.])
    prediction = target.copy()
    if fault == "target_shape":
        target = target[:1]
    elif fault == "prediction_nan":
        prediction[0, 0, 0, 0] = np.nan
    elif fault == "target_inf":
        target[0, 0, 0, 0] = np.inf
    elif fault == "mask_shape":
        mask = mask[:, None]
    elif fault == "masked_prediction":
        prediction[:, :, 1, 2] = 0.
    elif fault == "masked_target":
        target[:, :, 1, 2] = 0.
    elif fault == "difference_overflow":
        prediction[0, 0, 0, 0], target[0, 0, 0, 0] = 1e308, -1e308
    else:
        prediction[0, 0, 0, 0] = 1e200
    with pytest.raises(ModelValidationError):
        score_forecast(prediction, target, mask)


def test_micro_aggregation_uses_error_sums_and_counts_not_mean_of_window_rmse():
    accumulator = ForecastMetricAccumulator()
    with pytest.raises(ModelValidationError, match="empty"):
        accumulator.result()
    for valid_count, error in ((1, 1.), (3, 3.)):
        mask = np.ones((2, 1, 3), dtype=bool)
        mask[:, :, :valid_count] = False
        target = np.zeros((2, 3, 1, 3), dtype=float)
        target[np.broadcast_to(mask[:, None], target.shape)] = np.nan
        prediction = target + np.array([error, 2 * error])[:, None, None, None]
        accumulator.add(prediction, target, mask)
    result = accumulator.result()
    np.testing.assert_array_equal(result["count"], [[4, 4, 4], [4, 4, 4]])
    np.testing.assert_array_equal(result["mae"], [[2.5] * 3, [5.] * 3])
    np.testing.assert_allclose(result["rmse"], [[math.sqrt(7)] * 3, [2 * math.sqrt(7)] * 3])
    result["count"][0][0] = -1
    assert accumulator.result()["count"][0][0] == 4


def test_accumulator_rejects_mismatched_leads_or_overflow_without_changing_prior_sums():
    accumulator = ForecastMetricAccumulator()
    target, mask = _signal([1., 2.])
    prediction = target + 1.
    accumulator.add(prediction, target, mask)
    before = accumulator.result()
    with pytest.raises(ModelValidationError, match="shapes differ"):
        accumulator.add(prediction[:1], target[:1], mask[:1])
    assert accumulator.result() == before
    large = ForecastMetricAccumulator()
    target = np.zeros((1, 3, 1, 1))
    prediction = np.full_like(target, 1e154)
    mask = np.zeros((1, 1, 1), dtype=bool)
    large.add(prediction, target, mask)
    before = large.result()
    with pytest.raises(ModelValidationError, match="sums overflowed"):
        large.add(prediction, target, mask)
    assert large.result() == before


def test_random_small_metrics_match_independent_scalar_fsum_reference():
    rng = np.random.default_rng(72343)
    target, mask = _signal([1., 2., 3.])
    prediction = target + rng.normal(size=target.shape)
    result = score_forecast(prediction, target, mask)
    for lead in range(3):
        for channel in range(3):
            errors = [float(prediction[lead, channel, iy, ix] - target[lead, channel, iy, ix])
                      for iy in range(2) for ix in range(3) if not mask[lead, iy, ix]]
            assert result["count"][lead][channel] == len(errors)
            assert result["sae"][lead][channel] == pytest.approx(math.fsum(abs(e) for e in errors), rel=1e-14)
            assert result["sse"][lead][channel] == pytest.approx(math.fsum(e * e for e in errors), rel=1e-14)
