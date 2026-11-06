"""Native byte/provenance and real-node source contracts, without COMSOL."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.branch_evidence import (
    validate_native_branch_evidence,
    validate_prepared_native_evidence,
)
from hydro_disturb_bench.comsol_java import bridge

PLAN = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/m3_branch_pulse_20261105/preregistered_plan.json"
)
PROTOCOL = PLAN.with_name("restart_protocol_v2.json")
FY = "rho_f*branch_A*exp(-((x-branch_xc)^2+(y-branch_yc)^2)/(2*branch_sigma^2))*if((t>branch_on)&&(t<branch_off),sin(pi*(t-branch_on)/(branch_off-branch_on))^2,0)"
FORCE = ["0[N/m^3]", FY, "0[N/m^3]"]
STATE = [0.01, 0.001, 0.02, 2500.0]


def _array(values):
    data = np.asarray(values, dtype="<f8")
    raw = data.tobytes()
    return {
        "dtype": "float64",
        "byte_order": "little",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode("ascii"),
    }


def _vector(tag="sol1", number=1, frames=1, values=STATE):
    return {
        **_array(values),
        "solution_tag": tag,
        "solnum": number,
        "time": 180.0,
        "count": len(values),
        "solution_size": [len(values), frames],
    }


def _derivative(tag="sol1", number=1, values=(1e-5, -2e-5, 3e-4, 0.0)):
    a = np.asarray(values)
    return {
        **_array(values),
        "available": True,
        "solution_tag": tag,
        "solnum": number,
        "time": 180.0,
        "count": len(values),
        "finite_count": int(np.isfinite(a).sum()),
        "nan_count": int(np.isnan(a).sum()),
        "positive_infinity_count": int(np.isposinf(a).sum()),
        "negative_infinity_count": int(np.isneginf(a).sum()),
    }


def _rehash_mapping(mapping):
    payload = mapping["payload"]
    text = (
        "{"
        + ",".join(
            json.dumps(k)
            + ":"
            + json.dumps(
                v,
                ensure_ascii=False,
                separators=((", ", ":") if isinstance(v, list) else (",", ":")),
            )
            for k, v in payload.items()
        )
        + "}"
    )
    mapping["sha256"] = hashlib.sha256(text.encode()).hexdigest()


def _mapping():
    coordinates = [[0.1, 0.1, 0.1, np.nan], [0.12, 0.12, 0.12, np.nan]]
    payload = {
        "n_dofs": 4,
        "field_names": ["comp1.u", "comp1.p"],
        "field_dof_counts": [3, 1],
        "dof_names": ["comp1.u", "comp1.v", "comp1.p", "comp1.dt2Inv_u"],
        "name_indices": [0, 1, 2, 3],
        "solution_vector_indices": [0, 1, 2, 3],
        "geometry_indices": [1, 1, 1, -1],
        "coordinates": {
            "shape": [2, 4],
            "dtype": "float64",
            "byte_order": "little",
            "order": "C",
            "non_finite_count": 2,
            **{k: v for k, v in _array(coordinates).items() if k in ("sha256", "base64")},
        },
    }
    result = {"hash_encoding": "UTF-8 exact JSON payload below", "payload": payload}
    _rehash_mapping(result)
    return result


def _feature(tag, kind, props=None, selection=None, children=None):
    result = {
        "tag": tag,
        "path": tag,
        "type": kind,
        "active": True,
        "properties": props or {},
        "unreadable_properties": {},
        "selection_entities": selection,
    }
    if children is not None:
        result["features"] = children
    return result


def _parameter(value, unit):
    return {"expr": str(value) + (f"[{unit}]" if unit else ""), "value": value, "unit": unit}


def _snapshot(plan, initialized=False, amplitude=None):
    parameters = {
        "rho_f": _parameter(plan["scales"]["rho_f_kg_m3"], "kg/m^3"),
        "U_inf": _parameter(plan["scales"]["U_inf_m_s"], "m/s"),
        "mu_f": _parameter(0.001004, "Pa*s"),
    }
    variables = {
        "control": "time",
        "initmethod": "init",
        "initsol": "zero",
        "initsoluse": "current",
        "solnum": "auto",
        "manualsolnum": [1.0],
        "cname": ["t", "timestep"],
        "clist": "0; 0.2",
        "solvertype": "none",
        "manualsol": "off",
        "t": 0.0,
    }
    if initialized:
        parameters.update(
            branch_tb=_parameter(180.0, "s"),
            branch_source_solnum=_parameter(901.0, None),
            branch_seed_solnum=_parameter(901.0, None),
        )
        variables.update(
            control="user",
            initmethod="sol",
            initsol="sol_branch_seed",
            solnum="manual",
            clist="180; 0.2",
            solvertype="solnum",
            manualsol="on",
            t=200.0,
            manualsolnum=[901.0],
        )
    physics_children = [_feature("init1", "init", {"u": [0.0, 0.0]})]
    if amplitude is not None:
        for key, value, unit in (
            ("A", amplitude, "m/s^2"),
            ("xc", 0.15, "m"),
            ("yc", 0.12, "m"),
            ("sigma", 0.01, "m"),
            ("on", 181.0, "s"),
            ("off", 183.0, "s"),
        ):
            parameters["branch_" + key] = _parameter(value, unit)
        physics_children.append(_feature("branch_vf", "VolumeForce", {"F": FORCE.copy()}, [1]))
    tlist = (
        np.linspace(180, 200, 101).tolist() if initialized else np.linspace(0, 200, 1001).tolist()
    )
    return {
        "parameters": parameters,
        "solver_features": [
            _feature("st1", "StudyStep", {"study": "std1", "studystep": "time"}),
            _feature("v1", "Variables", variables),
            _feature(
                "t1",
                "Time",
                {
                    "rtol": 1e-4,
                    "maxstepconstraintbdf": "const",
                    "maxstepbdf": 0.05,
                    "consistent": "bweuler",
                    "tlist": tlist,
                    "atol": 0.001,
                },
            ),
        ],
        "study_step": _feature("time", "Transient", {"tlist": tlist, "mesh": ["geom1", "mesh1"]}),
        "non_mesh": {
            "components": [
                {
                    "tag": "comp1",
                    "geometry": [
                        {
                            "tag": "geom1",
                            "features": [_feature("r1", "Rectangle", {"size": [0.4, 0.24]})],
                        }
                    ],
                    "physics": [
                        _feature(
                            "spf",
                            "LaminarFlow",
                            {"compressibility": "Incompressible"},
                            children=physics_children,
                        )
                    ],
                    "materials": [_feature("mat1", "Common", {"rho": "rho_f", "mu": "mu_f"})],
                }
            ]
        },
    }


def _force_evaluation(times, amplitude, plan, prefix):
    times = np.asarray(times)
    gate = np.where((times > 181) & (times < 183), np.sin(np.pi * (times - 181) / 2) ** 2, 0)
    fy = plan["scales"]["rho_f_kg_m3"] * amplitude * gate
    totals = (
        plan["source_integral_reference"]["peak_force_per_depth_N_m"] * (amplitude / 0.005) * gate
    )
    numbers = list(range(1, len(times) + 1))
    return {
        "expression_source_feature": "branch_vf",
        "dataset_tag": "dset1",
        "solution_tag": "sol1",
        "actual_times": times.tolist(),
        "point_evaluation": {
            "tag": prefix + "_eval",
            "type": "EvalPoint",
            "dataset_tag": prefix + "_point",
            "point_dataset_tag": prefix + "_point",
            "point_dataset_type": "CutPoint2D",
            "point_source_dataset": "dset1",
            "solnum": numbers,
            "solrepresentation": "solnum",
            "innerinput": "manual",
            "expressions": FORCE + ["t", "x", "y"],
            "units": ["N/m^3"] * 3 + ["s", "m", "m"],
            "values_by_expression": [
                np.zeros(len(times)).tolist(),
                fy.tolist(),
                np.zeros(len(times)).tolist(),
                times.tolist(),
                np.full(len(times), 0.15).tolist(),
                np.full(len(times), 0.12).tolist(),
            ],
        },
        "integral": {
            "tag": prefix + "_integral",
            "type": "IntSurface",
            "dataset_tag": "dset1",
            "selection": [1],
            "solnum": numbers,
            "solrepresentation": "solnum",
            "innerinput": "manual",
            "expressions": [FY],
            "units": ["N/m"],
            "Fy_per_depth": totals.tolist(),
        },
    }


@pytest.fixture
def native_case(tmp_path):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan["restart_protocol"] = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    before, initialized = _snapshot(plan), _snapshot(plan, initialized=True)
    mapping = _mapping()
    mesh = {
        "component_tag": "comp1",
        "geometry_tag": "geom1",
        "mesh_tag": "mesh1",
        "vertex_count": 17910,
        "element_count": 34752,
        "sha256": "a" * 64,
        "hash_input": "native vertices and connectivity",
    }
    provenance = (
        "source_solution=sol1;source_solnum=901;source_time_s=180.0;float64_le_sha256="
        + _vector()["sha256"]
        + ";udot_float64_le_sha256="
        + _derivative()["sha256"]
        + ";copy_method=copySolution_full_native_history"
    )
    prepare = {
        "status": "SUCCESS",
        "operation": "prepare_branch_state",
        "time_solver_executed": False,
        "source_model": str(Path(plan["source_model"]).resolve()),
        "output_model": str(tmp_path / "checkpoint.mph"),
        "source_solnum": 901,
        "seed_solnum": 901,
        "seed_frame_count": 1001,
        "seed_times": np.linspace(0, 200, 1001).tolist(),
        "native_copy_method": "copySolution_full_native_history",
        "seed_solution_tag": "sol_branch_seed",
        "dataset_tag": "dset1",
        "solution_tag": "sol1",
        "source_times": np.linspace(0, 200, 1001).tolist(),
        "seed_provenance": provenance,
        "native_vectors": {
            "source": _vector(number=901, frames=1001),
            "seed": _vector("sol_branch_seed", 901, 1001),
            "variables_probe": _vector(values=[0.01, 0.001, 0.02, 0.0]),
        },
        "native_derivatives": {
            "source": _derivative(number=901),
            "seed": _derivative("sol_branch_seed", 901),
            "variables_probe": _derivative(values=[0.0, 0.0, 0.0, 0.0]),
        },
        "source_dof_mapping": deepcopy(mapping),
        "initialized_dof_mapping": deepcopy(mapping),
        "mesh_before": deepcopy(mesh),
        "mesh_after": deepcopy(mesh),
        "before": before,
        "configured": deepcopy(initialized),
        "after": deepcopy(initialized),
    }
    branches = []
    for name, amplitude in (("zero", 0.0), ("pulse", 0.005)):
        configured = _snapshot(plan, initialized=True, amplitude=amplitude)
        configured["solver_features"][2]["properties"]["consistent"] = "off"
        branch = {
            "status": "SUCCESS",
            "restart_protocol_id": plan["restart_protocol"]["id"],
            "operation": "run_pulse_branch",
            "time_solver_executed": True,
            "source_model": prepare["output_model"],
            "output_model": str(tmp_path / (name + ".mph")),
            "amplitude_m_s2": amplitude,
            "dataset_tag": "dset1",
            "solution_tag": "sol1",
            "seed_provenance": provenance,
            "seed_solnum": 901,
            "seed_frame_count": 1001,
            "seed_times": np.linspace(0, 200, 1001).tolist(),
            "native_copy_method": "copySolution_full_native_history",
            "initialization_path": "native seed -> runAll(Compile, Variables, Time); prior Variables execution is a validation probe only",
            "native_input_role": "full native seed frame selected by live Variables initsol/manualsolnum; variables_probe is not claimed to be the internal Time input",
            "actual_times": np.linspace(180, 200, 101).tolist(),
            "native_vectors": {
                "seed": _vector("sol_branch_seed", 901, 1001),
                "input": _vector("sol_branch_seed", 901, 1001),
                "variables_probe": _vector(values=[0.01, 0.001, 0.02, 0.0]),
                "solver_first": _vector(frames=101, values=[0.01000001, 0.001, 0.02, 0.0]),
            },
            "native_derivatives": {
                "seed": _derivative("sol_branch_seed", 901),
                "input": _derivative("sol_branch_seed", 901),
                "variables_probe": _derivative(values=[0.0, 0.0, 0.0, 0.0]),
                "solver_first": _derivative(),
            },
            "source_dof_mapping": deepcopy(mapping),
            "initialized_dof_mapping": deepcopy(mapping),
            "mesh_before": deepcopy(mesh),
            "mesh_after": deepcopy(mesh),
            "before": deepcopy(initialized),
            "configured": configured,
            "after": deepcopy(configured),
            "force_feature": _feature("branch_vf", "VolumeForce", {"F": FORCE.copy()}, [1]),
            "force_initial_evaluation": _force_evaluation([180.0], amplitude, plan, "branch_pre"),
            "force_evaluation": _force_evaluation(
                np.linspace(180, 200, 101), amplitude, plan, "branch_post"
            ),
        }
        branches.append(branch)
    return prepare, *branches, plan


def test_native_byte_and_force_evidence_passes_without_requiring_first_solved_state_equality(
    native_case,
):
    result = validate_native_branch_evidence(*native_case)
    assert result["status"] == "PASS"
    assert result["native_dof_count"] == 4
    assert result["source_seed_vector_bytes_equal"] is True
    assert result["source_seed_derivative_bytes_equal"] is True
    assert result["variables_probe_cross_branch_bytes_equal"] is True
    assert result["variables_probe_sha256_by_branch"]["zero"] != result["native_state_sha256"]
    assert result["complete_branch_evidence"] is True
    assert result["restart_protocol_id"] == "native-restart-consistent-off-v2"
    assert result["seed_solnum"] == 901 and result["seed_frame_count"] == 1001
    assert (
        result["native_state_differences"]["zero"]["solver_first_raw_dof_max_absolute_difference"]
        == 2500
    )
    assert result["source_integrals"]["pulse"]["impulse_per_depth_N_s_m"] == pytest.approx(
        0.0031359350545522137
    )


@pytest.mark.parametrize(
    "fault",
    [
        "base64",
        "hash",
        "count",
        "endian",
        "nonfinite_state",
        "wrong_native_input",
        "wrong_initial_time",
        "mapping_hash",
        "mapping_order",
        "mesh",
        "solver",
        "material",
        "extra_parameter",
        "initialization",
        "wrong_force_unit",
        "wrong_integral_unit",
        "wrong_dataset",
        "wrong_point_dataset",
        "wrong_solnum",
        "wrong_force_expression",
        "inactive_force",
        "wrong_domain",
        "wrong_center_force",
        "wrong_force_time",
        "time_gap",
        "zero_force",
        "missing_input",
        "missing_initial_role",
        "udot_hash",
        "udot_changed",
        "udot_missing",
        "udot_count_lie",
        "single_frame_seed",
    ],
)
def test_native_provenance_faults_cannot_be_accepted(native_case, fault):
    prepare, zero, pulse, plan = native_case
    vector = pulse["native_vectors"]["input"]
    point = pulse["force_evaluation"]["point_evaluation"]
    if fault == "base64":
        vector["base64"] = "not valid base64!"
    elif fault == "hash":
        vector["sha256"] = "0" * 64
    elif fault == "count":
        vector["count"] += 1
    elif fault == "endian":
        vector["byte_order"] = "big"
    elif fault == "nonfinite_state":
        pulse["native_vectors"]["input"] = _vector(
            "sol_branch_seed", 901, 1001, values=[np.nan, 0.001, 0.02, 2500]
        )
    elif fault == "wrong_native_input":
        pulse["native_vectors"]["input"] = _vector(
            "sol_branch_seed", 901, 1001, values=[0.011, 0.001, 0.02, 2500]
        )
    elif fault == "wrong_initial_time":
        vector["time"] = 180.2
    elif fault == "mapping_hash":
        pulse["initialized_dof_mapping"]["sha256"] = "0" * 64
    elif fault == "mapping_order":
        mapping = pulse["initialized_dof_mapping"]
        mapping["payload"]["solution_vector_indices"] = [1, 0, 2, 3]
        _rehash_mapping(mapping)
    elif fault == "mesh":
        pulse["mesh_after"]["element_count"] += 1
    elif fault == "solver":
        pulse["after"]["solver_features"][2]["properties"]["maxstepbdf"] = 0.1
    elif fault == "material":
        pulse["after"]["non_mesh"]["components"][0]["materials"][0]["properties"]["rho"] = "2*rho_f"
    elif fault == "extra_parameter":
        pulse["after"]["parameters"]["branch_unregistered"] = _parameter(1, None)
    elif fault == "initialization":
        pulse["configured"]["solver_features"][1]["properties"]["initsol"] = "other_solution"
    elif fault == "wrong_force_unit":
        point["units"][1] = "m/s^2"
    elif fault == "wrong_integral_unit":
        pulse["force_evaluation"]["integral"]["units"] = ["N"]
    elif fault == "wrong_dataset":
        pulse["force_evaluation"]["integral"]["dataset_tag"] = "other_dataset"
    elif fault == "wrong_point_dataset":
        point["dataset_tag"] = "other_point"
    elif fault == "wrong_solnum":
        point["solnum"][0] = 2
    elif fault == "wrong_force_expression":
        pulse["force_feature"]["properties"]["F"][1] = "branch_A"
    elif fault == "inactive_force":
        pulse["force_feature"]["active"] = False
    elif fault == "wrong_domain":
        pulse["force_feature"]["selection_entities"] = [2]
    elif fault == "wrong_center_force":
        point["values_by_expression"][1][10] *= 2
    elif fault == "wrong_force_time":
        point["values_by_expression"][3][10] += 0.2
    elif fault == "time_gap":
        pulse["actual_times"][2] += 0.1
    elif fault == "zero_force":
        zero["force_evaluation"]["point_evaluation"]["values_by_expression"][1][10] = 0.01
    elif fault == "missing_input":
        pulse["native_vectors"].pop("input")
    elif fault == "missing_initial_role":
        pulse.pop("native_input_role")
    elif fault == "udot_hash":
        pulse["native_derivatives"]["input"]["sha256"] = "0" * 64
    elif fault == "udot_changed":
        pulse["native_derivatives"]["input"] = _derivative(
            "sol_branch_seed", 901, values=[0.0, 0.0, 0.0, 0.0]
        )
    elif fault == "udot_missing":
        pulse["native_derivatives"].pop("input")
    elif fault == "udot_count_lie":
        pulse["native_derivatives"]["input"]["finite_count"] = 3
    elif fault == "single_frame_seed":
        prepare["seed_solnum"] = 1
        prepare["seed_frame_count"] = 1
        prepare["seed_times"] = [180.0]
    with pytest.raises(bridge.ModelValidationError):
        validate_native_branch_evidence(*native_case)


def test_source_integral_exceeding_frozen_tolerance_returns_fail(native_case):
    integral = native_case[2]["force_evaluation"]["integral"]
    integral["Fy_per_depth"] = (np.asarray(integral["Fy_per_depth"]) * 1.02).tolist()
    result = validate_native_branch_evidence(*native_case)
    assert result["status"] == "FAIL"
    assert result["source_integrals"]["pulse"]["passed"] is False
    assert result["source_integrals"]["pulse"]["impulse_relative_error"] == pytest.approx(0.02)


def test_prepare_only_check_does_not_claim_completed_branch_evidence(native_case):
    original_plan = deepcopy(native_case[3])
    original_plan.pop("restart_protocol")
    result = validate_prepared_native_evidence(native_case[0], original_plan)
    assert result["status"] == "PASS"
    assert result["complete_branch_evidence"] is False
    assert result["source_integrals"] == {}
    assert result["variables_probe_cross_branch_bytes_equal"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "protocol_missing",
        "protocol_wrong_id",
        "protocol_extra_setting",
        "readback_missing",
        "readback_wrong_id",
        "pulse_not_changed",
        "zero_not_changed",
        "before_changed",
        "other_setting_drift",
    ],
)
def test_restart_protocol_allows_only_registered_change_in_both_branches(native_case, fault):
    prepare, zero, pulse, plan = native_case
    if fault == "protocol_missing":
        plan.pop("restart_protocol")
    elif fault == "protocol_wrong_id":
        plan["restart_protocol"]["id"] = "unregistered"
    elif fault == "protocol_extra_setting":
        plan["restart_protocol"]["solver_change"]["rtol"] = 0.001
    elif fault == "readback_missing":
        pulse.pop("restart_protocol_id")
    elif fault == "readback_wrong_id":
        zero["restart_protocol_id"] = "unregistered"
    elif fault in ("pulse_not_changed", "zero_not_changed"):
        response = pulse if fault.startswith("pulse") else zero
        for phase in ("configured", "after"):
            response[phase]["solver_features"][2]["properties"]["consistent"] = "bweuler"
    elif fault == "before_changed":
        zero["before"]["solver_features"][2]["properties"]["consistent"] = "off"
    elif fault == "other_setting_drift":
        for phase in ("configured", "after"):
            pulse[phase]["solver_features"][2]["properties"]["atol"] = 0.002
    with pytest.raises(bridge.ModelValidationError):
        validate_native_branch_evidence(*native_case)


@pytest.mark.parametrize("other_change", [False, True])
def test_only_consistent_default_bookkeeping_is_authorized(native_case, other_change):
    prepare, zero, pulse, plan = native_case
    for phase in ("before", "configured", "after"):
        prepare[phase]["solver_features"][2]["properties"]["propertyDefaults"] = [["atol", 0.001]]
        for branch in (zero, pulse):
            pairs = [["atol", 0.001]]
            if phase != "before":
                pairs.append(["consistent", "bweuler"])
            branch[phase]["solver_features"][2]["properties"]["propertyDefaults"] = pairs
    if other_change:
        pulse["after"]["solver_features"][2]["properties"]["propertyDefaults"].append(
            ["initialstepbdf", 0.1]
        )
        with pytest.raises(bridge.ModelValidationError):
            validate_native_branch_evidence(*native_case)
    else:
        assert validate_native_branch_evidence(*native_case)["status"] == "PASS"


def test_validation_only_probe_difference_is_diagnostic_not_a_hidden_time_input_claim(native_case):
    native_case[2]["native_vectors"]["variables_probe"] = _vector(
        values=[0.010000001, 0.001, 0.02, 0.0]
    )
    result = validate_native_branch_evidence(*native_case)
    assert result["status"] == "PASS"
    assert result["variables_probe_cross_branch_bytes_equal"] is False
    assert result["variables_probe_cross_branch_difference_by_field"][0][
        "max_absolute_difference"
    ] == pytest.approx(1e-9)
    assert result["source_seed_vector_bytes_equal"] is True
    assert result["source_seed_derivative_bytes_equal"] is True


def test_first_solved_state_difference_is_reported_without_inventing_new_gate(native_case):
    native_case[2]["native_vectors"]["solver_first"] = _vector(
        frames=101, values=[0.01000002, 0.001, 0.02, 0.0]
    )
    result = validate_native_branch_evidence(*native_case)
    assert result["status"] == "PASS"
    assert not result["solver_first_cross_branch_bytes_equal"]
    assert result["solver_first_cross_branch_difference_by_field"][0][
        "max_absolute_difference"
    ] == pytest.approx(1e-8)
