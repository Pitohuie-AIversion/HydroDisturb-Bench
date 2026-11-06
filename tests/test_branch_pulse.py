"""Independent analytic tests for the preregistered restart/response analysis.

These tests never invoke COMSOL. They preserve all preregistered scales and
thresholds while using a small nonuniform grid to expose quadrature mistakes.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java import bridge

PLAN_PATH = Path(__file__).resolve().parents[1] / "docs/evidence/m3_branch_pulse_20261105/preregistered_plan.json"
X = np.asarray([0.0, .07, .10, .12, .15, .20, .25, .40])
Y = np.asarray([0.0, .08, .10, .12, .16, .20, .24])
BASE_TIMES = np.linspace(170, 200, 151)
BRANCH_TIMES = np.linspace(180, 200, 101)


def _weights(axis):
    """Explicit trapezoid node weights, independent of production helpers."""
    intervals = np.diff(axis)
    return np.r_[intervals[0] / 2, (intervals[:-1] + intervals[1:]) / 2,
                 intervals[-1] / 2]


def _area_weights(data, roi=False):
    weights = _weights(data["y"])[:, None] * _weights(data["x"])[None, :]
    weights[data["cylinder_mask"]] = 0
    if roi:
        xx, yy = np.meshgrid(data["x"], data["y"])
        weights *= (xx >= .12) & (xx <= .25) & (yy >= .08) & (yy <= .16)
    return weights


def _data(times):
    xx, yy = np.meshgrid(X, Y)
    t = np.asarray(times)[:, None, None]
    values = np.stack([
        .004 + .00002 * (t - 180) + .02 * xx + .01 * yy,
        -.002 + .00003 * (t - 180) - .01 * xx + .015 * yy,
        .03 + .00004 * (t - 180) + .1 * xx - .2 * yy,
    ], axis=1)
    cylinder = np.hypot(xx - .10, yy - .12) <= .005 + 1e-12
    mask = np.broadcast_to(cylinder, (len(times), len(Y), len(X))).copy()
    values = np.where(mask[:, None], np.nan, values)
    return {
        "values": values, "mask": mask, "cylinder_mask": cylinder,
        "invalid_mask": mask.copy(), "x": X.copy(), "y": Y.copy(),
        "times": np.asarray(times).copy(),
        "metadata": {"tensor_layout": "(T, 3, Ny, Nx)", "shape": list(values.shape),
                     "channel_order": ["u", "v", "p"], "units": ["m/s", "m/s", "Pa"],
                     "actual_times": list(times), "requested_times": list(times),
                     "grid_shape": [len(X), len(Y)]},
    }


def _add_velocity(data, du=0.0, dv=0.0, frames=None):
    selected = np.arange(len(data["times"])) if frames is None else np.asarray(frames)
    for ti in selected:
        valid = ~data["mask"][ti]
        data["values"][ti, 0, valid] += du
        data["values"][ti, 1, valid] += dv


def _pressure_pattern(data):
    xx, yy = np.meshgrid(data["x"], data["y"])
    weights = _area_weights(data)
    pattern = xx + 2 * yy
    pattern -= np.sum(weights * pattern) / np.sum(weights)
    pattern /= np.sqrt(np.sum(weights * pattern**2) / np.sum(weights))
    return pattern


@pytest.fixture
def experiment():
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    # Only the grid size is reduced; the production plan and tolerances stay intact.
    plan["grid_shape_nx_ny"] = [len(X), len(Y)]
    baseline, zero = _data(BASE_TIMES), _data(BRANCH_TIMES)
    pulse = deepcopy(zero)
    active = np.flatnonzero((pulse["times"] >= 182) & (pulse["times"] <= 183))
    _add_velocity(pulse, dv=.02 * plan["scales"]["U_inf_m_s"], frames=active)
    return baseline, zero, pulse, plan


def _compare(experiment):
    from hydro_disturb_bench.branch_pulse import compare_branch_fields

    return compare_branch_fields(*experiment)


def test_analysis_matches_101_branch_times_from_151_frame_baseline(experiment):
    baseline, zero, pulse, plan = experiment
    # Earlier baseline history is not part of the restart comparison.
    _add_velocity(baseline, du=1, dv=-2, frames=np.flatnonzero(baseline["times"] < 180))
    result = _compare(experiment)
    assert result["status"] == "PASS"
    np.testing.assert_array_equal(result["times"], BRANCH_TIMES)
    assert result["restart"]["passed"] is True
    assert result["response"]["passed"] is True
    assert result["response"]["pre_event_passed"] is True
    assert result["response"]["peak_roi_over_U"] == pytest.approx(.02, abs=1e-12)
    assert result["response"]["required_peak_over_U"] == pytest.approx(.005)
    assert result["restart"]["velocity"]["max"] < 1e-12
    assert np.isfinite(result["restart"]["pressure_centered"]["per_frame"]).all()


def test_velocity_norm_uses_u_and_v_jointly_without_component_averaging(experiment):
    baseline, zero, pulse, plan = experiment
    U = plan["scales"]["U_inf_m_s"]
    for data in (zero, pulse):
        _add_velocity(data, du=.0012 * U, dv=.0016 * U)
    result = _compare(experiment)
    np.testing.assert_allclose(result["restart"]["velocity"]["per_frame"], .002, rtol=0, atol=1e-12)
    assert result["restart"]["velocity"]["time_rms"] == pytest.approx(.002, abs=1e-12)
    assert result["response"]["noise_floor_over_U"] == pytest.approx(.002, abs=1e-12)
    assert result["response"]["required_peak_over_U"] == pytest.approx(.01, abs=1e-12)
    # The actual response is P-Z, so the shared restart offset cancels.
    assert result["response"]["peak_roi_over_U"] == pytest.approx(.02, abs=1e-12)
    assert result["status"] == "PASS"


def test_nonuniform_area_weights_exclude_mask_and_use_global_weights_for_roi(experiment):
    baseline, zero, pulse, plan = experiment
    U = plan["scales"]["U_inf_m_s"]
    # One node has known, nonuniform full-domain and ROI weights.
    iy, ix = 1, 3  # ROI corner y=.08, x=.12, included by the frozen definition.
    increment = .01 * U
    zero["values"][:, 0, iy, ix] += increment
    pulse["values"][:, 0, iy, ix] += increment
    global_weights = _area_weights(zero)
    roi_weights = _area_weights(zero, roi=True)
    expected_global = .01 * np.sqrt(global_weights[iy, ix] / global_weights.sum())
    expected_roi = .01 * np.sqrt(roi_weights[iy, ix] / roi_weights.sum())
    result = _compare(experiment)
    np.testing.assert_allclose(result["restart"]["velocity"]["per_frame"], expected_global, rtol=0, atol=1e-12)
    np.testing.assert_allclose(result["restart"]["roi_velocity_error_per_frame"], expected_roi, rtol=0, atol=1e-12)
    assert zero["mask"].any() and np.isnan(zero["values"][:, :, zero["cylinder_mask"]]).all()
    assert np.isfinite(result["restart"]["velocity"]["per_frame"]).all()


def test_pressure_gauge_offset_is_reported_but_not_counted_as_centered_error(experiment):
    for data in experiment[1:3]:
        data["values"][:, 2] += 7.25
    result = _compare(experiment)
    np.testing.assert_allclose(result["restart"]["pressure_mean_offset_Pa"], 7.25, rtol=0, atol=1e-12)
    assert result["restart"]["pressure_centered"]["max"] < 1e-10
    assert result["status"] == "PASS"


def test_pressure_norm_uses_rho_u_squared_and_weighted_spatial_centering(experiment):
    baseline, zero, pulse, plan = experiment
    q = plan["scales"]["rho_f_kg_m3"] * plan["scales"]["U_inf_m_s"] ** 2
    pattern = _pressure_pattern(zero)
    for data in (zero, pulse):
        data["values"][:, 2] += q * .004 * pattern + .123
    result = _compare(experiment)
    np.testing.assert_allclose(result["restart"]["pressure_centered"]["per_frame"], .004, rtol=0, atol=1e-12)
    np.testing.assert_allclose(result["restart"]["pressure_mean_offset_Pa"], .123, rtol=0, atol=1e-12)
    assert result["status"] == "PASS"


def test_time_rms_uses_half_weight_at_temporal_endpoints(experiment):
    baseline, zero, pulse, plan = experiment
    U = plan["scales"]["U_inf_m_s"]
    q = plan["scales"]["rho_f_kg_m3"] * U**2
    pattern = _pressure_pattern(zero)
    for data in (zero, pulse):
        _add_velocity(data, du=.009 * U, frames=[0])
        data["values"][-1, 2] += .02 * q * pattern
    result = _compare(experiment)
    # dt/2 divided by total duration = .1/20, not 1/101.
    assert result["restart"]["velocity"]["time_rms"] == pytest.approx(.009 * np.sqrt(.1 / 20), abs=1e-12)
    assert result["restart"]["pressure_centered"]["time_rms"] == pytest.approx(.02 * np.sqrt(.1 / 20), abs=1e-12)
    assert result["restart"]["velocity"]["max"] == pytest.approx(.009, abs=1e-12)
    assert result["restart"]["pressure_centered"]["max"] == pytest.approx(.02, abs=1e-12)
    assert result["restart"]["passed"] is True


@pytest.mark.parametrize("metric,peak,only_endpoint", [
    ("velocity", .0051, False), ("velocity", .0101, True),
    ("pressure", .0101, False), ("pressure", .0301, True),
])
def test_restart_gate_failure_is_returned_without_confusing_it_with_response(experiment, metric, peak, only_endpoint):
    baseline, zero, pulse, plan = experiment
    U = plan["scales"]["U_inf_m_s"]
    frames = [100] if only_endpoint else np.arange(101)
    for data in (zero, pulse):
        if metric == "velocity":
            _add_velocity(data, du=peak * U, frames=frames)
        else:
            q = plan["scales"]["rho_f_kg_m3"] * U**2
            data["values"][frames, 2] += peak * q * _pressure_pattern(data)
    result = _compare(experiment)
    assert result["status"] == "FAIL"
    assert result["restart"]["passed"] is False
    assert result["response"]["peak_roi_over_U"] == pytest.approx(.02, abs=1e-12)


def test_pre_event_leak_fails_even_with_detectable_later_response(experiment):
    _add_velocity(experiment[2], dv=.0002 * experiment[3]["scales"]["U_inf_m_s"], frames=[4])
    result = _compare(experiment)
    assert result["status"] == "FAIL"
    assert result["restart"]["passed"] is True
    assert result["response"]["pre_event_passed"] is False
    assert result["response"]["pre_event_peak_over_U"] == pytest.approx(.0002, abs=1e-12)
    assert result["response"]["pre_event_bound_over_U"] == pytest.approx(.0001, abs=1e-12)


def test_pre_event_bound_uses_pre_event_noise_only(experiment):
    baseline, zero, pulse, plan = experiment
    U = plan["scales"]["U_inf_m_s"]
    # A later restart discrepancy must not authorize an earlier spurious signal.
    for data in (zero, pulse):
        _add_velocity(data, du=.002 * U, frames=[75])
    _add_velocity(pulse, dv=.0002 * U, frames=[4])
    result = _compare(experiment)
    assert result["response"]["noise_floor_over_U"] == pytest.approx(.002, abs=1e-12)
    assert result["response"]["pre_event_bound_over_U"] == pytest.approx(.0001, abs=1e-12)
    assert result["response"]["pre_event_passed"] is False


def test_response_below_five_times_restart_noise_is_not_declared_physical(experiment):
    baseline, zero, pulse, plan = experiment
    for data in (zero, pulse):
        _add_velocity(data, du=.0045 * plan["scales"]["U_inf_m_s"])
    result = _compare(experiment)
    assert result["restart"]["passed"] is True
    assert result["response"]["peak_roi_over_U"] == pytest.approx(.02, abs=1e-12)
    assert result["response"]["required_peak_over_U"] == pytest.approx(.0225, abs=1e-12)
    assert result["response"]["passed"] is False
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("where,increment", [(10, .0049), (5, .02), (31, .02)])
def test_response_floor_and_event_window_are_respected(experiment, where, increment):
    baseline, zero, pulse, plan = experiment
    pulse["values"][:] = zero["values"]
    _add_velocity(pulse, dv=increment * plan["scales"]["U_inf_m_s"], frames=[where])
    # t=181 belongs to pre-event; t=186.2 lies beyond the response window.
    result = _compare(experiment)
    assert result["status"] == "FAIL"
    assert result["response"]["passed"] is False
    expected_peak = increment if where == 10 else 0
    assert result["response"]["peak_roi_over_U"] == pytest.approx(expected_peak, abs=1e-12)


def test_response_window_includes_186_seconds(experiment):
    baseline, zero, pulse, plan = experiment
    pulse["values"][:] = zero["values"]
    _add_velocity(pulse, dv=.02 * plan["scales"]["U_inf_m_s"], frames=[30])
    assert _compare(experiment)["status"] == "PASS"


def test_change_outside_fixed_roi_does_not_satisfy_response_gate(experiment):
    baseline, zero, pulse, plan = experiment
    pulse["values"][:] = zero["values"]
    pulse["values"][10, 1, 0, 0] += .1 * plan["scales"]["U_inf_m_s"]
    result = _compare(experiment)
    assert result["response"]["peak_roi_over_U"] == pytest.approx(0, abs=1e-12)
    assert result["response"]["passed"] is False
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("fault", [
    "tensor_shape", "channel_order", "pressure_unit", "coordinate_drift", "mask_drift",
    "fluid_nan", "fluid_inf", "duplicate_time", "missing_branch_frame", "nearest_baseline_time",
])
def test_analysis_rejects_incompatible_field_contracts(experiment, fault):
    baseline, zero, pulse, plan = experiment
    if fault == "tensor_shape":
        pulse["values"] = pulse["values"].transpose(0, 1, 3, 2)
    elif fault == "channel_order":
        pulse["metadata"]["channel_order"] = ["v", "u", "p"]
    elif fault == "pressure_unit":
        zero["metadata"]["units"][2] = "kPa"
    elif fault == "coordinate_drift":
        pulse["x"][1] += .001
    elif fault == "mask_drift":
        pulse["mask"][:, 0, 0] = True
        pulse["invalid_mask"][:, 0, 0] = True
        pulse["values"][:, :, 0, 0] = np.nan
    elif fault == "fluid_nan":
        pulse["values"][5, 0, 0, 0] = np.nan
    elif fault == "fluid_inf":
        pulse["values"][5, 0, 0, 0] = np.inf
    elif fault == "duplicate_time":
        zero["times"][1] = zero["times"][0]
    elif fault == "missing_branch_frame":
        for key in ("values", "mask", "invalid_mask", "times"):
            pulse[key] = pulse[key][:-1]
        pulse["metadata"]["shape"] = list(pulse["values"].shape)
        pulse["metadata"]["actual_times"] = pulse["times"].tolist()
        pulse["metadata"]["requested_times"] = pulse["times"].tolist()
    elif fault == "nearest_baseline_time":
        baseline["times"][50] += .05
        baseline["metadata"]["actual_times"] = baseline["times"].tolist()
        baseline["metadata"]["requested_times"] = baseline["times"].tolist()
    with pytest.raises(bridge.ModelValidationError):
        _compare(experiment)


def test_analysis_rejects_shared_invalid_mask_that_hides_fluid_samples(experiment):
    for data in experiment[:3]:
        data["mask"][:, 0, 0] = True
        data["invalid_mask"][:, 0, 0] = True
        data["values"][:, :, 0, 0] = np.nan
    assert not experiment[0]["cylinder_mask"][0, 0]
    with pytest.raises(bridge.ModelValidationError):
        _compare(experiment)


def test_saved_time_roundoff_is_allowed_without_nearest_frame_selection(experiment):
    # A saved t=186 represented one ULP above 186 still belongs to that endpoint.
    experiment[2]["values"][:] = experiment[1]["values"]
    _add_velocity(experiment[2], dv=.02 * experiment[3]["scales"]["U_inf_m_s"], frames=[30])
    for data in experiment[1:3]:
        data["times"] = np.nextafter(data["times"], np.inf)
        data["metadata"]["actual_times"] = data["times"].tolist()
    result = _compare(experiment)
    assert result["status"] == "PASS"
    np.testing.assert_array_equal(result["times"], experiment[1]["times"])
