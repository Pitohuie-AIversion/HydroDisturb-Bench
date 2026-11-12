"""Analytical common-window and pairwise comparisons, without COMSOL."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.errors import ModelValidationError
from hydro_disturb_bench.pulse_variant_comparison import compare_pulse_variants

ROOT = Path(__file__).resolve().parents[1]
X = np.array([0., .10, .12, .15, .18, .40])
Y = np.array([0., .09, .12, .15, .24])
TIMES = np.linspace(180., 200., 101)


def _fields(times):
    xx, yy = np.meshgrid(X, Y)
    values = np.zeros((len(times), 3, len(Y), len(X)), dtype=np.float64)
    values[:, 0] = .01
    values[:, 1] = .002
    values[:, 2] = xx + 2 * yy
    cylinder = np.hypot(xx - .1, yy - .12) <= .005
    mask = np.broadcast_to(cylinder, (len(times), len(Y), len(X))).copy()
    values = np.where(mask[:, None], np.nan, values)
    return {"values": values, "mask": mask, "cylinder_mask": cylinder,
            "invalid_mask": mask.copy(), "x": X.copy(), "y": Y.copy(), "times": times.copy(),
            "metadata": {"tensor_layout": "(T, 3, Ny, Nx)", "shape": list(values.shape),
                         "channel_order": ["u", "v", "p"], "units": ["m/s", "m/s", "Pa"],
                         "actual_times": times.tolist(), "requested_times": times.tolist(),
                         "grid_shape": [len(X), len(Y)]}}


@pytest.fixture
def study():
    plan = json.loads((ROOT / "docs/evidence/m3_branch_pulse_20261105/preregistered_plan.json")
                      .read_text(encoding="utf-8"))
    plan["grid_shape_nx_ny"] = [len(X), len(Y)]
    plan["response_acceptance"].update(roi_m=[.12, .18, .09, .15],
                                       response_window_s=[181., 188.],
                                       response_window_left_open=True, pre_event_end_s=181.)
    baseline, zero = _fields(np.linspace(170., 200., 151)), _fields(TIMES)
    U = plan["scales"]["U_inf_m_s"]
    zero["values"][:, 0] += .001 * U
    variants = {name: deepcopy(zero) for name in ("default", "reverse", "short", "long")}
    active = (TIMES > 181.) & (TIMES <= 188.)
    for name, du, dv in (("default", 0., .02), ("reverse", .01, -.03),
                         ("short", 0., .01), ("long", 0., .04)):
        variants[name]["values"][active, 0] += du * U
        variants[name]["values"][active, 1] += dv * U
    return baseline, zero, variants, plan


def test_uniform_vectors_have_independent_analytic_pairwise_norms(study):
    result = compare_pulse_variants(*study)
    active = (TIMES > 181.) & (TIMES <= 188.)
    time_factor = np.sqrt(np.trapezoid(active.astype(float), TIMES) / 20.)
    assert result["status"] == "PASS"
    assert result["cases"]["default"]["common_window_peak_roi_over_U"] == pytest.approx(.02)
    assert result["cases"]["default"]["peak_to_noise_ratio"] == pytest.approx(20.)
    assert result["cases"]["default"]["common_window_peak_time_s"] == pytest.approx(181.2)
    assert result["cases"]["reverse"]["common_window_peak_roi_over_U"] == pytest.approx(np.hypot(.01, .03))
    for name, expected in (("reverse", np.hypot(.01, .05)), ("short", .01), ("long", .02)):
        pair = result["against_default"][name]
        assert pair["common_window_peak_roi_over_U"] == pytest.approx(expected)
        assert pair["full_time_velocity_rms_over_U"] == pytest.approx(expected * time_factor)
        assert pair["full_time_centered_pressure_rms_over_rhoU2"] == pytest.approx(0., abs=1e-12)
    assert json.loads(json.dumps(result, allow_nan=False))["status"] == "PASS"


def test_common_window_endpoint_and_outside_peak_are_separate(study):
    U = study[3]["scales"]["U_inf_m_s"]
    fields = study[2]["long"]
    fields["values"][TIMES == 188., 1] += .01 * U
    fields["values"][TIMES > 188., 1] += .5 * U
    result = compare_pulse_variants(*study)["cases"]["long"]
    assert result["common_window_peak_roi_over_U"] == pytest.approx(.05)
    assert result["common_window_peak_time_s"] == 188.
    assert result["full_time_max_velocity_rms_over_U"] == pytest.approx(.5)


def test_nonuniform_weights_exclude_solid_points(study):
    baseline, zero, variants, plan = study
    U = plan["scales"]["U_inf_m_s"]
    fields = variants["short"]
    fields["values"][:] = variants["default"]["values"]
    iy, ix = 2, 3
    fields["values"][-1, 0, iy, ix] += .03 * U
    dx, dy = np.diff(X), np.diff(Y)
    wx = np.r_[dx[0] / 2, (dx[:-1] + dx[1:]) / 2, dx[-1] / 2]
    wy = np.r_[dy[0] / 2, (dy[:-1] + dy[1:]) / 2, dy[-1] / 2]
    weights = wy[:, None] * wx[None]
    weights[zero["cylinder_mask"]] = 0
    expected = .03 * np.sqrt(weights[iy, ix] / weights.sum())
    pair = compare_pulse_variants(*study)["against_default"]["short"]
    assert pair["velocity_per_frame_over_U"][-1] == pytest.approx(expected)
    assert pair["full_time_velocity_rms_over_U"] == pytest.approx(expected * np.sqrt(.1 / 20))
    assert pair["common_window_peak_roi_over_U"] == pytest.approx(0.)


def test_pressure_gauge_and_pattern_are_distinguished(study):
    fields = study[2]["reverse"]
    fields["values"][:, 2] += 2.5
    pair = compare_pulse_variants(*study)["against_default"]["reverse"]
    assert pair["full_time_centered_pressure_rms_over_rhoU2"] < 1e-12
    np.testing.assert_allclose(pair["pressure_mean_offset_Pa"], 2.5, atol=1e-12)
    fields["values"][:, 2] += fields["x"][None, None, :]
    pair = compare_pulse_variants(*study)["against_default"]["reverse"]
    assert pair["full_time_centered_pressure_rms_over_rhoU2"] > 0.


def test_zero_noise_uses_null_ratio_and_does_not_mutate_plan(study):
    baseline, zero, variants, plan = study
    original_plan = deepcopy(plan)
    baseline["values"][:, 0] += .001 * plan["scales"]["U_inf_m_s"]
    result = compare_pulse_variants(*study)
    assert result["cases"]["default"]["noise_floor_over_U"] == 0.
    assert result["cases"]["default"]["peak_to_noise_ratio"] is None
    assert result["cases"]["default"]["peak_to_noise_ratio_defined"] is False
    assert plan == original_plan
    json.dumps(result, allow_nan=False)


def test_failed_field_gate_is_not_hidden_by_successful_other_cases(study):
    study[2]["short"]["values"][:] = study[1]["values"]
    result = compare_pulse_variants(*study)
    assert result["status"] == "FAIL"
    assert result["cases"]["short"]["above_noise_floor"] is False
    assert result["cases"]["default"]["common_window_field_status"] == "PASS"


@pytest.mark.parametrize("fault", ["coordinates", "times", "mask", "unit", "fluid_nan"])
def test_field_contract_drift_is_rejected(study, fault):
    fields = study[2]["long"]
    if fault == "coordinates":
        fields["x"][1] += .001
    elif fault == "times":
        fields["times"][30] += .01
        fields["metadata"]["actual_times"] = fields["times"].tolist()
        fields["metadata"]["requested_times"] = fields["times"].tolist()
    elif fault == "mask":
        fields["mask"][:, 0, 0] = True
        fields["invalid_mask"][:, 0, 0] = True
        fields["values"][:, :, 0, 0] = np.nan
    elif fault == "unit":
        fields["metadata"]["units"][2] = "kPa"
    else:
        fields["values"][20, 0, 0, 0] = np.nan
    with pytest.raises(ModelValidationError):
        compare_pulse_variants(*study)


@pytest.mark.parametrize("fault", ["roi", "window", "left_open", "pre_event", "schedule", "cases"])
def test_mismatched_comparison_setup_is_rejected(study, fault):
    plan, variants = study[3], study[2]
    rules = plan["response_acceptance"]
    if fault == "roi":
        rules["roi_m"][1] = .25
    elif fault == "window":
        rules["response_window_s"][1] = 186.
    elif fault == "left_open":
        rules["response_window_left_open"] = False
    elif fault == "pre_event":
        rules["pre_event_end_s"] = 182.
    elif fault == "schedule":
        plan["output_step_s"] = .1
    else:
        variants.pop("short")
    with pytest.raises(ModelValidationError):
        compare_pulse_variants(*study)
