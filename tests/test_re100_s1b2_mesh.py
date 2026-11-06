"""Offline admission tests for the single M0/M1 native-mesh experiment.

Synthetic readbacks exercise production gates; they are not COMSOL integration
evidence. Wake tests use actual exported-text parsing and analytical signals.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
for location in (ROOT_DIR, ROOT_DIR / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from hydro_disturb_bench.comsol_java import bridge as comsol_bridge
from scripts import run_re100_s1b2_mesh as mesh_run


def _time_policy():
    return {
        "rtol": "1e-4",
        "maxstepconstraintbdf": "const",
        "maxstepbdf": "0.05",
        "atolmethod": "scaled",
        "atolfactor": "0.1",
        "tlist": "range(0, 0.2, 200)",
        "timemethod": "bdf",
    }


@pytest.mark.parametrize(
    "key,value",
    [("rtol", "1e-3"), ("maxstepconstraintbdf", "auto"), ("maxstepbdf", "0.025")],
    ids=["changed_tolerance", "automatic_step_cap", "case_d_time_policy"],
)
def test_mesh_experiment_rejects_time_policy_other_than_case_c(key, value):
    settings = _time_policy()
    settings[key] = value
    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.check_time_policy(settings)


@pytest.fixture
def invariant_readbacks():
    before = {
        "solver_details": _time_policy(),
        "physics_details": {"shape_order": "1", "initial_conditions": {"u": "0"}},
        "mesh_details": {
            "sdim": 2, "is_complete": True,
            "features": ["size", "size1", "cr1", "ftri1", "bl1"],
            "num_elem": 100, "num_vertex": 70,
        },
    }
    after = deepcopy(before)
    after["mesh_details"].update(num_elem=196, num_vertex=120)
    tree = {
        "parameters": {"U_inf": "0.01[m/s]", "rho": "1000[kg/m^3]"},
        "studies": [{"tag": "transient_only", "features": [{"type": "Transient"}]}],
        "components": [{"tag": "comp1", "meshes": [{"tag": "mesh1", "num_elem": 100}]}],
    }
    new_tree = deepcopy(tree)
    new_tree["components"][0]["meshes"][0]["num_elem"] = 196
    return before, after, tree, new_tree


def test_invariants_allow_changed_mesh_counts_with_same_time_and_physics(invariant_readbacks):
    assert mesh_run.check_invariants(*invariant_readbacks)["status"] == "PASS"


@pytest.mark.parametrize("fault", ["absolute_tolerance", "output_schedule", "element_order", "initial_condition"])
def test_mesh_study_rejects_non_target_solver_and_physics_changes(invariant_readbacks, fault):
    after = invariant_readbacks[1]
    if fault == "absolute_tolerance":
        after["solver_details"]["atolfactor"] = "1"
    elif fault == "output_schedule":
        after["solver_details"]["tlist"] = "range(0, 0.1, 200)"
    elif fault == "element_order":
        after["physics_details"]["shape_order"] = "2"
    else:
        after["physics_details"]["initial_conditions"]["u"] = "U_inf"

    with pytest.raises(RuntimeError, match="Non-target"):
        mesh_run.check_invariants(*invariant_readbacks)


def _native_feature(tag, kind, properties, *, path=None, children=None, entities=None):
    return {
        "tag": tag, "path": path or tag, "type": kind, "active": True,
        "properties": properties, "unreadable_properties": {},
        "selection_entities": entities, "features": children or [],
    }


def _square_mesh(divisions):
    """Small synthetic native mesh: values are test data, not CFD readback claims."""
    coords = [(i / divisions, j / divisions) for j in range(divisions + 1) for i in range(divisions + 1)]
    triangles = []
    for j in range(divisions):
        for i in range(divisions):
            a = j * (divisions + 1) + i
            b, c, d = a + 1, a + divisions + 1, a + divisions + 2
            triangles.extend(((a, b, d), (a, d, c)))
    return {
        "tag": "mesh1", "geometry_tag": "geom1", "length_unit": "m", "sdim": 2,
        "automatic": False, "is_complete": True,
        "num_elem": len(triangles), "num_vertex": len(coords),
        "quality": {"measure": "volcircum", "minimum": 0.7, "mean": 0.8},
        "area": {"minimum": 0.5 / divisions**2, "maximum": 0.5 / divisions**2, "total": 1.0},
        "vertices": np.array(coords).T.tolist(),
        "elements": {"tri": {"count": len(triangles), "connectivity": np.array(triangles).T.tolist(), "entities": [1] * len(triangles)}},
        "features": [
            _native_feature("size", "MeshSizeDefault", {"custom": "off", "hmax": 0.0084, "hmin": 0.00024, "hgrad": 1.13}),
            _native_feature("size1", "Size", {"custom": "on", "hmax": 0.0004, "hmin": 0.00005, "hgrad": 1.08}, entities=[5, 6, 7, 8]),
            _native_feature("bl1", "BndLayer", {}, children=[
                _native_feature("blp1", "BndLayerProp", {"inittype": "blhminfact", "blhminfact": 5.0, "blnlayers": 2.0, "blstretch": 1.2}, path="bl1/blp1", entities=[5, 6, 7, 8]),
            ]),
        ],
    }


@pytest.fixture
def native_readbacks():
    non_mesh = {"components": [{
        "tag": "comp1",
        "geometry": [{"tag": "geom1", "features": [_native_feature("c1", "Circle", {"r": 0.005})]}],
        "physics": [_native_feature("spf", "LaminarFlow", {}, children=[
            _native_feature("fp1", "FluidProperties", {"streamlinens": "0", "crosswindns": "0", "rho": "rho_f"}, path="spf/fp1"),
            _native_feature("init1", "init", {"u_init": ["0", "0", "0"], "p_init": "0"}, path="spf/init1"),
            _native_feature("wallbc1", "WallBC", {"BoundaryCondition": "NoSlip"}, path="spf/wallbc1", entities=[5, 6, 7, 8]),
        ])],
        "materials": [],
    }]}
    before = {
        "status": "SUCCESS", "operation": "inspect_native_mesh", "sha256": "A" * 64,
        "components": [{"tag": "comp1", "meshes": [_square_mesh(1)]}],
        "non_mesh": non_mesh,
    }
    after = deepcopy(before)
    after["sha256"] = "B" * 64
    after["components"][0]["meshes"][0] = _square_mesh(2)
    plan = {
        "source_sha256": before["sha256"], "component_tag": "comp1", "mesh_tag": "mesh1",
        "refinement_factor": 1.4, "changes": [],
    }
    for index, feature in enumerate(before["components"][0]["meshes"][0]["features"][:2]):
        for prop in ("hmax", "hmin"):
            old = feature["properties"][prop]
            new = old / 1.4
            plan["changes"].append({"feature_path": [feature["tag"]], "property": prop, "before": old, "after": new})
            after["components"][0]["meshes"][0]["features"][index]["properties"][prop] = new
    plan["changes"].append({"feature_path": ["size"], "property": "custom", "before": "off", "after": "on"})
    after["components"][0]["meshes"][0]["features"][0]["properties"]["custom"] = "on"
    return before, after, plan


def test_native_refinement_requires_real_rebuilt_mesh_and_actual_scale_readbacks(native_readbacks):
    result = mesh_run.check_mesh_refinement(*native_readbacks)
    assert result["status"] == "PASS"


@pytest.mark.parametrize("fault", ["sampling_only", "wrong_size_readback", "missing_quality", "stale_source_hash", "wrong_refinement_factor", "changed_boundary_layer"])
def test_native_mesh_gate_rejects_missing_or_inconsistent_refinement_evidence(native_readbacks, fault):
    before, after, plan = native_readbacks
    mesh_before, mesh_after = (item["components"][0]["meshes"][0] for item in (before, after))
    if fault == "sampling_only":
        for key in ("num_elem", "num_vertex", "vertices", "elements", "area"):
            mesh_after[key] = deepcopy(mesh_before[key])
    elif fault == "wrong_size_readback":
        mesh_after["features"][1]["properties"]["hmin"] = mesh_before["features"][1]["properties"]["hmin"]
    elif fault == "missing_quality":
        mesh_after["quality"] = None
    elif fault == "stale_source_hash":
        plan["source_sha256"] = "C" * 64
    elif fault == "wrong_refinement_factor":
        plan["refinement_factor"] = 2.0
    else:
        mesh_after["features"][2]["features"][0]["properties"]["blstretch"] = 1.4

    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.check_mesh_refinement(before, after, plan)


@pytest.mark.parametrize("fault", ["geometry", "stabilization", "boundary_condition", "initial_condition"])
def test_native_mesh_gate_rejects_nonmesh_physics_and_geometry_changes(native_readbacks, fault):
    component = native_readbacks[1]["non_mesh"]["components"][0]
    physics = component["physics"][0]["features"]
    if fault == "geometry":
        component["geometry"][0]["features"][0]["properties"]["r"] = 0.006
    elif fault == "stabilization":
        physics[0]["properties"]["streamlinens"] = "1"
    elif fault == "boundary_condition":
        physics[2]["properties"]["BoundaryCondition"] = "Slip"
    else:
        physics[1]["properties"]["u_init"] = ["U_inf", "0", "0"]

    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.check_mesh_refinement(*native_readbacks)


@pytest.mark.parametrize("fault", [None, "source_changed", "retained_solution", "missing_clear_proof"], ids=["fresh_mesh", "changed_source", "stale_cfd_solution", "missing_empty_readback"])
def test_native_bridge_publishes_only_new_mesh_with_unchanged_source_and_cleared_solution(tmp_path, monkeypatch, native_readbacks, fault):
    source, output = tmp_path / "m0.mph", tmp_path / "m1.mph"
    source.write_bytes(b"M0 solved model")
    plan = deepcopy(native_readbacks[2])
    plan["source_sha256"] = comsol_bridge.compute_file_hash(source)

    def execute(args, root=None):
        assert args[0] == "refine-native-mesh"
        assert Path(args[1]) == source
        Path(args[2]).write_bytes(b"new native M1 mesh")
        if fault == "source_changed":
            source.write_bytes(b"unexpected source mutation")
        result = {"status": "SUCCESS"}
        if fault != "missing_clear_proof":
            result["stored_solution_data_empty"] = fault != "retained_solution"
        return result

    external = Mock(side_effect=execute)
    monkeypatch.setattr(comsol_bridge, "_run_bridge", external)
    if fault:
        with pytest.raises(comsol_bridge.ModelValidationError):
            comsol_bridge.refine_native_mesh(source, output, plan)
        assert not output.exists()
    else:
        result = comsol_bridge.refine_native_mesh(source, output, plan)
        assert source.read_bytes() == b"M0 solved model"
        assert output.read_bytes() == b"new native M1 mesh"
        assert result["stored_solution_data_empty"] is True
        assert result["source_sha256"] == plan["source_sha256"]
    external.assert_called_once()


def test_native_bridge_rejects_source_hash_mismatch_before_external_call(tmp_path, monkeypatch, native_readbacks):
    source, output = tmp_path / "m0.mph", tmp_path / "m1.mph"
    source.write_bytes(b"M0 solved model")
    external = Mock()
    monkeypatch.setattr(comsol_bridge, "_run_bridge", external)

    with pytest.raises(comsol_bridge.ModelValidationError, match="SHA-256"):
        comsol_bridge.refine_native_mesh(source, output, native_readbacks[2])

    external.assert_not_called()
    assert not output.exists()


def _add_cylinder_wall_quads(mesh, segments):
    """Add explicit wall edges and adjacent quads to exercise native length measurements."""
    vertices = np.asarray(mesh["vertices"]).T.tolist()
    offset = len(vertices)
    angles = 2 * np.pi * np.arange(segments) / segments
    for radius in (0.005, 0.006):
        vertices.extend(np.column_stack((0.1 + radius * np.cos(angles), 0.12 + radius * np.sin(angles))).tolist())
    edges, quads = [], []
    for i in range(segments):
        j = (i + 1) % segments
        edges.append([offset + i, offset + j])
        # Deliberately non-cyclic node order exercises geometry-based ordering.
        quads.append([offset + i, offset + j + segments, offset + j, offset + i + segments])
    mesh["vertices"] = np.asarray(vertices).T.tolist()
    mesh["num_vertex"] = len(vertices)
    mesh["num_elem"] += segments
    for tag, cells, entity in (("edg", edges, 5), ("quad", quads, 1)):
        mesh["elements"][tag] = {"count": segments, "connectivity": np.asarray(cells).T.tolist(), "entities": [entity] * segments}


def _write_case_diagnostics(directory):
    time = np.linspace(0.0, 200.0, 1001)
    phase = 2 * np.pi * 0.16 * time
    _write_wake(directory, time, 0.005 + 0.002 * np.sin(phase), 0.001 * np.sin(phase))
    for filename, values in (("lift_coeff.txt", 0.3 * np.sin(phase)), ("drag_coeff.txt", 1.346 + 0.002 * np.cos(2 * phase))):
        np.savetxt(directory / filename, np.column_stack((time, values)), header="Time (s) coefficient (1)", comments="% ")


@pytest.fixture
def offline_mesh_run(tmp_path, monkeypatch, native_readbacks, invariant_readbacks):
    source_dir = tmp_path / "accepted_case_c"
    source_dir.mkdir()
    source = source_dir / "case_c.mph"
    source.write_bytes(b"accepted Case C source")
    historical = tmp_path / "case_b.mph"
    historical.write_bytes(b"preserved Case B")
    _write_case_diagnostics(source_dir)
    source_hash = mesh_run.compute_file_hash(source)
    (source_dir / "solve_response.json").write_text(json.dumps({
        "output_sha256": source_hash,
        "solution_stats": {"num_dofs": 100, "num_timesteps": 1001, "time_min": 0.0, "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True},
    }), encoding="utf-8")
    native_before, native_after, plan = native_readbacks
    native_before["sha256"] = plan["source_sha256"] = source_hash
    before, after, tree, new_tree = invariant_readbacks
    for index, native in enumerate((native_before, native_after)):
        mesh = native["components"][0]["meshes"][0]
        _add_cylinder_wall_quads(mesh, 4 * (index + 1))
        (before, after)[index]["mesh_details"].update(num_elem=mesh["num_elem"], num_vertex=mesh["num_vertex"])
    for target in (tree, new_tree):
        target["parameters"] = {key: {"value": value} for key, value in {"U_inf": 0.01, "D": 0.01, "R": 0.005, "x_c": 0.1, "y_c": 0.12}.items()}
        target["components"][0]["physics"] = [{"tag": "spf", "features": [{"tag": "wallbc1", "type": "WallBC", "selection_entities": [5, 6, 7, 8]}]}]
    state = {
        "source": source, "historical": historical, "output": tmp_path / "new_m1",
        "native_before": native_before, "native_after": native_after, "plan": plan,
    }
    monkeypatch.setattr(mesh_run, "M0_DIR", source_dir)
    monkeypatch.setattr(mesh_run, "M0_MODEL", source)
    monkeypatch.setattr(mesh_run, "PROTECTED_PATHS", (source_dir, historical))

    def inspect_native(model_path):
        response = deepcopy(native_before if model_path == source else native_after)
        response["sha256"] = mesh_run.compute_file_hash(model_path)
        return response

    def refine(model_path, output_path, plan, allow_overwrite=False):
        assert model_path == source
        assert allow_overwrite is False
        output_path.write_bytes(b"M1 refined mesh; no stored solution")
        state["prepared"] = output_path
        return {
            "status": "SUCCESS", "before": deepcopy(native_before), "after": inspect_native(output_path),
            "stored_solution_data_empty": True, "cleared_solution_tags": ["sol1"],
            "source_sha256": source_hash, "output_sha256": mesh_run.compute_file_hash(output_path),
            "refinement_plan": deepcopy(plan),
        }

    def solve(**kwargs):
        kwargs["output_path"].write_bytes(b"M1 new solution")
        return {
            "status": "SUCCESS", "source_sha256": mesh_run.compute_file_hash(kwargs["model_path"]),
            "output_sha256": mesh_run.compute_file_hash(kwargs["output_path"]),
            "solution_stats": {"num_dofs": 200, "num_timesteps": 1001, "time_min": 0.0, "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True},
            "log": "\n".join(f"{i} {0.05 * i:.8f} 0.05 2 1 2 2 0 0" for i in range(1, 4001)),
        }

    def export(**kwargs):
        _write_case_diagnostics(kwargs["output_dir"])
        if state.get("drag_scale"):
            drag_path = kwargs["output_dir"] / "drag_coeff.txt"
            values = np.loadtxt(drag_path, comments="%")
            values[:, 1] *= state["drag_scale"]
            np.savetxt(drag_path, values, header="Time (s) coefficient (1)", comments="% ")
        return {"status": "SUCCESS"}

    callbacks = {
        "inspect_native_mesh": inspect_native,
        "refine_native_mesh": refine,
        "inspect_model": lambda model_path: deepcopy(tree if model_path == source else new_tree),
        "run_prechecks": lambda **kwargs: {"overall_prechecks_status": "PASS", "precheck_b_solver_model": deepcopy(before if kwargs["model_path"] == source else after)},
        "run_study": solve,
        "export_s1_diagnostics": export,
    }
    for name, callback in callbacks.items():
        state[name] = Mock(side_effect=callback)
        monkeypatch.setattr(mesh_run, name, state[name])
    return state


@pytest.mark.parametrize("drag_scale,expected_status", [(1.0, "PASS"), (1.02, "SENSITIVE")])
def test_entry_solves_only_isolated_refined_m1_and_keeps_comparison_preliminary(offline_mesh_run, drag_scale, expected_status):
    state = offline_mesh_run
    state["drag_scale"] = drag_scale

    report = mesh_run.run_mesh_m1(state["output"], state["plan"])

    state["refine_native_mesh"].assert_called_once_with(
        model_path=state["source"], output_path=state["prepared"], plan=state["plan"], allow_overwrite=False,
    )
    state["run_study"].assert_called_once_with(
        model_path=state["prepared"], output_path=state["output"] / "re100_s1b2_m1_solved.mph",
        study_tag="transient_only", allow_overwrite=False,
    )
    assert state["prepared"].parent == state["output"]
    assert state["prepared"] != state["source"]
    assert report["status"] == expected_status
    assert report["physical_comparison"]["acceptance_status"] == expected_status
    assert report["mesh_independence_established"] is False
    assert report["physical_comparison"]["grid_independence_phase_can_close"] is False
    assert report["protected_files_unchanged"] is True
    assert state["source"].read_bytes() == b"accepted Case C source"
    assert state["historical"].read_bytes() == b"preserved Case B"
    assert report["m0_mesh_summary"]["cylinder_boundary"]["edge_count"] == 4
    assert report["m1_mesh_summary"]["cylinder_boundary"]["edge_count"] == 8
    assert report["wake_comparison"]["normalization"] == "U_inf"
    assert json.loads((state["output"] / "mesh_report.json").read_text(encoding="utf-8")) == report


def test_entry_refuses_existing_output_before_external_operations(offline_mesh_run):
    state = offline_mesh_run
    state["output"].mkdir()
    marker = state["output"] / "prior_report.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError):
        mesh_run.run_mesh_m1(state["output"], state["plan"])

    state["inspect_model"].assert_not_called()
    state["refine_native_mesh"].assert_not_called()
    state["run_study"].assert_not_called()
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_entry_does_not_solve_when_native_mesh_did_not_refine(offline_mesh_run):
    state = offline_mesh_run
    state["native_after"]["components"] = deepcopy(state["native_before"]["components"])

    with pytest.raises(RuntimeError, match="counts must increase"):
        mesh_run.run_mesh_m1(state["output"], state["plan"])

    state["refine_native_mesh"].assert_called_once()
    state["run_study"].assert_not_called()
    saved = json.loads((state["output"] / "mesh_report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "FAIL"
    assert saved["protected_files_unchanged"] is True


@pytest.fixture
def completed_solve_with_failed_export(offline_mesh_run):
    """Exercise the real failed-run archive path after the M1 solution exists."""
    state = offline_mesh_run
    export_callback = state["export_s1_diagnostics"].side_effect
    state["export_s1_diagnostics"].side_effect = RuntimeError("OpenGL image export unavailable")
    with pytest.raises(RuntimeError, match="OpenGL"):
        mesh_run.run_mesh_m1(state["output"], state["plan"])
    state["export_s1_diagnostics"].side_effect = export_callback
    for name in ("inspect_model", "inspect_native_mesh", "refine_native_mesh", "run_study", "export_s1_diagnostics", "run_prechecks"):
        state[name].reset_mock()
    state["solved"] = state["output"] / "re100_s1b2_m1_solved.mph"
    state["solved_bytes"] = state["solved"].read_bytes()
    state["failure_bytes"] = (state["output"] / "failure.log").read_bytes()
    return state


def test_postprocess_recovery_uses_saved_solution_without_refining_or_solving(completed_solve_with_failed_export):
    state = completed_solve_with_failed_export

    report = mesh_run.verify_saved_mesh_m1(state["output"])

    state["refine_native_mesh"].assert_not_called()
    state["run_study"].assert_not_called()
    state["export_s1_diagnostics"].assert_called_once()
    assert state["export_s1_diagnostics"].call_args.kwargs["model_path"] == state["solved"]
    assert state["solved"].read_bytes() == state["solved_bytes"]
    assert report["status"] == "PASS"
    assert report["mesh_independence_established"] is False
    assert report["protected_files_unchanged"] is True


def test_postprocess_recovery_rejects_tampered_solution_before_export(completed_solve_with_failed_export):
    state = completed_solve_with_failed_export
    state["solved"].write_bytes(b"different model after failed export")

    with pytest.raises(RuntimeError):
        mesh_run.verify_saved_mesh_m1(state["output"])

    state["export_s1_diagnostics"].assert_not_called()
    state["refine_native_mesh"].assert_not_called()
    state["run_study"].assert_not_called()


def test_postprocess_recovery_preserves_failure_and_does_not_invent_missing_wall_clock(completed_solve_with_failed_export):
    state = completed_solve_with_failed_export
    report_path = state["output"] / "mesh_report.json"
    legacy_report = json.loads(report_path.read_text(encoding="utf-8"))
    legacy_report.pop("solve_duration_sec", None)
    report_path.write_text(json.dumps(legacy_report, indent=2), encoding="utf-8")
    original_report_bytes = report_path.read_bytes()
    response_path = state["output"] / "solve_response.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["elapsed_ms"] = 123456
    response_path.write_text(json.dumps(response), encoding="utf-8")

    mesh_run.verify_saved_mesh_m1(state["output"])

    metadata = json.loads((state["output"] / "solver_metadata.json").read_text(encoding="utf-8"))
    assert metadata["solve_duration_sec"] == "UNOBTAINED"
    assert metadata["comsol_elapsed_ms"] == 123456
    assert any(path.read_bytes() == original_report_bytes for path in state["output"].glob("mesh_report_initial_export_failure*.json"))
    assert any(path.read_bytes() == state["failure_bytes"] for path in state["output"].glob("failure_initial_export_failure*.log"))
    state["refine_native_mesh"].assert_not_called()
    state["run_study"].assert_not_called()


def _stable_stats(name):
    return {
        "name": name,
        "status": "STABLE",
        "is_stable": True,
        "num_cycles": 5,
        "window": [170.0, 200.0],
        "St_zc_mean": 0.16,
        "St_peaks_mean": 0.16,
        "Cd_mean": 1.346,
        "Cl_amp_mean": 0.3,
        "Cl_demeaned_rms": 0.21,
        "step_info": {"step_count": 4091},
        "solve_wall_clock_sec": 10.0,
    }


def test_two_mesh_agreement_is_only_preliminary_not_mesh_independence():
    result = mesh_run.classify_comparison(_stable_stats("M0"), _stable_stats("M1"))

    assert result["acceptance_status"] == "PASS"
    assert result["mesh_independence_established"] is False
    assert set(result["thresholds_pct"].values()) == {1.0}


@pytest.mark.parametrize("metric", ["St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"])
def test_each_primary_metric_retains_one_percent_sensitivity_gate(metric):
    coarse, fine = _stable_stats("M0"), _stable_stats("M1")
    fine[metric] *= 1.02

    result = mesh_run.classify_comparison(coarse, fine)

    assert result["acceptance_status"] == "SENSITIVE"
    assert result["mesh_independence_established"] is False


@pytest.mark.parametrize("case_index", [0, 1], ids=["M0", "M1"])
@pytest.mark.parametrize("fault", ["unstable", "four_cycles"])
def test_both_meshes_require_five_stable_complete_cycles(case_index, fault):
    cases = [_stable_stats("M0"), _stable_stats("M1")]
    if fault == "unstable":
        cases[case_index]["is_stable"] = False
        cases[case_index]["stability_message"] = "Amplitude is drifting"
        expected = "UNSTABLE"
    else:
        cases[case_index]["num_cycles"] = 4
        expected = "INSUFFICIENT_CYCLES"

    result = mesh_run.classify_comparison(*cases)

    assert result["can_compare"] is False
    assert result["acceptance_status"] == expected
    assert result["mesh_independence_established"] is False


def _write_wake(directory, times, u, v):
    directory.mkdir(parents=True, exist_ok=True)
    for channel, values in (("u", u), ("v", v)):
        np.savetxt(
            directory / f"wake_{channel}.txt",
            np.column_stack((times, values)),
            header=f"Time (s) {channel} (m/s)",
            comments="% ",
            fmt="%.17g",
        )


def test_wake_constant_mean_and_demeaned_rms_on_non_grid_window(tmp_path):
    time = np.arange(0.0, 12.1, 0.2)
    _write_wake(tmp_path, time, np.full_like(time, 0.003), np.full_like(time, -1e-14))

    result = mesh_run.analyze_wake(tmp_path, [0.137, 10.137], 0.01)

    assert result["window"] == [0.137, 10.137]
    for channel, mean in (("u", 0.003), ("v", -1e-14)):
        observed = result["channels"][channel]
        assert observed["mean_m_s"] == pytest.approx(mean, abs=1e-17)
        assert observed["demeaned_rms_m_s"] == pytest.approx(0.0, abs=1e-17)
        assert observed["mean_over_u_inf"] == pytest.approx(mean / 0.01, abs=1e-15)


def test_wake_offset_sine_uses_exact_five_cycle_window_and_removes_mean(tmp_path):
    time = np.linspace(0.0, 12.0, 6001)
    u = 0.003 + 0.002 * np.sin(np.pi * time)
    v = -1e-14 + 0.0015 * np.cos(np.pi * time)
    _write_wake(tmp_path, time, u, v)

    result = mesh_run.analyze_wake(tmp_path, [0.137, 10.137], 0.01)

    for channel, mean, amplitude in (("u", 0.003, 0.002), ("v", -1e-14, 0.0015)):
        observed = result["channels"][channel]
        assert observed["mean_m_s"] == pytest.approx(mean, abs=1e-14)
        assert observed["demeaned_rms_m_s"] == pytest.approx(amplitude / np.sqrt(2), rel=2e-6)
        assert observed["rms_over_u_inf"] == pytest.approx(amplitude / np.sqrt(2) / 0.01, rel=2e-6)


@pytest.mark.parametrize("fault", ["uncovered_window", "zero_u_inf", "nonfinite_sample", "time_reversal"])
def test_wake_statistics_reject_invalid_normalization_or_series(tmp_path, fault):
    time = np.linspace(0.0, 12.0, 61)
    values = np.full_like(time, 0.003)
    if fault == "nonfinite_sample":
        values[20] = np.nan
    if fault == "time_reversal":
        time[20] = time[19] - 0.1
    _write_wake(tmp_path, time, values, values)
    window = [-0.1, 10.137] if fault == "uncovered_window" else [0.137, 10.137]
    u_inf = 0.0 if fault == "zero_u_inf" else 0.01

    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.analyze_wake(tmp_path, window, u_inf)


def test_near_zero_wake_mean_difference_uses_u_inf_not_local_mean(tmp_path):
    time = np.linspace(0.0, 12.0, 61)
    m0, m1 = tmp_path / "m0", tmp_path / "m1"
    _write_wake(m0, time, np.full_like(time, 1e-16), np.zeros_like(time))
    _write_wake(m1, time, np.full_like(time, 0.0001), np.full_like(time, -0.0002))
    coarse = mesh_run.analyze_wake(m0, [0.137, 10.137], 0.01)
    fine = mesh_run.analyze_wake(m1, [0.137, 10.137], 0.01)

    result = mesh_run.compare_wake_statistics(coarse, fine)

    assert result["normalization"] == "U_inf"
    assert result["diagnostic_only"] is True
    assert result["mesh_independence_established"] is False
    assert result["channels"]["u"]["delta_mean_over_u_inf"] == pytest.approx(0.01)
    assert result["channels"]["v"]["delta_mean_over_u_inf"] == pytest.approx(-0.02)
    for channel in ("u", "v"):
        assert result["channels"][channel]["delta_rms_over_u_inf"] == pytest.approx(0, abs=1e-15)


def _m2_plan_for_source(source_sha256: str) -> dict:
    plan = json.loads(mesh_run.M2_PLAN.read_text(encoding="utf-8"))
    plan["source_sha256"] = source_sha256
    return plan


def test_m2_config_binds_to_m0_at_h0_over_196_and_checks_m1_count_ordering(native_readbacks):
    before_m0, after_m1, _ = native_readbacks
    plan_m2 = _m2_plan_for_source(before_m0["sha256"])
    after_m2 = deepcopy(before_m0)
    after_m2["sha256"] = "D" * 64
    after_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = after_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    result = mesh_run.check_mesh_refinement(before_m0, after_m2, plan_m2, prior_refined=after_m1)

    assert result["status"] == "PASS"
    assert result["mesh_level"] == "M2"
    assert result["m2"]["num_elem"] > result["m1"]["num_elem"] > result["m0"]["num_elem"]
    assert result["m1_to_m2_element_count_ratio"] == pytest.approx(18 / 8)


@pytest.mark.parametrize(
    "fault",
    [
        "double_applied_from_m1_sizes",
        "source_case_is_m1",
        "source_model_is_already_m1",
        "mismatched_level_and_factor",
        "m2_not_finer_than_m1",
    ],
)
def test_m2_validation_rejects_double_refinement_and_non_monotonic_counts(native_readbacks, fault):
    before_m0, after_m1, _ = native_readbacks
    plan_m2 = _m2_plan_for_source(before_m0["sha256"])
    after_m2 = deepcopy(before_m0)
    after_m2["sha256"] = "D" * 64
    after_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = after_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    if fault == "double_applied_from_m1_sizes":
        # Attempting to divide M1's hmax (0.006) by 1.96 instead of M0's hmax (0.0084) by 1.96
        plan_m2["changes"][1]["after"] = (0.0084 / 1.4) / 1.96
        mesh_m2["features"][0]["properties"]["hmax"] = plan_m2["changes"][1]["after"]
    elif fault == "source_case_is_m1":
        plan_m2["source_case"] = "S1-B2 M1"
    elif fault == "source_model_is_already_m1":
        before_m0 = after_m1
        plan_m2["source_sha256"] = after_m1["sha256"]
    elif fault == "mismatched_level_and_factor":
        plan_m2["refinement_factor"] = 1.4
    elif fault == "m2_not_finer_than_m1":
        after_m2["components"][0]["meshes"][0] = deepcopy(after_m1["components"][0]["meshes"][0])
        for change in plan_m2["changes"]:
            if change["property"] in {"hmax", "hmin"}:
                tag = change["feature_path"][0]
                feature = next(item for item in after_m2["components"][0]["meshes"][0]["features"] if item["tag"] == tag)
                feature["properties"][change["property"]] = change["after"]

    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.check_mesh_refinement(before_m0, after_m2, plan_m2, prior_refined=after_m1)


@pytest.mark.parametrize("metric", ["St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"])
def test_three_mesh_comparison_returns_sensitive_when_any_m1_m2_metric_exceeds_one_percent(metric):
    m0, m1, m2 = _stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2")
    m1[metric] *= 1.03
    m2[metric] = m1[metric] * 1.012  # +1.2% relative to M1 (> 1% threshold)

    result = mesh_run.classify_three_mesh_comparison(m0, m1, m2)

    assert result["primary_pair"] == "M1_vs_M2"
    assert result["acceptance_status"] == "SENSITIVE"
    assert result["adjacent_m1_m2_within_1pct"] is False
    assert result["grid_independence_phase_can_close"] is False
    assert result["mesh_independence_established"] is False


def test_three_mesh_comparison_passes_adjacent_gate_when_m1_m2_within_one_percent_and_deltas_reduce():
    m0, m1, m2 = _stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2")
    # Simulate M0 -> M1 lift change > 1% (+2.7%) followed by M1 -> M2 change < 1% (+0.4%)
    m1["St_zc_mean"] = m0["St_zc_mean"] * 1.009
    m2["St_zc_mean"] = m1["St_zc_mean"] * 1.002
    m1["Cd_mean"] = m0["Cd_mean"] * 1.005
    m2["Cd_mean"] = m1["Cd_mean"] * 1.001
    m1["Cl_amp_mean"] = m0["Cl_amp_mean"] * 1.027
    m2["Cl_amp_mean"] = m1["Cl_amp_mean"] * 1.004
    m1["Cl_demeaned_rms"] = m0["Cl_demeaned_rms"] * 1.027
    m2["Cl_demeaned_rms"] = m1["Cl_demeaned_rms"] * 1.004

    result = mesh_run.classify_three_mesh_comparison(m0, m1, m2)

    assert result["acceptance_status"] == "PASS"
    assert result["adjacent_m1_m2_within_1pct"] is True
    assert result["all_primary_deltas_reduced"] is True
    assert result["grid_independence_phase_can_close"] is True
    assert result["mesh_independence_established"] is False
    assert result["three_mesh_trends"]["Cl_amp"]["within_1pct_m0_m1"] is False
    assert result["three_mesh_trends"]["Cl_amp"]["within_1pct_m1_m2"] is True
    assert result["three_mesh_trends"]["Cl_amp"]["direction"] == "monotonic"


@pytest.mark.parametrize("case_index", [0, 1, 2], ids=["M0", "M1", "M2"])
@pytest.mark.parametrize("fault", ["unstable", "four_cycles"])
def test_three_mesh_comparison_requires_five_stable_cycles_on_all_three_meshes(case_index, fault):
    cases = [_stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2")]
    if fault == "unstable":
        cases[case_index]["is_stable"] = False
        expected = "UNSTABLE"
    else:
        cases[case_index]["num_cycles"] = 4
        expected = "INSUFFICIENT_CYCLES"

    result = mesh_run.classify_three_mesh_comparison(*cases)

    assert result["can_compare"] is False
    assert result["acceptance_status"] == expected
    assert result["grid_independence_phase_can_close"] is False


@pytest.mark.parametrize("lift_scale,expected_status", [(1.0, "PASS"), (1.015, "SENSITIVE")])
def test_m2_entry_solves_only_isolated_m2_from_m0_and_preserves_m0_and_m1(
    offline_mesh_run, tmp_path, monkeypatch, lift_scale, expected_status,
):
    state = offline_mesh_run
    m0_source = state["source"]
    m1_dir = tmp_path / "accepted_m1"
    m1_dir.mkdir()
    m1_model = m1_dir / "re100_s1b2_m1_solved.mph"
    m1_model.write_bytes(b"accepted M1 solved model")
    _write_case_diagnostics(m1_dir)
    m1_hash = mesh_run.compute_file_hash(m1_model)
    (m1_dir / "solve_response.json").write_text(json.dumps({
        "output_sha256": m1_hash,
        "solution_stats": {
            "num_dofs": 150, "num_timesteps": 1001, "time_min": 0.0,
            "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True,
        },
    }), encoding="utf-8")

    native_m0 = state["native_before"]
    native_m1 = state["native_after"]
    native_m1["sha256"] = m1_hash

    plan_m2 = _m2_plan_for_source(native_m0["sha256"])
    native_m2 = deepcopy(native_m0)
    native_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = native_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]
    _add_cylinder_wall_quads(mesh_m2, 12)

    monkeypatch.setattr(mesh_run, "M1_DIR", m1_dir)
    monkeypatch.setattr(mesh_run, "M1_MODEL", m1_model)
    monkeypatch.setattr(mesh_run, "PROTECTED_PATHS", (state["source"].parent, m1_dir, state["historical"]))

    m1_plan_file = tmp_path / "m1_plan.json"
    m1_plan_file.write_text(json.dumps(state["plan"]), encoding="utf-8")
    monkeypatch.setattr(mesh_run, "DEFAULT_PLAN", m1_plan_file)

    def inspect_native_m2(model_path):
        if model_path == m0_source:
            response = deepcopy(native_m0)
        elif model_path == m1_model:
            response = deepcopy(native_m1)
        else:
            response = deepcopy(native_m2)
        response["sha256"] = mesh_run.compute_file_hash(model_path)
        return response

    def export_m2(**kwargs):
        _write_case_diagnostics(kwargs["output_dir"])
        lift_path = kwargs["output_dir"] / "lift_coeff.txt"
        values = np.loadtxt(lift_path, comments="%")
        values[:, 1] *= lift_scale
        np.savetxt(lift_path, values, header="Time (s) coefficient (1)", comments="% ")
        return {"status": "SUCCESS"}

    state["inspect_native_mesh"].side_effect = inspect_native_m2
    state["export_s1_diagnostics"].side_effect = export_m2

    output_m2 = tmp_path / "new_m2"
    report = mesh_run.run_mesh_m2(output_m2, plan_m2)

    state["refine_native_mesh"].assert_called_once_with(
        model_path=m0_source,
        output_path=output_m2 / "re100_s1b2_m2_mesh.mph",
        plan=plan_m2,
        allow_overwrite=False,
    )
    state["run_study"].assert_called_once_with(
        model_path=output_m2 / "re100_s1b2_m2_mesh.mph",
        output_path=output_m2 / "re100_s1b2_m2_solved.mph",
        study_tag="transient_only",
        allow_overwrite=False,
    )
    assert report["mesh_level"] == "M2"
    assert report["status"] == expected_status
    assert report["physical_comparison"]["primary_pair"] == "M1_vs_M2"
    assert report["physical_comparison"]["acceptance_status"] == expected_status
    assert report["m0_mesh_summary"]["cylinder_boundary"]["edge_count"] == 4
    assert report["m1_mesh_summary"]["cylinder_boundary"]["edge_count"] == 8
    assert report["m2_mesh_summary"]["cylinder_boundary"]["edge_count"] == 12
    assert report["protected_files_unchanged"] is True
    assert m0_source.read_bytes() == b"accepted Case C source"
    assert m1_model.read_bytes() == b"accepted M1 solved model"
    assert set(report["wake_comparison"]["windows"].keys()) == {"m0", "m1", "m2"}


def _m3_plan_for_source(source_sha256: str) -> dict:
    plan = json.loads(mesh_run.M3_PLAN.read_text(encoding="utf-8"))
    plan["source_sha256"] = source_sha256
    return plan


def test_m3_config_binds_to_m0_at_h0_over_2744_and_checks_m2_count_ordering(native_readbacks):
    before_m0, after_m1, _ = native_readbacks
    plan_m2 = _m2_plan_for_source(before_m0["sha256"])
    after_m2 = deepcopy(before_m0)
    after_m2["sha256"] = "D" * 64
    after_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = after_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    plan_m3 = _m3_plan_for_source(before_m0["sha256"])
    after_m3 = deepcopy(before_m0)
    after_m3["sha256"] = "E" * 64
    after_m3["components"][0]["meshes"][0] = _square_mesh(4)
    mesh_m3 = after_m3["components"][0]["meshes"][0]
    mesh_m3["features"][0]["properties"]["custom"] = "on"
    for change in plan_m3["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m3["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    result = mesh_run.check_mesh_refinement(before_m0, after_m3, plan_m3, prior_refined=after_m2)

    assert result["status"] == "PASS"
    assert result["mesh_level"] == "M3"
    assert result["m3"]["num_elem"] > result["m2"]["num_elem"] > result["m0"]["num_elem"]
    assert result["m2_to_m3_element_count_ratio"] == pytest.approx(32 / 18)


@pytest.mark.parametrize(
    "fault",
    [
        "double_applied_from_m2_sizes",
        "source_case_is_m2",
        "source_model_is_already_m2",
        "mismatched_level_and_factor",
        "m3_not_finer_than_m2",
    ],
)
def test_m3_validation_rejects_cumulative_refinement_and_non_monotonic_counts(native_readbacks, fault):
    before_m0, after_m1, _ = native_readbacks
    plan_m2 = _m2_plan_for_source(before_m0["sha256"])
    after_m2 = deepcopy(before_m0)
    after_m2["sha256"] = "D" * 64
    after_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = after_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    plan_m3 = _m3_plan_for_source(before_m0["sha256"])
    after_m3 = deepcopy(before_m0)
    after_m3["sha256"] = "E" * 64
    after_m3["components"][0]["meshes"][0] = _square_mesh(4)
    mesh_m3 = after_m3["components"][0]["meshes"][0]
    mesh_m3["features"][0]["properties"]["custom"] = "on"
    for change in plan_m3["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m3["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]

    if fault == "double_applied_from_m2_sizes":
        plan_m3["changes"][1]["after"] = (0.0084 / 1.96) / 2.744
        mesh_m3["features"][0]["properties"]["hmax"] = plan_m3["changes"][1]["after"]
    elif fault == "source_case_is_m2":
        plan_m3["source_case"] = "S1-B2 M2"
    elif fault == "source_model_is_already_m2":
        before_m0 = after_m2
        plan_m3["source_sha256"] = after_m2["sha256"]
    elif fault == "mismatched_level_and_factor":
        plan_m3["refinement_factor"] = 1.96
    elif fault == "m3_not_finer_than_m2":
        after_m3["components"][0]["meshes"][0] = deepcopy(after_m2["components"][0]["meshes"][0])
        for change in plan_m3["changes"]:
            if change["property"] in {"hmax", "hmin"}:
                tag = change["feature_path"][0]
                feature = next(item for item in after_m3["components"][0]["meshes"][0]["features"] if item["tag"] == tag)
                feature["properties"][change["property"]] = change["after"]

    with pytest.raises((RuntimeError, ValueError)):
        mesh_run.check_mesh_refinement(before_m0, after_m3, plan_m3, prior_refined=after_m2)


@pytest.mark.parametrize("metric", ["St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"])
def test_four_mesh_comparison_returns_sensitive_when_any_m2_m3_metric_exceeds_one_percent(metric):
    m0, m1, m2, m3 = _stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2"), _stable_stats("M3")
    m1[metric] *= 1.03
    m2[metric] = m1[metric] * 1.018
    m3[metric] = m2[metric] * 1.015  # +1.5% relative to M2 (> 1% threshold)

    result = mesh_run.classify_four_mesh_comparison(m0, m1, m2, m3)

    assert result["primary_pair"] == "M2_vs_M3"
    assert result["acceptance_status"] == "SENSITIVE"
    assert result["adjacent_m2_m3_within_1pct"] is False
    assert result["grid_independence_phase_can_close"] is False
    assert result["mesh_independence_established"] is False


def test_four_mesh_comparison_passes_adjacent_gate_when_m2_m3_within_one_percent_and_deltas_reduce():
    m0, m1, m2, m3 = _stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2"), _stable_stats("M3")
    # Simulate decreasing deltas across all four levels with M2 -> M3 < 1%
    for metric, delta01, delta12, delta23 in [
        ("St_zc_mean", 0.0014, 0.0010, 0.0004),
        ("Cd_mean", 0.0065, 0.0040, 0.0015),
        ("Cl_amp_mean", 0.0084, 0.0059, 0.0020),
        ("Cl_demeaned_rms", 0.0060, 0.0043, 0.0014),
    ]:
        m1[metric] = m0[metric] + delta01
        m2[metric] = m1[metric] + delta12
        m3[metric] = m2[metric] + delta23

    result = mesh_run.classify_four_mesh_comparison(m0, m1, m2, m3)

    assert result["acceptance_status"] == "PASS"
    assert result["adjacent_m2_m3_within_1pct"] is True
    assert result["all_primary_deltas_reduced"] is True
    assert result["grid_independence_phase_can_close"] is True
    assert result["mesh_independence_established"] is False
    assert result["four_mesh_trends"]["Cl_amp"]["within_1pct_m2_m3"] is True
    assert result["four_mesh_trends"]["Cl_amp"]["all_deltas_monotonically_reduced"] is True
    assert result["four_mesh_trends"]["Cl_amp"]["direction"] == "monotonic"


def test_four_mesh_comparison_distinguishes_relative_diff_from_absolute_delta_reduction():
    """Verify that all_primary_deltas_reduced strictly tracks absolute increments |Delta_23| < |Delta_12|,
    not relative percent difference |r_23| < |r_12|.

    Counterexample:
      Q1 = 1.00000, Q2 = 1.00900, Q3 = 1.01805
      Delta_12 = 0.00900, Delta_23 = 0.00905 (|Delta_23| > |Delta_12| -> absolute increment increased)
      r_12 = 0.9000%, r_23 = 0.00905 / 1.00900 = 0.8969% (|r_23| < |r_12| -> relative difference decreased)
    Under this condition:
      - all_primary_rel_diffs_reduced is True
      - all_primary_deltas_reduced MUST be False
      - grid_independence_phase_can_close MUST be False
    """
    m0, m1, m2, m3 = _stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2"), _stable_stats("M3")
    for metric in ["Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"]:
        m1[metric] = m0[metric] + 0.0050
        m2[metric] = m1[metric] + 0.0030
        m3[metric] = m2[metric] + 0.0010

    m0["St_zc_mean"] = 0.991
    m1["St_zc_mean"] = 1.0
    m2["St_zc_mean"] = 1.009
    m3["St_zc_mean"] = 1.01805

    result = mesh_run.classify_four_mesh_comparison(m0, m1, m2, m3)

    st_trend = result["four_mesh_trends"]["St_zc"]
    assert st_trend["abs_rel_diff_reduced"] is True
    assert st_trend["abs_delta_reduced"] is False
    assert result["all_primary_rel_diffs_reduced"] is True
    assert result["all_primary_deltas_reduced"] is False
    assert result["adjacent_m2_m3_within_1pct"] is True
    assert result["grid_independence_phase_can_close"] is False

    # Now fix St_zc_mean to have actual absolute delta reduction: Delta_23 = 0.0085 < 0.0090
    m3["St_zc_mean"] = 1.009 + 0.0085
    result_fixed = mesh_run.classify_four_mesh_comparison(m0, m1, m2, m3)
    assert result_fixed["four_mesh_trends"]["St_zc"]["abs_delta_reduced"] is True
    assert result_fixed["all_primary_deltas_reduced"] is True
    assert result_fixed["grid_independence_phase_can_close"] is True



@pytest.mark.parametrize("case_index", [0, 1, 2, 3], ids=["M0", "M1", "M2", "M3"])
@pytest.mark.parametrize("fault", ["unstable", "four_cycles"])
def test_four_mesh_comparison_requires_five_stable_cycles_on_all_four_meshes(case_index, fault):
    cases = [_stable_stats("M0"), _stable_stats("M1"), _stable_stats("M2"), _stable_stats("M3")]
    if fault == "unstable":
        cases[case_index]["is_stable"] = False
        expected = "UNSTABLE"
    else:
        cases[case_index]["num_cycles"] = 4
        expected = "INSUFFICIENT_CYCLES"

    result = mesh_run.classify_four_mesh_comparison(*cases)

    assert result["can_compare"] is False
    assert result["acceptance_status"] == expected
    assert result["grid_independence_phase_can_close"] is False


def test_compare_four_wake_statistics():
    time = np.linspace(0.0, 12.0, 61)
    wakes = []
    for shift in (0.001, 0.002, 0.003, 0.004):
        w = {
            "window": [0.137, 10.137],
            "u_inf_m_s": 0.01,
            "channels": {
                "u": {"mean_m_s": shift, "demeaned_rms_m_s": 0.0001, "mean_over_u_inf": shift / 0.01, "rms_over_u_inf": 0.01},
                "v": {"mean_m_s": 0.0, "demeaned_rms_m_s": 0.0002, "mean_over_u_inf": 0.0, "rms_over_u_inf": 0.02},
            },
        }
        wakes.append(w)

    result = mesh_run.compare_four_wake_statistics(*wakes)

    assert result["normalization"] == "U_inf"
    assert result["diagnostic_only"] is True
    assert set(result["windows"].keys()) == {"m0", "m1", "m2", "m3"}
    assert result["channels"]["u"]["delta_23_mean_over_u_inf"] == pytest.approx(0.1)


@pytest.mark.parametrize("lift_scale,expected_status", [(1.0, "PASS"), (1.015, "SENSITIVE")])
def test_m3_entry_solves_only_isolated_m3_from_m0_and_preserves_m0_m1_m2(
    offline_mesh_run, tmp_path, monkeypatch, lift_scale, expected_status,
):
    state = offline_mesh_run
    m0_source = state["source"]

    # M1 reference mock
    m1_dir = tmp_path / "accepted_m1"
    m1_dir.mkdir()
    m1_model = m1_dir / "re100_s1b2_m1_solved.mph"
    m1_model.write_bytes(b"accepted M1 solved model")
    _write_case_diagnostics(m1_dir)
    m1_hash = mesh_run.compute_file_hash(m1_model)
    (m1_dir / "solve_response.json").write_text(json.dumps({
        "output_sha256": m1_hash,
        "solution_stats": {
            "num_dofs": 150, "num_timesteps": 1001, "time_min": 0.0,
            "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True,
        },
    }), encoding="utf-8")

    # M2 reference mock
    m2_dir = tmp_path / "accepted_m2"
    m2_dir.mkdir()
    m2_model = m2_dir / "re100_s1b2_m2_solved.mph"
    m2_model.write_bytes(b"accepted M2 solved model")
    _write_case_diagnostics(m2_dir)
    m2_hash = mesh_run.compute_file_hash(m2_model)
    (m2_dir / "solve_response.json").write_text(json.dumps({
        "output_sha256": m2_hash,
        "solution_stats": {
            "num_dofs": 200, "num_timesteps": 1001, "time_min": 0.0,
            "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True,
        },
    }), encoding="utf-8")

    native_m0 = state["native_before"]
    native_m1 = state["native_after"]
    native_m1["sha256"] = m1_hash

    plan_m2 = _m2_plan_for_source(native_m0["sha256"])
    native_m2 = deepcopy(native_m0)
    native_m2["sha256"] = m2_hash
    native_m2["components"][0]["meshes"][0] = _square_mesh(3)
    mesh_m2 = native_m2["components"][0]["meshes"][0]
    mesh_m2["features"][0]["properties"]["custom"] = "on"
    for change in plan_m2["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m2["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]
    _add_cylinder_wall_quads(mesh_m2, 12)

    plan_m3 = _m3_plan_for_source(native_m0["sha256"])
    native_m3 = deepcopy(native_m0)
    native_m3["components"][0]["meshes"][0] = _square_mesh(4)
    mesh_m3 = native_m3["components"][0]["meshes"][0]
    mesh_m3["features"][0]["properties"]["custom"] = "on"
    for change in plan_m3["changes"]:
        if change["property"] in {"hmax", "hmin"}:
            tag = change["feature_path"][0]
            feature = next(item for item in mesh_m3["features"] if item["tag"] == tag)
            feature["properties"][change["property"]] = change["after"]
    _add_cylinder_wall_quads(mesh_m3, 16)

    monkeypatch.setattr(mesh_run, "M1_DIR", m1_dir)
    monkeypatch.setattr(mesh_run, "M1_MODEL", m1_model)
    monkeypatch.setattr(mesh_run, "M2_DIR", m2_dir)
    monkeypatch.setattr(mesh_run, "M2_MODEL", m2_model)
    monkeypatch.setattr(mesh_run, "PROTECTED_PATHS", (state["source"].parent, m1_dir, m2_dir, state["historical"]))

    m1_plan_file = tmp_path / "m1_plan.json"
    m1_plan_file.write_text(json.dumps(state["plan"]), encoding="utf-8")
    monkeypatch.setattr(mesh_run, "DEFAULT_PLAN", m1_plan_file)

    m2_plan_file = tmp_path / "m2_plan.json"
    m2_plan_file.write_text(json.dumps(plan_m2), encoding="utf-8")
    monkeypatch.setattr(mesh_run, "M2_PLAN", m2_plan_file)

    def inspect_native_m3(model_path):
        if model_path == m0_source:
            response = deepcopy(native_m0)
        elif model_path == m1_model:
            response = deepcopy(native_m1)
        elif model_path == m2_model:
            response = deepcopy(native_m2)
        else:
            response = deepcopy(native_m3)
        response["sha256"] = mesh_run.compute_file_hash(model_path)
        return response

    def export_m3(**kwargs):
        _write_case_diagnostics(kwargs["output_dir"])
        lift_path = kwargs["output_dir"] / "lift_coeff.txt"
        values = np.loadtxt(lift_path, comments="%")
        values[:, 1] *= lift_scale
        np.savetxt(lift_path, values, header="Time (s) coefficient (1)", comments="% ")
        return {"status": "SUCCESS"}

    def solve_m3(**kwargs):
        kwargs["output_path"].write_bytes(b"M3 new solution")
        return {
            "status": "SUCCESS", "source_sha256": mesh_run.compute_file_hash(kwargs["model_path"]),
            "output_sha256": mesh_run.compute_file_hash(kwargs["output_path"]),
            "solution_stats": {"num_dofs": 300, "num_timesteps": 1001, "time_min": 0.0, "time_max": 200.0, "has_nan_or_inf": False, "has_physical_field": True},
            "log": "\n".join(f"{i} {0.05 * i:.8f} 0.05 2 1 2 2 0 0" for i in range(1, 4001)),
        }

    state["inspect_native_mesh"].side_effect = inspect_native_m3
    state["export_s1_diagnostics"].side_effect = export_m3
    state["run_study"].side_effect = solve_m3

    output_m3 = tmp_path / "new_m3"
    report = mesh_run.run_mesh_m3(output_m3, plan_m3)

    state["refine_native_mesh"].assert_called_once_with(
        model_path=m0_source,
        output_path=output_m3 / "re100_s1b2_m3_mesh.mph",
        plan=plan_m3,
        allow_overwrite=False,
    )
    state["run_study"].assert_called_once_with(
        model_path=output_m3 / "re100_s1b2_m3_mesh.mph",
        output_path=output_m3 / "re100_s1b2_m3_solved.mph",
        study_tag="transient_only",
        allow_overwrite=False,
    )
    assert report["mesh_level"] == "M3"
    assert report["status"] == expected_status
    assert report["physical_comparison"]["primary_pair"] == "M2_vs_M3"
    assert report["physical_comparison"]["acceptance_status"] == expected_status
    assert report["m0_mesh_summary"]["cylinder_boundary"]["edge_count"] == 4
    assert report["m1_mesh_summary"]["cylinder_boundary"]["edge_count"] == 8
    assert report["m2_mesh_summary"]["cylinder_boundary"]["edge_count"] == 12
    assert report["m3_mesh_summary"]["cylinder_boundary"]["edge_count"] == 16
    assert report["protected_files_unchanged"] is True
    assert m0_source.read_bytes() == b"accepted Case C source"
    assert m1_model.read_bytes() == b"accepted M1 solved model"
    assert m2_model.read_bytes() == b"accepted M2 solved model"
    assert set(report["wake_comparison"]["windows"].keys()) == {"m0", "m1", "m2", "m3"}
