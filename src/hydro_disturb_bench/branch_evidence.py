"""Validate native restart bytes and actual force-node readbacks for the pilot."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

import numpy as np

from . import field_sampling as sampling

FY_EXPRESSION = (
    "rho_f*branch_A*exp(-((x-branch_xc)^2+(y-branch_yc)^2)/(2*branch_sigma^2))"
    "*if((t>branch_on)&&(t<branch_off),sin(pi*(t-branch_on)/(branch_off-branch_on))^2,0)"
)
FORCE_EXPRESSIONS = ["0[N/m^3]", FY_EXPRESSION, "0[N/m^3]"]
INITIALIZATION_KEYS = {
    "control",
    "initmethod",
    "initsol",
    "initsoluse",
    "solnum",
    "manualsolnum",
    "cname",
    "clist",
    "solvertype",
    "manualsol",
    "t",
    "propertyDefaults",
}
require = sampling._require


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _bytes(item, size, finite=True):
    require(
        item.get("dtype") == "float64" and item.get("byte_order") == "little",
        "Native arrays must be little-endian float64",
    )
    try:
        raw = base64.b64decode(item.get("base64", ""), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise sampling.bridge.ModelValidationError("Invalid native base64 bytes") from exc
    require(len(raw) == size * 8 and size > 0, "Native byte count differs from declared array size")
    require(
        hashlib.sha256(raw).hexdigest() == item.get("sha256"),
        "Native SHA-256 does not match decoded bytes",
    )
    values = np.frombuffer(raw, dtype="<f8")
    require(not finite or np.isfinite(values).all(), "Native state contains NaN/Inf")
    return raw, values


def _vector(item, tag, solnum, time, frames):
    count = item.get("count")
    require(_integer(count) and count > 0, "Missing native DOF count")
    require(
        item.get("solution_tag") == tag
        and item.get("solnum") == solnum
        and sampling._same(item.get("time"), time),
        "Native vector solution/time selection mismatch",
    )
    require(
        item.get("solution_size") == [count, frames],
        "Native solution size differs from selected source",
    )
    return _bytes(item, count)


def _derivative(item, tag, solnum, time, count):
    require(
        item.get("available") is True
        and item.get("solution_tag") == tag
        and item.get("solnum") == solnum
        and sampling._same(item.get("time"), time)
        and item.get("count") == count,
        "Missing or misassociated native UDot",
    )
    raw, values = _bytes(item, count, finite=False)
    counts = {
        "finite_count": int(np.isfinite(values).sum()),
        "nan_count": int(np.isnan(values).sum()),
        "positive_infinity_count": int(np.isposinf(values).sum()),
        "negative_infinity_count": int(np.isneginf(values).sum()),
    }
    require(
        all(item.get(key) == value for key, value in counts.items()),
        "UDot finite/nonfinite counts disagree with decoded bytes",
    )
    return raw, values


def _mapping(item, count):
    require(
        item.get("hash_encoding") == "UTF-8 exact JSON payload below",
        "Unknown DOF mapping hash encoding",
    )
    payload = item.get("payload", {})
    # The Java evidence contract uses comma-space for its named/int arrays,
    # but compact JSON for the coordinate-byte object and outer object.
    keys = (
        "n_dofs",
        "field_names",
        "field_dof_counts",
        "dof_names",
        "name_indices",
        "solution_vector_indices",
        "geometry_indices",
        "coordinates",
    )
    require(set(payload) == set(keys), "Unknown or incomplete DOF mapping payload")
    encoded = (
        "{"
        + ",".join(
            json.dumps(key)
            + ":"
            + json.dumps(
                payload[key],
                separators=((", ", ":") if isinstance(payload[key], list) else (",", ":")),
                ensure_ascii=False,
                allow_nan=False,
            )
            for key in keys
        )
        + "}"
    ).encode("utf-8")
    require(
        hashlib.sha256(encoded).hexdigest() == item.get("sha256"),
        "DOF mapping payload hash mismatch",
    )
    require(payload.get("n_dofs") == count, "DOF mapping size differs from native state")
    names, fields = payload.get("dof_names", []), payload.get("field_names", [])
    counts = payload.get("field_dof_counts", [])
    require(
        names
        and len(names) == len(set(names))
        and fields
        and len(fields) == len(counts)
        and all(_integer(v) and v >= 0 for v in counts)
        and sum(counts) == count,
        "Invalid named native field counts",
    )
    indices = np.asarray(payload.get("solution_vector_indices", []))
    named = np.asarray(payload.get("name_indices", []))
    geometry = np.asarray(payload.get("geometry_indices", []))
    require(
        indices.shape == named.shape == geometry.shape == (count,)
        and all(a.dtype.kind in "iu" for a in (indices, named, geometry))
        and np.array_equal(np.sort(indices), np.arange(count))
        and np.all((named >= 0) & (named < len(names))),
        "Invalid or incomplete native DOF ordering",
    )
    coords = payload.get("coordinates", {})
    shape = coords.get("shape", [])
    require(
        len(shape) == 2
        and all(_integer(v) and v > 0 for v in shape)
        and shape[1] == count
        and coords.get("order") == "C",
        "Invalid DOF coordinate layout",
    )
    _, coordinate_values = _bytes(coords, int(np.prod(shape)), finite=False)
    require(
        coords.get("non_finite_count") == int((~np.isfinite(coordinate_values)).sum()),
        "DOF coordinate nonfinite count mismatch",
    )
    return payload


def _walk(value):
    if isinstance(value, dict):
        if "properties" in value and "tag" in value:
            yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _one_feature(snapshot, kind):
    found = [node for node in snapshot["solver_features"] if node.get("type") == kind]
    require(len(found) == 1, f"Expected one actual {kind} solver feature")
    return found[0]


def _number(value):
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    try:
        return float(value)
    except (ValueError, TypeError):
        return float("nan")


def _check_solver(snapshot, plan, initialized, consistent="bweuler"):
    time = _one_feature(snapshot, "Time")["properties"]
    require(
        time.get("consistent") == consistent,
        "Actual consistent initialization differs from the registered restart protocol",
    )
    target = plan["solver"]
    require(
        sampling._same(_number(time.get("rtol")), target["rtol"], 1e-15)
        and time.get("maxstepconstraintbdf") == target["maxstepconstraintbdf"]
        and sampling._same(_number(time.get("maxstepbdf")), target["maxstepbdf_s"], 1e-15),
        "Actual Time solver settings differ from frozen plan",
    )
    if initialized:
        variables = _one_feature(snapshot, "Variables")["properties"]
        for key, expected in {
            "control": "user",
            "initmethod": "sol",
            "initsol": "sol_branch_seed",
            "initsoluse": "current",
            "solnum": "manual",
        }.items():
            require(variables.get(key) == expected, f"Actual native initialization differs: {key}")
        require(
            _number(variables.get("manualsolnum")) == plan["source_solnum"],
            "Initialization must select the original frame in the full native seed",
        )
        clist = variables.get("clist", "")
        if isinstance(clist, str):
            clist = clist.split(";")
        require(isinstance(clist, list) and len(clist) == 2, "Missing actual Variables time list")
        first = [_number(v) for v in str(clist[0]).split(",")]
        step = _number(clist[1])
        # Variables expands its starting time into the output list and chooses
        # 0.1*dt_out as its initial timestep. Neither is the BDF maximum step.
        expected = np.linspace(plan["branch_time_s"], plan["stop_s"], plan["frames"])
        require(
            variables.get("cname") == ["t", "timestep"]
            and (sampling._same(first, [plan["branch_time_s"]]) or sampling._same(first, expected))
            and any(
                sampling._same(step, value)
                for value in (plan["output_step_s"], 0.1 * plan["output_step_s"])
            )
            and variables.get("solvertype") == "solnum"
            and variables.get("manualsol") == "on"
            and np.isfinite(_number(variables.get("t"))),
            "Native Variables time initialization mismatch",
        )
        schedule = "range(180,0.2,200)"
        for value in (time.get("tlist"), snapshot["study_step"]["properties"].get("tlist")):
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            require(
                sampling._same(value, expected)
                or (isinstance(value, str) and value.replace(" ", "") == schedule),
                "Actual branch output schedule differs",
            )


def _normalized_snapshot(snapshot, preparation=False, force=False, restart_original=None):
    result = deepcopy(snapshot)
    if restart_original is not None:
        # Only after _check_solver verifies the authorized branch value; never
        # discard this property or allow another solver setting to drift.
        properties = _one_feature(result, "Time")["properties"]
        properties["consistent"] = restart_original
        # COMSOL records the overridden property's original default as this
        # exact pair. Preserve every other default-bookkeeping entry.
        defaults = properties.get("propertyDefaults")
        if isinstance(defaults, list):
            permitted = ["consistent", restart_original]
            require(defaults.count(permitted) <= 1, "Duplicate consistent default metadata")
            properties["propertyDefaults"] = [item for item in defaults if item != permitted]
    result["parameters"] = {
        k: v for k, v in result["parameters"].items() if not k.startswith("branch_")
    }
    if force:

        def remove(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, list):
                        node[key] = [
                            v
                            for v in value
                            if not isinstance(v, dict) or v.get("tag") != "branch_vf"
                        ]
                    remove(node[key])
            elif isinstance(node, list):
                for child in node:
                    remove(child)

        remove(result["non_mesh"])
    for node in _walk(result["solver_features"]):
        # Solver runtime messages are observable but are not solver settings.
        node["properties"].pop("message", None)
        if node.get("type") == "Time":
            if preparation:
                node["properties"].pop("tlist", None)
        if node.get("type") == "Variables":
            node["properties"].pop("clist", None)  # Separately validated above.
            node["properties"].pop("propertyDefaults", None)  # Getter/default bookkeeping.
        if preparation and node.get("type") == "Variables":
            for key in INITIALIZATION_KEYS:
                node["properties"].pop(key, None)
    if preparation:
        result["study_step"]["properties"].pop("tlist", None)
    return result


def _check_parameters(snapshot, plan, amplitude=None, source=False):
    parameters = snapshot["parameters"]
    expected = (
        {}
        if source
        else {
            "branch_tb": (plan["branch_time_s"], "s"),
            "branch_source_solnum": (plan["source_solnum"], None),
            "branch_seed_solnum": (plan["source_solnum"], None),
        }
    )
    if amplitude is not None:
        pulse = plan["pulse"]
        expected.update(
            {
                "branch_A": (amplitude, "m/s^2"),
                "branch_xc": (pulse["center_m"][0], "m"),
                "branch_yc": (pulse["center_m"][1], "m"),
                "branch_sigma": (pulse["sigma_m"], "m"),
                "branch_on": (pulse["on_s"], "s"),
                "branch_off": (pulse["off_s"], "s"),
            }
        )
    require(
        {k for k in parameters if k.startswith("branch_")} == set(expected),
        "Unexpected or missing branch parameter",
    )
    for key, (value, unit) in expected.items():
        item = parameters[key]
        require(
            item.get("expr")
            and sampling._same(item.get("value"), value)
            and (item.get("unit") == unit if unit else item.get("unit") in (None, "", "1")),
            f"Actual branch parameter differs: {key}",
        )
    for key, scale, unit in (
        ("rho_f", plan["scales"]["rho_f_kg_m3"], "kg/m^3"),
        ("U_inf", plan["scales"]["U_inf_m_s"], "m/s"),
    ):
        require(
            key in parameters
            and sampling._same(parameters[key].get("value"), scale)
            and parameters[key].get("unit") == unit,
            f"Actual model scale differs: {key}",
        )


def _feature_signature(feature):
    result = deepcopy(feature)
    for node in _walk(result):
        node.pop("path", None)
    return result


def _field_differences(source, initialized, mapping):
    indices = np.asarray(mapping["solution_vector_indices"])
    named = np.asarray(mapping["name_indices"])
    result = []
    for field, name in enumerate(mapping["dof_names"]):
        selection = indices[named == field]
        require(len(selection) > 0, "Named DOF field is empty")
        left, right = source[selection], initialized[selection]
        result.append(
            {
                "name": name,
                "count": len(selection),
                "different_count": int(np.count_nonzero(left.view("<u8") != right.view("<u8"))),
                "max_absolute_difference": float(np.max(abs(left - right))),
                "source_range": [float(left.min()), float(left.max())],
                "initialized_range": [float(right.min()), float(right.max())],
            }
        )
    return result


def _force(evidence, feature, dataset, solution, times, amplitude, plan):
    require(
        feature.get("tag") == "branch_vf"
        and feature.get("type") == "VolumeForce"
        and feature.get("active") is True
        and feature.get("selection_entities") == [1]
        and feature.get("properties", {}).get("F") == FORCE_EXPRESSIONS,
        "Actual VolumeForce feature differs from frozen source",
    )
    require(
        evidence.get("expression_source_feature") == feature["tag"]
        and evidence.get("dataset_tag") == dataset
        and evidence.get("solution_tag") == solution
        and sampling._same(evidence.get("actual_times"), times),
        "Force evidence Dataset/Solution/time mismatch",
    )
    point, integral = evidence.get("point_evaluation", {}), evidence.get("integral", {})
    require(
        point.get("type") == "EvalPoint"
        and point.get("tag")
        and point.get("dataset_tag") == point.get("point_dataset_tag")
        and bool(point.get("point_dataset_tag"))
        and point.get("point_dataset_type") == "CutPoint2D"
        and point.get("point_source_dataset") == dataset
        and point.get("expressions") == FORCE_EXPRESSIONS + ["t", "x", "y"]
        and point.get("units") == ["N/m^3"] * 3 + ["s", "m", "m"],
        "Actual force point expression/unit/association mismatch",
    )
    require(
        integral.get("type") == "IntSurface"
        and integral.get("tag")
        and integral.get("dataset_tag") == dataset
        and integral.get("selection") == [1]
        and integral.get("expressions") == [FY_EXPRESSION]
        and integral.get("units") == ["N/m"],
        "Actual force integral expression/unit/association mismatch",
    )
    for node in (point, integral):
        require(
            node.get("solrepresentation") == "solnum"
            and node.get("innerinput") == "manual"
            and node.get("solnum") == list(range(1, len(times) + 1)),
            "Force evaluation saved-solution selection mismatch",
        )
    values = np.asarray(point.get("values_by_expression"), dtype=float)
    totals = np.asarray(integral.get("Fy_per_depth"), dtype=float)
    require(
        values.shape == (6, len(times))
        and np.isfinite(values).all()
        and totals.shape == (len(times),)
        and np.isfinite(totals).all(),
        "Invalid force numerical arrays",
    )
    pulse = plan["pulse"]
    require(
        sampling._same(values[3], times)
        and sampling._same(values[4], np.full(len(times), pulse["center_m"][0]))
        and sampling._same(values[5], np.full(len(times), pulse["center_m"][1])),
        "Force point actual coordinates/time differ",
    )
    gate = np.where(
        (times > pulse["on_s"]) & (times < pulse["off_s"]),
        np.sin(np.pi * (times - pulse["on_s"]) / (pulse["off_s"] - pulse["on_s"])) ** 2,
        0,
    )
    expected = plan["scales"]["rho_f_kg_m3"] * amplitude * gate
    require(
        np.all(values[[0, 2]] == 0) and np.allclose(values[1], expected, rtol=1e-9, atol=1e-12),
        "Actual center force differs from rho*a*sin^2 pulse",
    )
    if amplitude == 0 or len(times) == 1:
        require(
            np.all(values[:3] == 0) and np.all(totals == 0),
            "Zero or initial branch force is not zero",
        )
        return {"passed": True, "peak_force_per_depth_N_m": 0.0, "impulse_per_depth_N_s_m": 0.0}
    reference = plan["source_integral_reference"]
    peak = float(np.max(totals))
    impulse = float(np.sum((totals[:-1] + totals[1:]) * np.diff(times) / 2))
    peak_error = abs(peak / reference["peak_force_per_depth_N_m"] - 1)
    impulse_error = abs(impulse / reference["impulse_per_depth_N_s_m"] - 1)
    require(
        np.all(totals[gate == 0] == 0) and np.all(totals >= 0),
        "Native force integral has wrong sign or support",
    )
    return {
        "passed": max(peak_error, impulse_error) <= reference["native_integral_relative_tolerance"],
        "peak_force_per_depth_N_m": peak,
        "impulse_per_depth_N_s_m": impulse,
        "peak_relative_error": peak_error,
        "impulse_relative_error": impulse_error,
    }


def validate_native_branch_evidence(prepare, zero, pulse, plan):
    """Recompute native hashes and validate the actual initialization/source chain.

    Structural/provenance contradictions raise ModelValidationError; an otherwise
    valid native source integral outside its preregistered tolerance returns FAIL.
    Source file hashes and solver step logs remain the runner's separate gates.
    """
    try:
        return _validate_native(prepare, zero, pulse, plan)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise sampling.bridge.ModelValidationError(
            f"Malformed native branch evidence: {exc}"
        ) from exc


def validate_prepared_native_evidence(prepare, plan):
    """Validate only a full native checkpoint; never certify unrun branches."""
    try:
        return _validate_native(prepare, None, None, plan, prepare_only=True)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise sampling.bridge.ModelValidationError(
            f"Malformed native preparation evidence: {exc}"
        ) from exc


def _validate_native(prepare, zero, pulse, plan, prepare_only=False):
    protocol = None
    if not prepare_only:
        protocol = plan.get("restart_protocol")
        require(
            isinstance(protocol, dict)
            and protocol.get("id") == "native-restart-consistent-off-v2"
            and protocol.get("version") == 2
            and protocol.get("decision_before_this_protocol_time_solves") is True
            and protocol.get("completed_new_future_trajectories_at_decision") == 0
            and protocol.get("solver_change")
            == {
                "feature_type": "Time",
                "property": "consistent",
                "original": "bweuler",
                "both_branches": "off",
            },
            "Missing or unsupported frozen restart protocol",
        )
    require(
        prepare.get("status") == "SUCCESS"
        and prepare.get("operation") == "prepare_branch_state"
        and prepare.get("time_solver_executed") is False,
        "Preparation is not a successful no-Time-solve operation",
    )
    require(
        Path(prepare["source_model"]).resolve() == Path(plan["source_model"]).resolve(),
        "Prepared source model differs from frozen model",
    )
    checkpoint = Path(prepare["output_model"]).resolve()
    require(
        checkpoint != Path(prepare["source_model"]).resolve(),
        "Checkpoint overwrites the source model",
    )
    times = np.asarray(prepare["source_times"], dtype=float)
    source_index, tb = plan["source_solnum"], plan["branch_time_s"]
    solution, dataset = plan["source_solution"], prepare["dataset_tag"]
    require(
        dataset
        and prepare["solution_tag"] == solution
        and prepare["source_solnum"] == source_index
        and prepare["seed_solnum"] == source_index
        and prepare["seed_solution_tag"] == "sol_branch_seed",
        "Prepared solution mapping differs",
    )
    require(
        times.ndim == 1
        and len(times) >= source_index
        and np.isfinite(times).all()
        and np.all(np.diff(times) > 0)
        and np.flatnonzero(abs(times - tb) <= sampling.TIME_ATOL).tolist() == [source_index - 1],
        "Original native saved time is not uniquely selected",
    )
    original_bytes, original = _vector(
        prepare["native_vectors"]["source"], solution, source_index, tb, len(times)
    )
    source_hash = hashlib.sha256(original_bytes).hexdigest()
    derivative_bytes, original_derivative = _derivative(
        prepare["native_derivatives"]["source"], solution, source_index, tb, len(original)
    )
    require(np.isfinite(original_derivative).all(), "Source native derivative is not finite")
    derivative_hash = hashlib.sha256(derivative_bytes).hexdigest()
    provenance = dict(item.split("=", 1) for item in prepare["seed_provenance"].split(";"))
    require(
        provenance.get("source_solution") == solution
        and provenance.get("source_solnum") == str(source_index)
        and sampling._same(_number(provenance.get("source_time_s")), tb)
        and provenance.get("float64_le_sha256") == source_hash
        and provenance.get("udot_float64_le_sha256") == derivative_hash
        and provenance.get("copy_method") == "copySolution_full_native_history",
        "Stored seed provenance is not bound to source bytes",
    )
    mapping = _mapping(prepare["source_dof_mapping"], len(original))
    fingerprint = prepare["mesh_before"]
    require(
        all(fingerprint.get(k) for k in ("component_tag", "geometry_tag", "mesh_tag", "hash_input"))
        and all(
            _integer(fingerprint.get(k)) and fingerprint[k] > 0
            for k in ("vertex_count", "element_count")
        )
        and re.fullmatch("[0-9a-f]{64}", fingerprint.get("sha256", "")),
        "Missing native mesh fingerprint",
    )
    base_snapshot = _normalized_snapshot(prepare["before"], preparation=True)
    differences, force_results, unavailable = {}, {}, []
    variables_probes = {}
    solved_first = {}
    actual = np.linspace(tb, plan["stop_s"], plan["frames"])
    outputs = {checkpoint}
    responses = [("prepare", prepare, None)]
    if not prepare_only:
        responses.extend(
            (("zero", zero, 0.0), ("pulse", pulse, plan["pulse"]["acceleration_amplitude_m_s2"]))
        )
    for name, response, amplitude in responses:
        is_prepare = name == "prepare"
        require(
            response.get("native_copy_method") == "copySolution_full_native_history"
            and response.get("seed_solnum") == source_index
            and response.get("seed_frame_count") == len(times)
            and np.array_equal(np.asarray(response.get("seed_times")), times),
            "Full native seed history is missing or differs from source",
        )
        if not is_prepare:
            require(
                response.get("restart_protocol_id") == protocol["id"],
                "Actual branch restart protocol id is missing or mismatched",
            )
            require(
                response.get("status") == "SUCCESS"
                and response.get("operation") == "run_pulse_branch"
                and response.get("time_solver_executed") is True
                and response.get("amplitude_m_s2") == amplitude,
                f"Invalid {name} branch operation",
            )
            require(
                Path(response["source_model"]).resolve() == checkpoint,
                "Branch does not load the common checkpoint",
            )
            output = Path(response["output_model"]).resolve()
            require(
                output not in outputs and output != Path(prepare["source_model"]).resolve(),
                "Branch output reuses a protected model path",
            )
            outputs.add(output)
            require(
                response["dataset_tag"] == dataset
                and response["solution_tag"] == solution
                and response["seed_provenance"] == prepare["seed_provenance"]
                and sampling._same(response["actual_times"], actual),
                "Branch Dataset/source/time association mismatch",
            )
            require(
                response.get("initialization_path")
                == "native seed -> runAll(Compile, Variables, Time); prior Variables execution is a validation probe only"
                and response.get("native_input_role")
                == "full native seed frame selected by live Variables initsol/manualsolnum; variables_probe is not claimed to be the internal Time input",
                "Branch initialization role/path is missing or ambiguous",
            )
        require(
            response["mesh_before"] == fingerprint and response["mesh_after"] == fingerprint,
            "Native mesh changed across preparation or branches",
        )
        for key in ("source_dof_mapping", "initialized_dof_mapping"):
            require(
                _mapping(response[key], len(original)) == mapping,
                "Native DOF field/coordinate/order mapping changed",
            )
        vectors = response["native_vectors"]
        derivatives = response["native_derivatives"]
        for key in ("seed",) if is_prepare else ("seed", "input"):
            raw, _ = _vector(vectors[key], "sol_branch_seed", source_index, tb, len(times))
            require(
                raw == original_bytes,
                "Selected native seed bytes differ from the original saved state",
            )
            raw_derivative, _ = _derivative(
                derivatives[key], "sol_branch_seed", source_index, tb, len(original)
            )
            require(
                raw_derivative == derivative_bytes,
                "Selected native seed UDot differs from original",
            )
        probe_bytes, variables_probe = _vector(vectors["variables_probe"], solution, 1, tb, 1)
        require(
            len(variables_probe) == len(original), "Variables probe native vector length changed"
        )
        _derivative(derivatives["variables_probe"], solution, 1, tb, len(original))
        differences[name] = {
            "variables_probe_sha256": vectors["variables_probe"]["sha256"],
            "variables_probe_raw_dof_max_absolute_difference": float(
                np.max(abs(variables_probe - original))
            ),
            "variables_probe_difference_by_field": _field_differences(
                original, variables_probe, mapping
            ),
            "variables_probe_udot_sha256": derivatives["variables_probe"]["sha256"],
            "selected_seed_sha256": source_hash,
            "selected_seed_udot_sha256": derivative_hash,
        }
        if not is_prepare:
            variables_probes[name] = (probe_bytes, variables_probe)
            _, first = _vector(vectors["solver_first"], solution, 1, tb, len(actual))
            require(len(first) == len(original), "First solved native vector length changed")
            _derivative(derivatives["solver_first"], solution, 1, tb, len(original))
            solved_first[name] = first
            differences[name].update(
                solver_first_sha256=vectors["solver_first"]["sha256"],
                solver_first_raw_dof_max_absolute_difference=float(np.max(abs(first - original))),
                solver_first_difference_by_field=_field_differences(original, first, mapping),
                solver_first_udot_sha256=derivatives["solver_first"]["sha256"],
            )
        for phase in ("before", "configured", "after"):
            snap = response[phase]
            source = is_prepare and phase == "before"
            branch_force = not is_prepare and phase != "before"
            restart_original = protocol["solver_change"]["original"] if branch_force else None
            expected_consistent = (
                protocol["solver_change"]["both_branches"] if branch_force else "bweuler"
            )
            _check_solver(snap, plan, initialized=not source, consistent=expected_consistent)
            _check_parameters(
                snap, plan, amplitude=amplitude if branch_force else None, source=source
            )
            require(
                _normalized_snapshot(
                    snap, preparation=True, force=branch_force, restart_original=restart_original
                )
                == base_snapshot,
                f"Non-target readable model settings changed: {name}/{phase}",
            )
            if not is_prepare:
                require(
                    _normalized_snapshot(
                        snap, force=branch_force, restart_original=restart_original
                    )
                    == _normalized_snapshot(prepare["after"]),
                    f"Prepared initialization/settings changed: {name}/{phase}",
                )
            for node in _walk(snap):
                for property_name in node.get("unreadable_properties", {}):
                    unavailable.append(
                        {
                            "snapshot": name + "/" + phase,
                            "path": node.get("path", node["tag"]),
                            "property": property_name,
                        }
                    )
            if branch_force:
                nodes = [node for node in _walk(snap["non_mesh"]) if node.get("tag") == "branch_vf"]
                require(
                    len(nodes) == 1
                    and _feature_signature(nodes[0])
                    == _feature_signature(response["force_feature"]),
                    "Force readback is not the actual physics feature in the model",
                )
        if not is_prepare:
            _force(
                response["force_initial_evaluation"],
                response["force_feature"],
                dataset,
                solution,
                np.asarray([tb]),
                amplitude,
                plan,
            )
            force_results[name] = _force(
                response["force_evaluation"],
                response["force_feature"],
                dataset,
                solution,
                np.asarray(response["actual_times"]),
                amplitude,
                plan,
            )
    passed = all(item["passed"] for item in force_results.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "complete_branch_evidence": not prepare_only,
        "restart_protocol_id": None if prepare_only else protocol["id"],
        "registered_solver_change": None if prepare_only else protocol["solver_change"],
        "source_seed_vector_bytes_equal": True,
        "source_seed_derivative_bytes_equal": True,
        "variables_probe_cross_branch_bytes_equal": None
        if prepare_only
        else variables_probes["zero"][0] == variables_probes["pulse"][0],
        "variables_probe_sha256_by_branch": {
            name: hashlib.sha256(item[0]).hexdigest() for name, item in variables_probes.items()
        },
        "variables_probe_cross_branch_difference_by_field": []
        if prepare_only
        else _field_differences(variables_probes["zero"][1], variables_probes["pulse"][1], mapping),
        "variables_probe_comparison_policy": "Diagnostic only: a separate Variables execution does not observe runAll's internal Time input. Differences are reported without a new numerical gate; strict source/seed/selected-input U and UDot checks and frozen sampled-field gates are unchanged.",
        "solver_first_cross_branch_bytes_equal": None
        if prepare_only
        else np.array_equal(solved_first["zero"].view("<u8"), solved_first["pulse"].view("<u8")),
        "solver_first_cross_branch_difference_by_field": []
        if prepare_only
        else _field_differences(solved_first["zero"], solved_first["pulse"], mapping),
        "native_state_sha256": source_hash,
        "native_derivative_sha256": derivative_hash,
        "native_dof_count": len(original),
        "native_dof_mapping_sha256": prepare["source_dof_mapping"]["sha256"],
        "source_solnum": source_index,
        "seed_solnum": source_index,
        "seed_frame_count": len(times),
        "branch_time_s": tb,
        "mesh_fingerprint": fingerprint,
        "native_state_differences": differences,
        "source_integrals": force_results,
        "readable_invariants_passed": True,
        "unreadable_property_observations": unavailable,
        "unreadable_property_observation_count": len(unavailable),
        "invariance_scope": "Readable snapshot properties and reported native mesh fingerprint compared; unreadable properties are not independently verified. Preparation may change tlist/Variables initialization and branch parameters; branches may add the audited force/parameters and the separately checked registered consistent=bweuler->off change. Solver runtime messages, Variables expanded clist and property-default bookkeeping are separately accounted for.",
        "native_difference_scope": "Source and selected full-history native seed U/UDot bytes are equal. The separate Variables probes are validation-only and are not claimed to be the hidden input to runAll. Actual first solved states are decoded and compared to source and each other without a new numerical threshold; frozen sampled-field gates control their physical differences. Raw native-DOF differences mix units.",
        "external_gates": [
            "source/checkpoint/output file hashes",
            "accepted internal time-step logs",
            "sampled-field restart and response gates",
        ],
    }
