"""Synthetic native/source evidence; no COMSOL result is fabricated as real."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from test_branch_evidence import _feature, _vector
from test_branch_evidence import native_case as _native_case

from hydro_disturb_bench.comsol_java.errors import ModelValidationError
from hydro_disturb_bench.pulse_parameters import (
    properties_payload,
    pulse_direction,
    source_integral_reference,
)
from hydro_disturb_bench.pulse_validation import validate_pulse_readback

native_case = _native_case

FORCE = [
    "rho_f*local_A*local_dx*exp(-((x-local_xc)^2+(y-local_yc)^2)/(2*local_sigma^2))*if((t>local_on)&&(t<local_off),sin(pi*(t-local_on)/(local_off-local_on))^2,0)",
    "rho_f*local_A*local_dy*exp(-((x-local_xc)^2+(y-local_yc)^2)/(2*local_sigma^2))*if((t>local_on)&&(t<local_off),sin(pi*(t-local_on)/(local_off-local_on))^2,0)",
    "0[N/m^3]",
]


def pulse_config(**changes):
    config = {"schema_version": 1, "event_id": "synthetic_combined", "mechanism": "gaussian_body_acceleration",
              "pulse": {"center_m": [.17, .13], "direction_deg": 45., "acceleration_m_s2": .0075,
                        "sigma_m": .008, "on_s": 182., "off_s": 184.}}
    config["pulse"].update(changes)
    return config


def _parameters(config):
    p = config["pulse"]
    dx, dy = pulse_direction(config)
    specs = {"local_A": (p["acceleration_m_s2"], "m/s^2"), "local_xc": (p["center_m"][0], "m"),
             "local_yc": (p["center_m"][1], "m"), "local_sigma": (p["sigma_m"], "m"),
             "local_theta": (p["direction_deg"], "deg"), "local_dx": (dx, "1"), "local_dy": (dy, "1"),
             "local_on": (p["on_s"], "s"), "local_off": (p["off_s"], "s")}
    result = {}
    for name, (value, unit) in specs.items():
        result[name] = {"expression": str(float(value)) + ("" if unit == "1" else "[" + unit + "]"),
                        "value": math.radians(value) if name == "local_theta" else value,
                        "unit": "rad" if name == "local_theta" else (None if unit == "1" else unit),
                        "value_in_declared_unit": value, "declared_unit": unit}
    return result


def _node(prefix, expressions, values, indices, *, point, coordinates=None):
    result = {"tag": prefix + "_eval", "type": "EvalPoint" if point else "IntSurface",
              "dataset_tag": prefix + "_point" if point else "dset1", "solrepresentation": "solnum",
              "innerinput": "manual", "solnum": indices, "expressions": expressions,
              "units": ["N/m^3"] * (len(expressions) - 3) + ["s", "m", "m"] if point else ["N/m"] * len(expressions),
              "values_by_expression": np.asarray(values).tolist()}
    if point:
        result.update(point_dataset_tag=prefix + "_point", point_dataset_type="CutPoint2D",
                      point_source_dataset="dset1", requested_coordinates=coordinates)
    else:
        result["selection"] = [1]
    return result


def make_pulse_evidence(config, mode, native_case):
    """Make declared synthetic response for validator and runner fault tests."""
    prepare, zero, _, _ = native_case
    response = deepcopy(zero)
    p = config["pulse"]
    for key in ("amplitude_m_s2", "force_initial_evaluation", "force_evaluation", "initialization_path"):
        response.pop(key, None)
    response.update(synthetic_fixture=True, operation=mode + "_local_pulse", event_id=config["event_id"],
                    properties_sha256=hashlib.sha256(properties_payload(config).encode("ascii")).hexdigest(),
                    time_solver_executed=mode == "run", output_model=str(Path(prepare["output_model"]).with_name(config["event_id"] + ".mph")),
                    native_input_role="live Variables initsol/manualsolnum selects full native seed; variables_probe is diagnostic only",
                    time_solver_log="Synthetic Time message; no COMSOL run" if mode == "run" else "")
    times = np.linspace(180., 200., 101) if mode == "run" else np.array([180.])
    response["actual_times"] = response["solver_times"] = times.tolist()
    if mode == "inspect":
        for key in ("native_vectors", "native_derivatives"):
            response[key].pop("solver_first")
    parameters = _parameters(config)
    response["actual_pulse"] = {
        "amplitude_m_s2": p["acceleration_m_s2"], "xc_m": p["center_m"][0], "yc_m": p["center_m"][1],
        "sigma_m": p["sigma_m"], "direction_deg": p["direction_deg"], "direction": list(pulse_direction(config)),
        "on_s": p["on_s"], "off_s": p["off_s"], "parameters": parameters,
    }
    feature = _feature("local_vf", "VolumeForce", {"F": FORCE.copy()}, [1])
    response["force_feature"] = feature
    for phase in ("configured", "after"):
        snapshot = deepcopy(prepare["after"])
        snapshot["solver_features"][2]["properties"]["consistent"] = "off"
        snapshot["parameters"].update({name: {"expr": item["expression"], "value": item["value"], "unit": item["unit"]}
                                       for name, item in parameters.items()})
        snapshot["non_mesh"]["components"][0]["physics"][0]["features"].append(deepcopy(feature))
        response[phase] = snapshot
    response["geometry"] = {"component_tag": "comp1", "geometry_tag": "geom1", "length_unit": "m",
                            "bounds": [0., .4, 0., .24], "circle": {"feature_tag": "c1", "center": [.1, .12], "radius": .005}}
    response["material"] = {
        "material_tag": "mat1", "physics_feature_tag": "fp1", "density_expression": ["rho_f"],
        "dynamicviscosity_expression": ["mu_f"], "physics_rho_expression": "rho_f", "physics_mu_expression": "mu_f",
        "rho_f": {"expression": "998.2[kg/m^3]", "value": 998.2, "unit": "kg/m^3"},
        "mu_f": {"expression": "0.001004[Pa*s]", "value": .001004, "unit": "Pa*s"},
    }
    response["initialization_readback"] = {
        "control": "user", "initmethod": "sol", "initsol": "sol_branch_seed", "initsoluse": "current",
        "solnum": "manual", "manualsolnum": [901.], "native_seed_time": [180.],
        "native_seed_sha256": prepare["native_vectors"]["source"]["sha256"],
        "native_seed_udot_sha256": prepare["native_derivatives"]["source"]["sha256"],
    }
    diagnostic_times = [180., p["on_s"], (p["on_s"] + p["off_s"]) / 2, p["off_s"], 200.]
    expressions = [f"subst({force},t,{float(time)}[s])" for time in diagnostic_times for force in FORCE]
    direction = np.array([*pulse_direction(config), 0.])
    reference = source_integral_reference(config)
    gate = np.array([0., 0., 1., 0., 0.])
    vectors = (998.2 * p["acceleration_m_s2"] * gate[:, None] * direction[None])
    points = []
    x, y = p["center_m"]
    for role, xy, factor in (("center", [x, y], 1.), ("x_plus_sigma", [x + p["sigma_m"], y], math.exp(-.5)),
                             ("y_plus_sigma", [x, y + p["sigma_m"]], math.exp(-.5))):
        values = np.concatenate([(factor * vectors).ravel(), [180., *xy]])[:, None]
        node = _node("diag_" + role, expressions + ["t", "x", "y"], values, [1], point=True, coordinates=xy)
        node["role"] = role
        points.append(node)
    totals = reference["peak_force_per_depth_N_m"] * gate[:, None] * direction[None]
    response["force_evidence"] = {
        "expression_source_feature": "local_vf", "source_expressions": FORCE.copy(), "dataset_tag": "dset1",
        "solution_tag": "sol1", "native_solnum": 1, "native_solution_time": 180.,
        "diagnostic_times": diagnostic_times, "diagnostic_time_semantics": "prescribed_source_time_substitution; not future solved states",
        "points": points, "integral": _node("diag", expressions, totals.ravel()[:, None], [1], point=False),
    }
    if mode == "run":
        gate = np.array([math.sin(math.pi * (time - p["on_s"]) / (p["off_s"] - p["on_s"])) ** 2
                         if p["on_s"] < time < p["off_s"] else 0. for time in times])
        values = 998.2 * p["acceleration_m_s2"] * direction[:, None] * gate[None]
        point_values = np.vstack([values, times, np.full(101, x), np.full(101, y)])
        totals = reference["peak_force_per_depth_N_m"] * direction[:, None] * gate[None]
        integral = _node("actual", FORCE.copy(), totals, list(range(1, 102)), point=False)
        integral["force_per_depth_by_component"] = totals.tolist()
        response["actual_force_evaluation"] = {
            "expression_source_feature": "local_vf", "dataset_tag": "dset1", "solution_tag": "sol1", "actual_times": times.tolist(),
            "point_evaluation": _node("actual", FORCE + ["t", "x", "y"], point_values, list(range(1, 102)), point=True, coordinates=[x, y]),
            "integral": integral,
        }
    return response


@pytest.mark.parametrize("mode", ["inspect", "run"])
@pytest.mark.parametrize("direction", [0., 45., 90., -90., 180., -135.])
def test_configurable_vector_source_passes_for_axis_diagonal_and_negative_directions(native_case, mode, direction):
    config = pulse_config(direction_deg=direction)
    response = make_pulse_evidence(config, mode, native_case)
    report = validate_pulse_readback(response, config, mode=mode, prepare=native_case[0])
    assert report["status"] == "PASS"
    assert report["native_identity"]["source_seed_input_U_bytes_equal"] is True
    assert report["native_identity"]["source_seed_input_UDot_bytes_equal"] is True
    assert len(report["actual_times"]) == (101 if mode == "run" else 1)
    assert report["actual_saved_time_source"] is None if mode == "inspect" else report["actual_saved_time_source"]["passed"]
    assert "not future" in report["source_scope"] and "not net fluid momentum" in report["source_scope"]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("mode", ["inspect", "run"])
def test_zero_amplitude_preserves_zero_source_and_finite_reports(native_case, mode):
    config = pulse_config(acceleration_m_s2=0.)
    response = make_pulse_evidence(config, mode, native_case)
    report = validate_pulse_readback(response, config, mode=mode, prepare=native_case[0])
    assert report["status"] == "PASS"
    assert report["prescribed_source_diagnostics"]["peak_force_vector_N_per_m"] == [0., 0.]
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("fault", ["position", "direction", "sigma", "amplitude", "time", "missing_parameter",
                                   "parameter_unit", "angle_unit", "missing_dimensionless_unit", "properties_hash", "point_unit", "integral_unit",
                                   "dataset", "point_dataset", "native_time", "diagnostic_as_native", "point_native_time",
                                   "point_position", "sigma_point_force", "nonzero_off", "force_expression", "false_time",
                                   "inspect_future_axis", "inspect_solver_first", "source_path", "output_path", "parameter_snapshot",
                                   "mesh", "geometry", "material", "solver_rtol", "solver_consistent", "other_solver_setting",
                                   "native_input", "native_udot", "initialization_solnum", "force_inactive", "force_selection",
                                   "diagnostic_substitution", "missing_sigma_point", "z_force"])
def test_configurable_source_faults_reject_through_formal_validator(native_case, fault):
    config = pulse_config()
    response = make_pulse_evidence(config, "inspect", native_case)
    actual = response["actual_pulse"]
    evidence = response["force_evidence"]
    point = evidence["points"][0]
    if fault in ("position", "direction", "sigma", "amplitude", "time"):
        key = {"position": "xc_m", "direction": "direction_deg", "sigma": "sigma_m", "amplitude": "amplitude_m_s2", "time": "on_s"}[fault]
        actual[key] += .01
    elif fault == "missing_parameter":
        actual["parameters"].pop("local_sigma")
    elif fault in ("parameter_unit", "angle_unit"):
        actual["parameters"]["local_theta" if fault == "angle_unit" else "local_A"]["unit"] = "Pa"
    elif fault == "missing_dimensionless_unit":
        actual["parameters"]["local_dx"].pop("unit")
    elif fault == "properties_hash":
        response["properties_sha256"] = "0" * 64
    elif fault == "point_unit":
        point["units"][0] = "N"
    elif fault == "integral_unit":
        evidence["integral"]["units"][0] = "N/m^3"
    elif fault == "dataset":
        evidence["solution_tag"] = "wrong_solution"
    elif fault == "point_dataset":
        point["point_source_dataset"] = "another_dataset"
    elif fault in ("native_time", "diagnostic_as_native"):
        evidence["native_solution_time" if fault == "native_time" else "diagnostic_times"] = 183. if fault == "native_time" else [180.] * 5
    elif fault == "point_native_time":
        point["values_by_expression"][-3][0] = 183.
    elif fault == "point_position":
        point["values_by_expression"][-2][0] = .15
    elif fault == "sigma_point_force":
        evidence["points"][1]["values_by_expression"][6][0] *= .5
    elif fault == "nonzero_off":
        point["values_by_expression"][9][0] = .1
    elif fault == "force_expression":
        response["force_feature"]["properties"]["F"][0] = FORCE[0].replace("local_xc", "0.15[m]")
    elif fault == "false_time":
        response["time_solver_executed"] = True
    elif fault == "inspect_future_axis":
        response["actual_times"] = [180., 181.]
    elif fault == "inspect_solver_first":
        response["native_vectors"]["solver_first"] = _vector(frames=101)
    elif fault == "source_path":
        response["source_model"] = response["output_model"]
    elif fault == "output_path":
        response["output_model"] = response["source_model"]
    elif fault == "parameter_snapshot":
        response["configured"]["parameters"]["local_sigma"]["value"] *= 2
    elif fault == "mesh":
        response["mesh_after"]["sha256"] = "c" * 64
    elif fault == "geometry":
        response["geometry"]["circle"]["radius"] = .01
    elif fault == "material":
        response["material"]["mu_f"]["value"] *= 2
    elif fault in ("solver_rtol", "solver_consistent", "other_solver_setting"):
        props = response["after"]["solver_features"][2]["properties"]
        props[{"solver_rtol": "rtol", "solver_consistent": "consistent", "other_solver_setting": "atol"}[fault]] = "bweuler" if fault == "solver_consistent" else .123
    elif fault == "native_input":
        response["native_vectors"]["input"] = _vector("sol_branch_seed", 901, 1001, [.02, .001, .02, 2500.])
    elif fault == "native_udot":
        response["native_derivatives"]["input"]["sha256"] = "c" * 64
    elif fault == "initialization_solnum":
        response["initialization_readback"]["manualsolnum"] = [900.]
    elif fault == "force_inactive":
        response["force_feature"]["active"] = False
    elif fault == "force_selection":
        response["force_feature"]["selection_entities"] = [2]
    elif fault == "diagnostic_substitution":
        point["expressions"][6] = point["expressions"][6].replace("183.0[s]", "182.0[s]")
    elif fault == "missing_sigma_point":
        evidence["points"].pop()
    else:
        point["values_by_expression"][8][0] = .001
    with pytest.raises(ModelValidationError):
        validate_pulse_readback(response, config, mode="inspect", prepare=native_case[0])


@pytest.mark.parametrize("fault", ["missing_frame", "false_time", "wrong_force", "wrong_integral_dataset", "wrong_solnum"])
def test_run_mode_requires_actual_saved_times_and_force_nodes(native_case, fault):
    config = pulse_config()
    response = make_pulse_evidence(config, "run", native_case)
    if fault == "missing_frame":
        response["actual_times"].pop()
    elif fault == "false_time":
        response["time_solver_executed"] = False
    elif fault == "wrong_force":
        response["actual_force_evaluation"]["point_evaluation"]["values_by_expression"][0][15] *= 2
    elif fault == "wrong_integral_dataset":
        response["actual_force_evaluation"]["integral"]["dataset_tag"] = "wrong"
    else:
        response["actual_force_evaluation"]["integral"]["solnum"][2] = 99
    with pytest.raises(ModelValidationError):
        validate_pulse_readback(response, config, mode="run", prepare=native_case[0])


@pytest.mark.parametrize("mode", ["inspect", "run"])
def test_native_integral_outside_one_percent_returns_fail_without_widening_tolerance(native_case, mode):
    config = pulse_config(direction_deg=-135.)
    response = make_pulse_evidence(config, mode, native_case)
    key = "actual_force_evaluation" if mode == "run" else "force_evidence"
    integral = response[key]["integral"]
    integral["values_by_expression"] = (np.array(integral["values_by_expression"]) * 1.02).tolist()
    if mode == "run":
        integral["force_per_depth_by_component"] = deepcopy(integral["values_by_expression"])
    report = validate_pulse_readback(response, config, mode=mode, prepare=native_case[0])
    assert report["status"] == "FAIL"
    checked = report["actual_saved_time_source"] if mode == "run" else report["prescribed_source_diagnostics"]
    assert checked["native_integral_relative_tolerance"] == .01
    assert checked["spatial_vector_relative_error"] == pytest.approx(.02)


def test_inspect_impulse_is_explicit_analytic_envelope_not_a_claim_of_solved_future(native_case):
    config = pulse_config(on_s=181.2, off_s=184.)
    response = make_pulse_evidence(config, "inspect", native_case)
    report = validate_pulse_readback(response, config, mode="inspect", prepare=native_case[0])
    assert report["status"] == "PASS" and report["time_solver_executed"] is False
    assert report["actual_saved_time_source"] is None
    assert "analytic" in report["prescribed_source_diagnostics"]["impulse_method"]
    assert "solver_first_difference_by_field" not in report["native_identity"]


@pytest.mark.parametrize("direction,channel", [(45., 2), (90., 0), (0., 1)])
def test_identically_zero_source_integral_components_cannot_hide_under_relative_tolerance(native_case, direction, channel):
    config = pulse_config(direction_deg=direction)
    response = make_pulse_evidence(config, "inspect", native_case)
    response["force_evidence"]["integral"]["values_by_expression"][6 + channel][0] = 1e-8
    with pytest.raises(ModelValidationError, match="identically zero"):
        validate_pulse_readback(response, config, mode="inspect", prepare=native_case[0])
