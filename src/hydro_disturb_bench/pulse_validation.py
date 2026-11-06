"""Validate configurable source readbacks against a shared native checkpoint.

Prescribed-time source diagnostics are not future CFD states. Applied force and
impulse are per unit out-of-plane depth, not the fluid's net momentum change.
"""
from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from pathlib import Path

import numpy as np

from . import branch_evidence as native
from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .pulse_parameters import (
    BRANCH_TIME_S,
    CYLINDER_CENTER_M,
    CYLINDER_RADIUS_M,
    DENSITY_KG_M3,
    DOMAIN_BOUNDS_M,
    properties_payload,
    pulse_direction,
    source_integral_reference,
    validate_pulse_config,
)

_SPATIAL = "exp(-((x-local_xc)^2+(y-local_yc)^2)/(2*local_sigma^2))"
_GATE = "if((t>local_on)&&(t<local_off),sin(pi*(t-local_on)/(local_off-local_on))^2,0)"
FORCE_EXPRESSIONS = [f"rho_f*local_A*local_d{axis}*{_SPATIAL}*{_GATE}" for axis in ("x", "y")] + ["0[N/m^3]"]
_PROTOCOL = "native-restart-consistent-off-v2"
_DIAGNOSTIC_ROLE = "prescribed_source_time_substitution; not future solved states"
_INPUT_ROLE = "live Variables initsol/manualsolnum selects full native seed; variables_probe is diagnostic only"
_SOLVER_PLAN = {"source_solnum": 901, "branch_time_s": 180., "stop_s": 200., "frames": 101,
                "output_step_s": .2, "solver": {"rtol": 1e-4, "maxstepconstraintbdf": "const", "maxstepbdf_s": .05}}
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_LOCAL_KEYS = {"local_A", "local_xc", "local_yc", "local_sigma", "local_theta",
               "local_dx", "local_dy", "local_on", "local_off"}


def _require(condition, message):
    if not condition:
        raise ModelValidationError(message)


def _same(actual, expected, atol=1e-12):
    return native.sampling._same(actual, expected, atol)


def _array(value, shape, label):
    array = np.asarray(value)
    _require(array.dtype.kind in "fiu" and array.shape == shape and np.isfinite(array).all(),
             f"Invalid {label} numerical array")
    return array.astype(float)


def _literal(expression, value, unit):
    suffix = "" if unit == "1" else "[" + unit + "]"
    match = re.fullmatch("(" + _NUMBER + ")" + re.escape(suffix), str(expression))
    return match is not None and _same(float(match.group(1)), value)


def _pulse_parameters(response, config):
    pulse, actual = config["pulse"], response["actual_pulse"]
    dx, dy = pulse_direction(config)
    fields = {"amplitude_m_s2": pulse["acceleration_m_s2"], "xc_m": pulse["center_m"][0],
              "yc_m": pulse["center_m"][1], "sigma_m": pulse["sigma_m"],
              "direction_deg": pulse["direction_deg"], "on_s": pulse["on_s"], "off_s": pulse["off_s"]}
    _require(all(_same(actual.get(key), value) for key, value in fields.items())
             and _same(actual.get("direction"), [dx, dy]), "Actual pulse parameter values differ from config")
    expected = {
        "local_A": (fields["amplitude_m_s2"], "m/s^2"), "local_xc": (fields["xc_m"], "m"),
        "local_yc": (fields["yc_m"], "m"), "local_sigma": (fields["sigma_m"], "m"),
        "local_theta": (fields["direction_deg"], "deg"), "local_dx": (dx, "1"), "local_dy": (dy, "1"),
        "local_on": (fields["on_s"], "s"), "local_off": (fields["off_s"], "s"),
    }
    parameters = actual["parameters"]
    _require(set(parameters) == _LOCAL_KEYS, "Missing or extra actual local parameter")
    for name, (value, unit) in expected.items():
        item = parameters[name]
        _require(item.get("declared_unit") == unit and _same(item.get("value_in_declared_unit"), value)
                 and _literal(item.get("expression"), value, unit), f"Actual parameter expression/unit differs: {name}")
        if name == "local_theta":
            # COMSOL's raw evaluation is SI radians; the separate degree getter
            # proves the requested angle without assigning a nominal unit label.
            _require(item.get("unit") == "rad" and _same(item.get("value"), math.radians(value)),
                     "Actual angular parameter SI unit/value differs")
        else:
            # COMSOL returns an explicit JSON null for a dimensionless literal.
            # Absence of the getter field is not evidence of a unitless value.
            valid_unit = ("unit" in item and item["unit"] in (None, "", "1")) if unit == "1" else item.get("unit") == unit
            _require(valid_unit and _same(item.get("value"), value), f"Actual parameter SI unit/value differs: {name}")
    return parameters


def _geometry_material(response, prepare):
    geometry, material = response["geometry"], response["material"]
    mesh = prepare["mesh_before"]
    circle = geometry["circle"]
    _require(geometry.get("component_tag") == mesh["component_tag"]
             and geometry.get("geometry_tag") == mesh["geometry_tag"]
             and geometry.get("length_unit") == "m" and _same(geometry.get("bounds"), DOMAIN_BOUNDS_M)
             and circle.get("feature_tag") and _same(circle.get("center"), CYLINDER_CENTER_M)
             and _same(circle.get("radius"), CYLINDER_RADIUS_M), "Actual domain/cylinder geometry differs")
    _require(material.get("material_tag") and material.get("physics_feature_tag")
             and material.get("density_expression") == ["rho_f"]
             and material.get("dynamicviscosity_expression") == ["mu_f"]
             and material.get("physics_rho_expression") == "rho_f"
             and material.get("physics_mu_expression") == "mu_f", "Actual material/physics association differs")
    for name, value, unit in (("rho_f", DENSITY_KG_M3, "kg/m^3"), ("mu_f", .001004, "Pa*s")):
        item = material[name]
        _require(item.get("expression") and item.get("unit") == unit and _same(item.get("value"), value, 1e-15),
                 f"Actual material constant/unit differs: {name}")


def _remove_force(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list):
                value[key] = [item for item in child if not isinstance(item, dict) or item.get("tag") != "local_vf"]
            _remove_force(value[key])
    elif isinstance(value, list):
        for child in value:
            _remove_force(child)


def _snapshot(snapshot, *, configured):
    value = deepcopy(snapshot)
    if configured:
        value["parameters"] = {key: item for key, item in value["parameters"].items() if key not in _LOCAL_KEYS}
        _remove_force(value["non_mesh"])
    return native._normalized_snapshot(value, restart_original="bweuler" if configured else None)


def _snapshots(response, prepare, parameters):
    baseline = prepare["after"]
    expected = _snapshot(baseline, configured=False)
    feature = response["force_feature"]
    _require(feature.get("tag") == "local_vf" and feature.get("type") == "VolumeForce"
             and feature.get("active") is True and feature.get("selection_entities") == [1]
             and feature.get("properties", {}).get("F") == FORCE_EXPRESSIONS,
             "Actual active VolumeForce expressions/selection differ")
    unreadable = []
    for phase in ("before", "configured", "after"):
        snapshot = response[phase]
        changed = phase != "before"
        native._check_solver(snapshot, _SOLVER_PLAN, initialized=True, consistent="off" if changed else "bweuler")
        _require({key: val for key, val in snapshot["parameters"].items() if key.startswith("branch_")}
                 == {key: val for key, val in baseline["parameters"].items() if key.startswith("branch_")},
                 "Checkpoint branch initialization parameters changed")
        local = {key: val for key, val in snapshot["parameters"].items() if key.startswith("local_")}
        _require(set(local) == (_LOCAL_KEYS if changed else set()), "Unexpected local snapshot parameters")
        if changed:
            for name, item in parameters.items():
                _require(local[name] == {"expr": item["expression"], "value": item["value"], "unit": item["unit"]},
                         "Parameter summary is not bound to actual snapshot: " + name)
            nodes = [node for node in native._walk(snapshot["non_mesh"]) if node.get("tag") == "local_vf"]
            _require(len(nodes) == 1 and native._feature_signature(nodes[0]) == native._feature_signature(feature),
                     "Force summary is not bound to actual physics node")
        _require(_snapshot(snapshot, configured=changed) == expected, "Non-target readable settings changed: " + phase)
        for node in native._walk(snapshot):
            for key in node.get("unreadable_properties", {}):
                unreadable.append({"snapshot": phase, "path": node.get("path", node["tag"]), "property": key})
    return unreadable


def _native_identity(response, prepare, mode):
    _require(prepare.get("status") == "SUCCESS" and prepare.get("operation") == "prepare_branch_state"
             and prepare.get("time_solver_executed") is False, "Missing successful native checkpoint preparation")
    source_times = np.linspace(0., 200., 1001)
    _require(_same(prepare.get("source_times"), source_times) and _same(prepare.get("seed_times"), source_times)
             and prepare.get("source_solnum") == 901 and prepare.get("seed_solnum") == 901,
             "Prepared native source schedule differs")
    source_bytes, source = native._vector(prepare["native_vectors"]["source"], "sol1", 901, 180., 1001)
    derivative_bytes, derivative = native._derivative(prepare["native_derivatives"]["source"], "sol1", 901, 180., len(source))
    _require(np.isfinite(derivative).all(), "Original native derivative is not finite")
    mapping = native._mapping(prepare["source_dof_mapping"], len(source))
    for evidence in (prepare, response):
        _require(evidence.get("native_copy_method") == "copySolution_full_native_history"
                 and evidence.get("seed_solnum") == 901 and evidence.get("seed_frame_count") == 1001,
                 "Full native seed history/selection differs")
        _require(evidence["mesh_before"] == prepare["mesh_before"] == evidence["mesh_after"], "Native solver mesh changed")
        for key in ("source_dof_mapping", "initialized_dof_mapping"):
            _require(native._mapping(evidence[key], len(source)) == mapping, "Native DOF mapping changed")
        for key in ("seed",) if evidence is prepare else ("seed", "input"):
            raw, _ = native._vector(evidence["native_vectors"][key], "sol_branch_seed", 901, 180., 1001)
            dot, _ = native._derivative(evidence["native_derivatives"][key], "sol_branch_seed", 901, 180., len(source))
            _require(raw == source_bytes and dot == derivative_bytes, "Native seed/input U or UDot differs from source")
    _require(response.get("seed_provenance") == prepare.get("seed_provenance")
             and response.get("native_input_role") == _INPUT_ROLE, "Native provenance/initialization role differs")
    init = response["initialization_readback"]
    for key, value in {"control": "user", "initmethod": "sol", "initsol": "sol_branch_seed",
                       "initsoluse": "current", "solnum": "manual"}.items():
        _require(init.get(key) == value, "Actual selected initialization differs: " + key)
    _require(init.get("manualsolnum") == [901.] and _same(init.get("native_seed_time"), [180.])
             and init.get("native_seed_sha256") == hashlib.sha256(source_bytes).hexdigest()
             and init.get("native_seed_udot_sha256") == hashlib.sha256(derivative_bytes).hexdigest(),
             "Actual selected native initialization identity differs")
    result = {"source_seed_input_U_bytes_equal": True, "source_seed_input_UDot_bytes_equal": True,
              "dof_count": len(source), "source_U_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "source_UDot_sha256": hashlib.sha256(derivative_bytes).hexdigest(),
              "variables_probe_role": "Separate diagnostic; not observed internal Time input"}
    diagnostic_states = [("variables_probe", 1)] + ([("solver_first", 101)] if mode == "run" else [])
    for key, frames in diagnostic_states:
        _, state = native._vector(response["native_vectors"][key], "sol1", 1, 180., frames)
        _require(len(state) == len(source), "Native diagnostic/output DOF count changed")
        native._derivative(response["native_derivatives"][key], "sol1", 1, 180., len(source))
        result[key + "_difference_by_field"] = native._field_differences(source, state, mapping)
    return result


def _node(node, dataset, indices, *, point):
    _require(node.get("tag") and node.get("type") == ("EvalPoint" if point else "IntSurface")
             and node.get("solrepresentation") == "solnum" and node.get("innerinput") == "manual"
             and node.get("solnum") == indices, "Actual evaluation node or saved-solution selection differs")
    if point:
        _require(node.get("point_dataset_tag") and node.get("dataset_tag") == node["point_dataset_tag"]
                 and node.get("point_dataset_type") == "CutPoint2D" and node.get("point_source_dataset") == dataset,
                 "Actual force point Dataset chain differs")
    else:
        _require(node.get("dataset_tag") == dataset and node.get("selection") == [1],
                 "Actual source integral Dataset/domain differs")


def _substitution(expressions, times, *, coordinates):
    suffix = ["t", "x", "y"] if coordinates else []
    _require(isinstance(expressions, list) and len(expressions) == 3 * len(times) + len(suffix),
             "Incomplete prescribed-time expression list")
    for index, time in enumerate(times):
        for channel, force in enumerate(FORCE_EXPRESSIONS):
            match = re.fullmatch(re.escape("subst(" + force + ",t,") + "(" + _NUMBER + ")" + re.escape("[s])"),
                                 expressions[3 * index + channel])
            _require(match is not None and _same(float(match.group(1)), time),
                     "Diagnostic expression is not the actual force at its declared source time")
    if coordinates:
        _require(expressions[-3:] == suffix, "Diagnostic native time/coordinates expressions differ")


def _gate(times, pulse):
    return np.where((times > pulse["on_s"]) & (times < pulse["off_s"]),
                    np.sin(np.pi * (times - pulse["on_s"]) / (pulse["off_s"] - pulse["on_s"])) ** 2, 0.)


def _integrals(totals, times, reference, pulse, *, solved):
    gate = _gate(times, pulse)
    direction = np.array([*reference["direction"], 0.])
    _require(np.all(totals[direction == 0] == 0), "Source integral is nonzero for an identically zero force component")
    expected = reference["peak_force_per_depth_N_m"] * direction[:, None] * gate[None]
    _require(np.all(totals[:, gate == 0] == 0), "Source spatial integral is nonzero outside pulse support")
    scale = reference["peak_force_per_depth_N_m"]
    error = float(np.max(np.linalg.norm(totals - expected, axis=0)))
    relative = error / scale if scale else (0. if error == 0 else float("inf"))
    peak = totals[:, int(np.argmax(gate))]
    impulse = np.trapezoid(totals, times, axis=1) if solved else peak * reference["temporal_integral_s"]
    expected_impulse = np.array([*reference["impulse_vector_Ns_per_m"], 0.])
    impulse_scale = reference["impulse_per_depth_N_s_m"]
    impulse_error = float(np.linalg.norm(impulse - expected_impulse))
    impulse_relative = impulse_error / impulse_scale if impulse_scale else (0. if impulse_error == 0 else float("inf"))
    # Infinite ratios are represented as an explicit zero-amplitude mismatch,
    # so the validation report remains strict finite JSON.
    return {"passed": relative <= .01 and impulse_relative <= .01,
            "peak_force_vector_N_per_m": peak[:2].tolist(), "peak_Fz_N_per_m": float(peak[2]),
            "impulse_vector_Ns_per_m": impulse[:2].tolist(), "impulse_Fz_Ns_per_m": float(impulse[2]),
            "max_spatial_vector_absolute_error_N_per_m": error,
            "spatial_vector_relative_error": relative if np.isfinite(relative) else None,
            "impulse_vector_relative_error": impulse_relative if np.isfinite(impulse_relative) else None,
            "native_integral_relative_tolerance": .01,
            "impulse_method": "Trapezoidal actual saved-time source integrals" if solved else
                              "Diagnostic peak spatial integral times analytic sin-squared time integral",
            "scope": "Applied source per unit out-of-plane depth; not net fluid momentum change"}


def _diagnostics(response, config, reference):
    pulse, evidence = config["pulse"], response["force_evidence"]
    dataset, solution = response["dataset_tag"], response["solution_tag"]
    times = np.array([180., pulse["on_s"], (pulse["on_s"] + pulse["off_s"]) / 2, pulse["off_s"], 200.])
    _require(evidence.get("expression_source_feature") == "local_vf" and evidence.get("source_expressions") == FORCE_EXPRESSIONS
             and evidence.get("dataset_tag") == dataset and evidence.get("solution_tag") == solution
             and evidence.get("native_solnum") == 1 and _same(evidence.get("native_solution_time"), 180.)
             and evidence.get("diagnostic_time_semantics") == _DIAGNOSTIC_ROLE
             and _same(evidence.get("diagnostic_times"), times), "Prescribed source diagnostics misrepresent native/diagnostic times or source")
    x, y = pulse["center_m"]
    coordinates = {"center": [x, y], "x_plus_sigma": [x + pulse["sigma_m"], y],
                   "y_plus_sigma": [x, y + pulse["sigma_m"]]}
    points = evidence["points"]
    _require(len(points) == 3 and {point.get("role") for point in points} == set(coordinates), "Missing or duplicate center/sigma diagnostics")
    direction = np.array([*pulse_direction(config), 0.])
    for point in points:
        _node(point, dataset, [1], point=True)
        _substitution(point.get("expressions"), times, coordinates=True)
        _require(point.get("units") == ["N/m^3"] * 15 + ["s", "m", "m"], "Diagnostic point actual units differ")
        values = _array(point.get("values_by_expression"), (18, 1), "prescribed point")[:, 0]
        xy = coordinates[point["role"]]
        _require(_same(point.get("requested_coordinates"), xy) and _same(values[-3:], [180., *xy]),
                 "Diagnostic actual/requested coordinates or native solution time differ")
        factor = 1. if point["role"] == "center" else math.exp(-.5)
        expected = DENSITY_KG_M3 * pulse["acceleration_m_s2"] * factor * _gate(times, pulse)[:, None] * direction[None]
        _require(np.allclose(values[:15].reshape(5, 3), expected, rtol=1e-9, atol=1e-12),
                 "Actual center/sigma force vector differs from configured Gaussian pulse")
    integral = evidence["integral"]
    _node(integral, dataset, [1], point=False)
    _substitution(integral.get("expressions"), times, coordinates=False)
    _require(integral.get("units") == ["N/m"] * 15, "Diagnostic source integral actual units differ")
    totals = _array(integral.get("values_by_expression"), (15, 1), "prescribed integral")[:, 0].reshape(5, 3).T
    return _integrals(totals, times, reference, pulse, solved=False)


def _actual_force(response, config, reference, times):
    evidence, pulse = response["actual_force_evaluation"], config["pulse"]
    dataset, solution = response["dataset_tag"], response["solution_tag"]
    _require(evidence.get("expression_source_feature") == "local_vf" and evidence.get("dataset_tag") == dataset
             and evidence.get("solution_tag") == solution and _same(evidence.get("actual_times"), times),
             "Actual saved-time force Dataset/Solution/time differs")
    indices = list(range(1, 102))
    point, integral = evidence["point_evaluation"], evidence["integral"]
    _node(point, dataset, indices, point=True)
    _require(point.get("expressions") == FORCE_EXPRESSIONS + ["t", "x", "y"]
             and point.get("units") == ["N/m^3"] * 3 + ["s", "m", "m"], "Actual saved-time point expression/unit differs")
    values = _array(point.get("values_by_expression"), (6, 101), "actual point")
    x, y = pulse["center_m"]
    _require(_same(point.get("requested_coordinates"), [x, y]) and _same(values[3], times)
             and _same(values[4], np.full(101, x)) and _same(values[5], np.full(101, y)),
             "Actual saved-time force coordinates/time differ")
    expected = DENSITY_KG_M3 * pulse["acceleration_m_s2"] * np.array([*pulse_direction(config), 0.])[:, None] * _gate(times, pulse)[None]
    _require(np.allclose(values[:3], expected, rtol=1e-9, atol=1e-12), "Actual saved-time force curve differs")
    _node(integral, dataset, indices, point=False)
    _require(integral.get("expressions") == FORCE_EXPRESSIONS and integral.get("units") == ["N/m"] * 3,
             "Actual saved-time integral expression/unit differs")
    totals = _array(integral.get("values_by_expression"), (3, 101), "actual integral")
    _require(np.array_equal(_array(integral.get("force_per_depth_by_component"), (3, 101), "actual integral alias"), totals),
             "Actual integral aliases disagree")
    return _integrals(totals, times, reference, pulse, solved=True)


def validate_pulse_readback(response, config, *, mode, prepare):
    """Validate actual source readbacks; source-integral sensitivity returns FAIL.

    Bad provenance/contracts raise ModelValidationError. The caller separately
    pins input/output file hashes and validates full raw solver step logs.
    """
    if mode not in ("inspect", "run"):
        raise InvalidParameterError("Pulse validation mode must be inspect or run")
    config = validate_pulse_config(config)
    try:
        _require(response.get("status") == "SUCCESS" and response.get("operation") == mode + "_local_pulse"
                 and response.get("time_solver_executed") is (mode == "run")
                 and response.get("restart_protocol_id") == _PROTOCOL and response.get("event_id") == config["event_id"],
                 "Actual operation/mode/event/restart protocol differs")
        expected_hash = hashlib.sha256(properties_payload(config).encode("ascii")).hexdigest()
        _require(response.get("properties_sha256") == expected_hash, "Decoded properties payload SHA-256 differs")
        _require(Path(response["source_model"]).resolve() == Path(prepare["output_model"]).resolve()
                 and Path(response["output_model"]).resolve() not in
                     (Path(prepare["source_model"]).resolve(), Path(prepare["output_model"]).resolve()),
                 "Pulse source/output does not preserve the common checkpoint and original model")
        _require(response.get("dataset_tag") == prepare.get("dataset_tag") and response.get("solution_tag") == "sol1",
                 "Pulse Dataset/Solution differs from native source")
        times = np.linspace(180., 200., 101) if mode == "run" else np.array([BRANCH_TIME_S])
        _require(_same(response.get("actual_times"), times) and _same(response.get("solver_times"), times),
                 "Actual solved time axis differs from operation mode")
        if mode == "inspect":
            _require("solver_first" not in response["native_vectors"] and "solver_first" not in response["native_derivatives"]
                     and "actual_force_evaluation" not in response and response.get("time_solver_log") == "",
                     "Inspect-only response claims Time solver output")
        else:
            _require(isinstance(response.get("time_solver_log"), str) and bool(response["time_solver_log"].strip()),
                     "Missing actual Time solver message")
        parameters = _pulse_parameters(response, config)
        _geometry_material(response, prepare)
        identity = _native_identity(response, prepare, mode)
        unreadable = _snapshots(response, prepare, parameters)
        reference = source_integral_reference(config)
        diagnostic = _diagnostics(response, config, reference)
        actual = _actual_force(response, config, reference, times) if mode == "run" else None
        passed = diagnostic["passed"] and (actual is None or actual["passed"])
        return {"status": "PASS" if passed else "FAIL", "mode": mode, "event_id": config["event_id"],
                "properties_sha256": expected_hash, "time_solver_executed": mode == "run",
                "actual_times": times.tolist(), "native_identity": identity, "independent_source_reference": reference,
                "prescribed_source_diagnostics": diagnostic, "actual_saved_time_source": actual,
                "unreadable_properties": unreadable,
                "settings_scope": "Readable snapshots and native mesh/DOF evidence; unreadable properties are not independently certified",
                "source_scope": "Applied source per unit depth, not net fluid momentum change; prescribed diagnostics are not future solved states"}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        raise ModelValidationError(f"Malformed configurable pulse readback: {exc}") from exc
