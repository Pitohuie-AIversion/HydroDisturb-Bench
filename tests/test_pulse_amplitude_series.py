"""Amplitude-series provenance and scheduling checks; never launch COMSOL."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

from hydro_disturb_bench.branch_evidence import validate_native_branch_evidence
from hydro_disturb_bench.branch_pulse import compare_branch_fields
from hydro_disturb_bench.comsol_java import bridge

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs/evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json"
PLAN_SHA = "75b08df846652372f7c01a1e8699f67cf57912c44b45b719135d701b7f8c4f8d"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def runner():
    return _load_module(
        "pulse_amplitude_series_under_test", ROOT / "scripts/run_m3_pulse_amplitude_series.py"
    )


@pytest.fixture
def series(runner):
    return runner.load_frozen_plan(PLAN_PATH, PLAN_SHA)


@pytest.mark.parametrize("amplitude", [0.0025, 0.005, 0.010])
def test_derived_plan_changes_only_registered_amplitude_and_source_references(runner, series, amplitude):
    before = deepcopy(series)
    physical_path = ROOT / series["base_plan"]["path"]
    physical_bytes = physical_path.read_bytes()
    base = json.loads(physical_bytes)
    expected = deepcopy(base)
    expected["source_model"] = str((runner.DATA_ROOT / series["source_model"]["path"]).resolve())
    expected["restart_protocol"] = json.loads(
        (ROOT / series["restart_protocol"]["path"]).read_text(encoding="utf-8")
    )
    expected["pulse"]["acceleration_amplitude_m_s2"] = amplitude
    for key in (
        "peak_force_density_N_m3",
        "peak_force_per_depth_N_m",
        "impulse_per_depth_N_s_m",
    ):
        expected["source_integral_reference"][key] *= amplitude / 0.005

    actual = runner.derive_plan(series, amplitude)

    assert actual == expected
    assert series == before
    assert physical_path.read_bytes() == physical_bytes
    # Independent dimensional calculation, rather than copying the scaling implementation.
    rho_a = base["scales"]["rho_f_kg_m3"] * amplitude
    expected_peak = rho_a * base["source_integral_reference"]["gaussian_area_m2"]
    actual_source = actual["source_integral_reference"]
    assert actual_source["peak_force_density_N_m3"] == pytest.approx(rho_a, rel=1e-14)
    assert actual_source["peak_force_per_depth_N_m"] == pytest.approx(expected_peak, rel=1e-14)
    assert actual_source["impulse_per_depth_N_s_m"] == pytest.approx(
        expected_peak * (base["pulse"]["off_s"] - base["pulse"]["on_s"]) / 2,
        rel=1e-14,
    )
    assert actual["restart_acceptance"] == base["restart_acceptance"]
    assert actual["response_acceptance"] == base["response_acceptance"]
    assert actual_source["native_integral_relative_tolerance"] == 0.01


@pytest.mark.parametrize("amplitude", [0.0, -0.0025, 0.0075, 0.02, float("nan"), float("inf"), True])
def test_derived_plan_rejects_unregistered_amplitudes(runner, series, amplitude):
    with pytest.raises(bridge.ModelValidationError):
        runner.derive_plan(series, amplitude)


def test_frozen_series_bytes_are_checked_before_plan_is_used(runner, tmp_path):
    path = tmp_path / "plan.json"
    path.write_bytes(PLAN_PATH.read_bytes() + b"\n")
    with pytest.raises(bridge.ModelValidationError):
        runner.load_frozen_plan(path, PLAN_SHA)


@pytest.mark.parametrize("fault", ["new_amplitude", "duplicate_case", "resolve_old_again", "reuse_new"])
def test_frozen_series_structure_cannot_authorize_extra_or_relabelled_cases(runner, tmp_path, fault):
    value = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if fault == "new_amplitude":
        value["new_amplitudes_m_s2"][0] = 0.0075
        value["cases"][0]["amplitude_m_s2"] = 0.0075
    elif fault == "duplicate_case":
        value["cases"].append(deepcopy(value["cases"][0]))
    elif fault == "resolve_old_again":
        value["cases"][1]["mode"] = "new"
    else:
        value["cases"][0]["mode"] = "reuse"
    path = tmp_path / "plan.json"
    _write(path, value)
    with pytest.raises(bridge.ModelValidationError):
        runner.load_frozen_plan(path, _hash(path))


@pytest.fixture
def sample_binding(tmp_path):
    model = tmp_path / "new_amplitude.mph"
    model.write_bytes(b"offline saved branch bytes")
    execution = {"output_model": str(model), "output_sha256": _hash(model)}
    fields = {"metadata": {"source_model": str(model), "source_sha256": _hash(model)}}
    return fields, model, execution


def test_sample_identity_matches_the_exact_solved_branch(runner, sample_binding):
    runner.validate_sample(*sample_binding)


@pytest.mark.parametrize("reference_name,archive_name", [
    ("zero", "native_zero_index.zip"), ("a0050", "native_pulse_index.zip"),
])
def test_real_archived_loader_metadata_accepts_comsol_uppercase_sha(
    runner, series, reference_name, archive_name,
):
    # This small committed archive supplies actual loader metadata, including
    # COMSOL's uppercase digest, rather than a manually invented normal sample.
    path = ROOT / "docs/evidence/m3_branch_pulse_20261105" / archive_name
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist()
                      if name.endswith("/fields/sequence_manifest.json"))
        metadata = json.loads(archive.read(member))
    role = "zero_model" if reference_name == "zero" else "a0050_model"
    expected = next(item["sha256"] for item in series["reference_files"] if item["role"] == role)
    assert metadata["source_sha256"] == metadata["source_sha256"].upper()
    assert metadata["source_sha256"] != expected
    assert metadata["shape"] == [101, 3, 97, 161]
    runner.validate_sample({"metadata": metadata}, Path(metadata["source_model"]),
                           {"output_sha256": expected})


@pytest.mark.parametrize("invalid_hash", ["a" * 63, "a" * 65, "g" * 64, None, 123])
def test_matching_but_invalid_hash_strings_cannot_pass_sample_binding(
    runner, sample_binding, invalid_hash,
):
    fields, model, execution = sample_binding
    fields["metadata"]["source_sha256"] = invalid_hash
    execution["output_sha256"] = invalid_hash
    with pytest.raises(bridge.ModelValidationError):
        runner.validate_sample(fields, model, execution)


@pytest.mark.parametrize("fault", ["old_amplitude_path", "wrong_hash"])
def test_sample_cannot_be_substituted_from_another_amplitude(runner, sample_binding, tmp_path, fault):
    fields, model, execution = sample_binding
    if fault == "old_amplitude_path":
        old_model = tmp_path / "old_a0050.mph"
        old_model.write_bytes(model.read_bytes())
        fields["metadata"]["source_model"] = str(old_model)
    else:
        fields["metadata"]["source_sha256"] = "0" * 64
    with pytest.raises(bridge.ModelValidationError):
        runner.validate_sample(fields, model, execution)


def _fields(times, model, *, pulse=False):
    """Full prescribed grid, analytic quiet baseline and a detectable later pulse."""
    x, y = np.linspace(0, .4, 161), np.linspace(0, .24, 97)
    xx, yy = np.meshgrid(x, y)
    cylinder = np.hypot(xx - .1, yy - .12) <= .005 + 1e-12
    frame = np.zeros((1, 3, len(y), len(x)), dtype=np.float64)
    frame[:, :, cylinder] = np.nan
    values = np.broadcast_to(frame, (len(times), 3, len(y), len(x)))
    if pulse:
        values = values.copy()
        values[np.asarray(times) >= 182, 1] += .0002
    mask = np.broadcast_to(cylinder, (len(times), len(y), len(x)))
    return {
        "values": values, "mask": mask, "cylinder_mask": cylinder,
        "invalid_mask": mask.copy(), "x": x, "y": y, "times": np.asarray(times),
        "metadata": {
            "status": "PASS", "tensor_layout": "(T, 3, Ny, Nx)",
            "shape": list(values.shape), "channel_order": ["u", "v", "p"],
            "units": ["m/s", "m/s", "Pa"], "grid_shape": [161, 97],
            "actual_times": list(times), "requested_times": list(times),
            # Match the real Java exporter / sequence loader's hexadecimal case.
            "source_model": str(model), "source_sha256": _hash(model).upper(),
        },
    }


def _step_log():
    rows = ["Step Time Stepsize", "- 180 - out"]
    rows.extend(f"{i} {180 + i * .05:.8f} .05" for i in range(1, 401))
    return "\n".join(rows) + "\n"


@pytest.fixture
def reference_world(runner, tmp_path, monkeypatch):
    """Real files/hashes and native schema; only the large field loader is external."""
    code_root, data_root = tmp_path / "code", tmp_path / "data"
    code_root.mkdir()
    data_root.mkdir()
    spec = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    base = json.loads((ROOT / spec["base_plan"]["path"]).read_text(encoding="utf-8"))
    protocol = json.loads((ROOT / spec["restart_protocol"]["path"]).read_text(encoding="utf-8"))
    native_helpers = _load_module("series_native_helpers", ROOT / "tests/test_branch_evidence.py")
    prepare, zero, pulse, _ = native_helpers.native_case.__wrapped__(tmp_path)
    source = data_root / spec["source_model"]["path"]
    checkpoint = data_root / spec["checkpoint"]["path"]
    for path, raw in ((source, b"offline source M3"), (checkpoint, b"offline native checkpoint")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    base["source_sha256"] = _hash(source)
    spec["source_model"]["sha256"] = _hash(source)
    spec["checkpoint"]["sha256"] = _hash(checkpoint)
    prepare.update(source_model=str(source), output_model=str(checkpoint),
                   source_sha256_before=_hash(source), source_sha256_after=_hash(source),
                   output_sha256=_hash(checkpoint))
    base_path = code_root / spec["base_plan"]["path"]
    _write(base_path, base)
    spec["base_plan"]["sha256"] = _hash(base_path)
    protocol["physical_plan_sha256"] = _hash(base_path)
    protocol_path = code_root / spec["restart_protocol"]["path"]
    _write(protocol_path, protocol)
    spec["restart_protocol"]["sha256"] = _hash(protocol_path)
    derived = deepcopy(base)
    derived["source_model"] = str(source)
    derived["restart_protocol"] = protocol
    reference = data_root / spec["reference_run"]
    prepare_dir = reference / "prepare"
    _write(prepare_dir / "readback.json", prepare)
    _write(prepare_dir / "execution.json", {
        "status": "COMPLETE", "operation": "prepare_branch_state", "reused": True,
        "source_model": str(source), "source_sha256_before": _hash(source),
        "source_sha256_after": _hash(source), "output_model": str(checkpoint),
        "output_sha256": _hash(checkpoint), "readback_sha256": _hash(prepare_dir / "readback.json"),
    })
    loaded = {}
    responses = {"zero": zero, "a0050": pulse}
    for name, response in responses.items():
        reference_spec = spec["references"][name]
        stage = data_root / reference_spec["stage_dir"]
        stage.mkdir(parents=True)
        model = stage / ("zero.mph" if name == "zero" else "pulse.mph")
        model.write_bytes(("offline " + name + " solved model").encode())
        response.update(source_model=str(checkpoint), output_model=str(model),
                        source_sha256_before=_hash(checkpoint), source_sha256_after=_hash(checkpoint),
                        output_sha256=_hash(model), time_solver_log=_step_log())
        _write(stage / "readback.json", response)
        _write(stage / "execution.json", {
            "status": "COMPLETE", "operation": "run_pulse_branch", "source_model": str(checkpoint),
            "source_sha256_before": _hash(checkpoint), "source_sha256_after": _hash(checkpoint),
            "output_model": str(model), "output_sha256": _hash(model),
            "readback_sha256": _hash(stage / "readback.json"),
            "command": ["run-pulse-branch", str(checkpoint), str(model),
                        str(reference_spec["amplitude_m_s2"]), protocol["id"]],
        })
        (stage / "process_002_stdout.log").write_text(_step_log(), encoding="utf-8")
        fields_path = data_root / reference_spec["fields_dir"]
        fields = _fields(np.linspace(180, 200, 101), model, pulse=name == "a0050")
        loaded[fields_path.resolve()] = fields
        _write(fields_path / "sequence_manifest.json", fields["metadata"])
        _write(fields_path.parent / "execution.json", {
            "status": "COMPLETE", "source_model": str(model), "source_sha256": _hash(model),
            "manifest_sha256": _hash(fields_path / "sequence_manifest.json"),
            "shape": fields["metadata"]["shape"],
        })
    baseline_path = data_root / spec["baseline_fields"]
    baseline = _fields(np.linspace(170, 200, 151), source)
    loaded[baseline_path.resolve()] = baseline
    _write(baseline_path / "sequence_manifest.json", baseline["metadata"])
    native_report = validate_native_branch_evidence(prepare, zero, pulse, derived)
    field_report = compare_branch_fields(
        baseline,
        loaded[(data_root / spec["references"]["zero"]["fields_dir"]).resolve()],
        loaded[(data_root / spec["references"]["a0050"]["fields_dir"]).resolve()],
        derived,
    )
    assert native_report["status"] == field_report["status"] == "PASS"
    _write(reference / "analysis/report.json", {
        "status": "PASS", "native_status": "PASS", "field_status": "PASS", "step_status": "PASS",
        "plan_sha256": _hash(base_path), "restart_protocol_sha256": _hash(protocol_path),
        "restart_protocol_id": protocol["id"], "source_model_sha256_after": _hash(source),
    })
    _write(reference / "analysis/native_validation.json", native_report)
    _write(reference / "analysis/field_comparison.json", field_report)
    _write(reference / "analysis/step_validation.json", {
        "status": "PASS", "zero": {"status": "PASS"}, "pulse": {"status": "PASS"},
    })
    spec["native_identity"] = {key: native_report[key] for key in spec["native_identity"]}
    for item in spec["reference_files"]:
        path = data_root / item["path"]
        item.update(sha256=_hash(path), bytes=path.stat().st_size)
    plan_path = code_root / "series_plan.json"
    _write(plan_path, spec)
    for name, value in (("ROOT", code_root), ("DATA_ROOT", data_root),
                        ("BASE_PLAN_SHA", _hash(base_path)),
                        ("RESTART_PROTOCOL_SHA", _hash(protocol_path))):
        monkeypatch.setattr(runner, name, value)
    loader = Mock(side_effect=lambda path: loaded[Path(path).resolve()])
    monkeypatch.setattr(runner, "load_full_field_sequence", loader)
    monkeypatch.setattr(runner.execution, "load_full_field_sequence", loader)
    frozen = runner.load_frozen_plan(plan_path, _hash(plan_path))
    return {"plan": frozen, "spec": spec, "plan_path": plan_path,
            "source": source, "checkpoint": checkpoint, "reference": reference,
            "loaded": loaded, "responses": responses, "prepare": prepare,
            "base": base, "loader": loader, "data_root": data_root,
            "native_helpers": native_helpers}


def _repin_reference(world, path, value=None):
    """Keep fixture byte integrity valid while injecting a semantic contradiction."""
    if value is not None:
        _write(path, value)
    for item in world["plan"]["reference_files"]:
        if (world["data_root"] / item["path"]).resolve() == path.resolve():
            item.update(sha256=_hash(path), bytes=path.stat().st_size)
            clean = {k: v for k, v in world["plan"].items() if not k.startswith("_frozen_")}
            _write(world["plan_path"], clean)
            world["plan"]["_frozen_plan_sha256"] = _hash(world["plan_path"])
            return
    raise AssertionError(f"Fixture path is not pinned: {path}")


def test_reference_reuse_checks_real_native_and_field_identities(runner, reference_world, monkeypatch):
    external = Mock(side_effect=AssertionError("Reference validation must not launch COMSOL"))
    monkeypatch.setattr(bridge, "run_command", external)
    result = runner.validate_reference(reference_world["plan"])
    assert result["zero"]["amplitude_m_s2"] == 0
    assert result["a0050"]["amplitude_m_s2"] == .005
    external.assert_not_called()


@pytest.mark.parametrize("fault", ["checkpoint_bytes", "zero_model_bytes", "missing_old_readback"])
def test_reference_byte_failures_stop_before_any_new_solve(runner, reference_world, monkeypatch, fault):
    world = reference_world
    if fault == "checkpoint_bytes":
        world["checkpoint"].write_bytes(b"different checkpoint")
    elif fault == "zero_model_bytes":
        Path(world["responses"]["zero"]["output_model"]).write_bytes(b"different old zero model")
    else:
        (world["reference"] / "zero/readback.json").unlink()
    external = Mock(side_effect=AssertionError("Unverified reference triggered COMSOL"))
    monkeypatch.setattr(bridge, "run_command", external)
    with pytest.raises((bridge.ModelValidationError, FileNotFoundError)):
        runner.run_series(world["plan"], world["data_root"] / "new_series", stage="solve")
    external.assert_not_called()


@pytest.mark.parametrize("fault", ["nonzero_old_zero", "wrong_checkpoint_path", "wrong_checkpoint_hash", "wrong_protocol"])
def test_reference_semantic_failures_are_not_hidden_by_valid_file_hashes(
    runner, reference_world, fault,
):
    world = reference_world
    stage = world["reference"] / "zero"
    record_path = stage / "execution.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if fault == "nonzero_old_zero":
        path = stage / "readback.json"
        response = json.loads(path.read_text(encoding="utf-8"))
        response["amplitude_m_s2"] = .005
        _repin_reference(world, path, response)
        record["readback_sha256"] = _hash(path)
    elif fault == "wrong_checkpoint_path":
        record["source_model"] = str(world["checkpoint"].with_name("unrelated_checkpoint.mph"))
    elif fault == "wrong_checkpoint_hash":
        record["source_sha256_before"] = "0" * 64
    else:
        record["command"][-1] = "unregistered-restart-protocol"
    _repin_reference(world, record_path, record)
    with pytest.raises(bridge.ModelValidationError):
        runner.validate_reference(world["plan"])


@pytest.mark.parametrize("fault", ["baseline_model", "zero_from_a0050", "manifest_hash"])
def test_reused_field_provenance_must_match_the_accepted_model(runner, reference_world, fault):
    world = reference_world
    if fault == "baseline_model":
        path = world["data_root"] / world["plan"]["baseline_fields"]
        world["loaded"][path.resolve()]["metadata"]["source_sha256"] = "0" * 64
    elif fault == "zero_from_a0050":
        path = world["data_root"] / world["plan"]["references"]["zero"]["fields_dir"]
        world["loaded"][path.resolve()]["metadata"].update(
            source_model=world["responses"]["a0050"]["output_model"],
            source_sha256=_hash(Path(world["responses"]["a0050"]["output_model"])),
        )
    else:
        path = world["reference"] / "sample_zero/execution.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["manifest_sha256"] = "0" * 64
        _repin_reference(world, path, record)
    with pytest.raises(bridge.ModelValidationError):
        runner.validate_reference(world["plan"])


def _new_bridge(world, *, fault=None, fail_amplitude=None):
    def call(args, **kwargs):
        amplitude = float(args[3])
        if amplitude == fail_amplitude:
            raise bridge.ModelValidationError("Injected second new Time failure")
        target = Path(args[2])
        target.write_bytes(f"offline solved amplitude {amplitude}".encode())
        response = deepcopy(world["responses"]["a0050"])
        response.update(series_id="m3-pulse-amplitude-series-v1", amplitude_m_s2=amplitude,
                        source_model=str(world["checkpoint"]), output_model=str(target),
                        output_sha256=_hash(target))
        for phase in ("configured", "after"):
            response[phase]["parameters"]["branch_A"] = {
                "value": amplitude, "unit": "m/s^2", "expr": str(amplitude) + "[m/s^2]",
            }
        response["force_evaluation"] = world["native_helpers"]._force_evaluation(
            np.linspace(180, 200, 101), amplitude, world["base"], "series_post",
        )
        response["force_initial_evaluation"] = world["native_helpers"]._force_evaluation(
            [180.0], amplitude, world["base"], "series_initial",
        )
        if fault == "wrong_amplitude":
            response["amplitude_m_s2"] = .005
        elif fault == "wrong_series":
            response["series_id"] = "unregistered-series"
        elif fault == "wrong_branch_A":
            response["after"]["parameters"]["branch_A"]["value"] = .005
        elif fault == "wrong_checkpoint":
            response["source_model"] = str(world["checkpoint"].with_name("another_seed.mph"))
        elif fault == "source_integral":
            node = response["force_evaluation"]["integral"]
            node["Fy_per_depth"] = (np.asarray(node["Fy_per_depth"]) * 1.03).tolist()
        return response
    return Mock(side_effect=call)


def _mock_sample(runner, world, monkeypatch, *, fault=None):
    def sample(output, name, plan, **kwargs):
        _, model, record = runner.execution.completed_stage(output / name)
        directory = output / f"sample_{name}/fields"
        fields = _fields(np.linspace(180, 200, 101), model, pulse=fault != "undetectable")
        if fault == "wrong_source":
            old = Path(world["responses"]["a0050"]["output_model"])
            fields["metadata"].update(source_model=str(old), source_sha256=_hash(old))
        world["loaded"][directory.resolve()] = fields
        _write(directory / "sequence_manifest.json", fields["metadata"])
        _write(directory.parent / "execution.json", {
            "status": "COMPLETE", "source_model": str(model),
            "source_sha256": record["output_sha256"],
            "manifest_sha256": _hash(directory / "sequence_manifest.json"),
        })
    monkeypatch.setattr(runner.execution, "sample_branch", sample)


def test_series_entrypoint_solves_only_two_new_amplitudes_and_reuses_controls(
    runner, reference_world, monkeypatch,
):
    world = reference_world
    before = {item["path"]: _hash(world["data_root"] / item["path"])
              for item in world["plan"]["reference_files"]}
    external = _new_bridge(world)
    monkeypatch.setattr(bridge, "run_command", external)
    _mock_sample(runner, world, monkeypatch)
    output = world["data_root"] / "new_series"
    report = runner.run_series(world["plan"], output)
    assert report["status"] == "PASS"
    assert [float(call.args[0][3]) for call in external.call_args_list] == [.0025, .010]
    assert all(call.args[0][0] == "run-pulse-series-branch" for call in external.call_args_list)
    assert all(call.args[0][-2:] == ["native-restart-consistent-off-v2", "m3-pulse-amplitude-series-v1"]
               for call in external.call_args_list)
    assert not (output / "zero").exists()
    assert not (output / "a0050").exists()
    assert [case["amplitude_m_s2"] for case in report["cases"]] == [.0025, .005, .010]
    assert report["cases"][1]["reused"] is True
    assert {path: _hash(world["data_root"] / path) for path in before} == before
    for name in ("a0025", "a0100"):
        native = json.loads((output / "analysis" / name / "native_validation.json").read_text())
        assert native["status"] == "PASS"


@pytest.mark.parametrize("fault", ["wrong_amplitude", "wrong_series", "wrong_branch_A", "wrong_checkpoint"])
def test_new_solve_rejects_actual_identity_faults_and_retains_returned_evidence(
    runner, reference_world, monkeypatch, fault,
):
    world = reference_world
    external = _new_bridge(world, fault=fault)
    monkeypatch.setattr(bridge, "run_command", external)
    output = world["data_root"] / "new_series"
    output.mkdir()
    with pytest.raises(bridge.ModelValidationError):
        runner._solve_new(output, world["checkpoint"], "a0025", .0025,
                          world["plan"], runner.derive_plan(world["plan"], .0025))
    stage = output / "a0025"
    assert (stage / "readback.json").is_file()
    assert json.loads((stage / "series_execution.json").read_text())["status"] == "FAIL"
    before = {p.name: p.read_bytes() for p in stage.iterdir() if p.is_file()}
    external.reset_mock()
    with pytest.raises(bridge.ModelValidationError):
        runner._solve_new(output, world["checkpoint"], "a0025", .0025,
                          world["plan"], runner.derive_plan(world["plan"], .0025))
    external.assert_not_called()
    assert {p.name: p.read_bytes() for p in stage.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("fault", ["restart", "response", "source_tolerance", "source_reference"])
def test_new_solve_cannot_relax_thresholds_or_fit_source_reference_to_observed_values(
    runner, reference_world, monkeypatch, fault,
):
    world = reference_world
    derived = runner.derive_plan(world["plan"], .0025)
    if fault == "restart":
        derived["restart_acceptance"]["velocity_time_rms_over_U_max"] *= 2
    elif fault == "response":
        derived["response_acceptance"]["response_floor_over_U"] /= 2
    elif fault == "source_tolerance":
        derived["source_integral_reference"]["native_integral_relative_tolerance"] = .1
    else:
        derived["source_integral_reference"]["peak_force_per_depth_N_m"] *= 1.03
    external = Mock()
    monkeypatch.setattr(bridge, "run_command", external)
    with pytest.raises(bridge.ModelValidationError):
        runner._solve_new(world["data_root"] / "new_series", world["checkpoint"], "a0025", .0025,
                          world["plan"], derived)
    external.assert_not_called()


def test_new_sample_cannot_be_relabelled_from_the_reused_a0050_case(
    runner, reference_world, monkeypatch,
):
    world = reference_world
    external = _new_bridge(world)
    monkeypatch.setattr(bridge, "run_command", external)
    _mock_sample(runner, world, monkeypatch, fault="wrong_source")
    with pytest.raises(bridge.ModelValidationError, match="Sampled fields"):
        runner.run_series(world["plan"], world["data_root"] / "new_series")
    assert external.call_count == 1


@pytest.mark.parametrize("fault", ["source_integral", "undetectable"])
def test_numerical_failure_stops_series_without_tuning_amplitude_or_acceptance(
    runner, reference_world, monkeypatch, fault,
):
    world = reference_world
    external = _new_bridge(world, fault=fault)
    monkeypatch.setattr(bridge, "run_command", external)
    _mock_sample(runner, world, monkeypatch, fault=fault)
    report = runner.run_series(world["plan"], world["data_root"] / "new_series")
    assert report["status"] == "FAIL"
    assert external.call_count == 1
    assert report["cases"][0]["amplitude_m_s2"] == .0025
    assert report["cases"][0]["status"] == "FAIL"
    assert report["cases"][0]["native_status" if fault == "source_integral" else "field_status"] == "FAIL"


def test_second_new_solve_failure_preserves_first_case_and_all_old_artifacts(
    runner, reference_world, monkeypatch,
):
    world = reference_world
    before = {item["path"]: _hash(world["data_root"] / item["path"])
              for item in world["plan"]["reference_files"]}
    external = _new_bridge(world, fail_amplitude=.010)
    monkeypatch.setattr(bridge, "run_command", external)
    output = world["data_root"] / "new_series"
    with pytest.raises(bridge.ModelValidationError, match="Injected second"):
        runner.run_series(world["plan"], output, stage="solve")
    assert external.call_count == 2
    assert json.loads((output / "a0025/series_execution.json").read_text())["status"] == "COMPLETE"
    assert (output / "a0100/failure.json").is_file()
    assert json.loads((output / "a0100/series_execution.json").read_text())["status"] == "FAIL"
    assert {path: _hash(world["data_root"] / path) for path in before} == before


def test_existing_completed_new_model_is_checked_before_reuse(runner, reference_world, monkeypatch):
    world = reference_world
    external = _new_bridge(world)
    monkeypatch.setattr(bridge, "run_command", external)
    output = world["data_root"] / "new_series"
    output.mkdir()
    derived = runner.derive_plan(world["plan"], .0025)
    _, model, _ = runner._solve_new(output, world["checkpoint"], "a0025", .0025, world["plan"], derived)
    model.write_bytes(b"changed completed new model")
    external.reset_mock()
    with pytest.raises(bridge.ModelValidationError, match="Completed model bytes changed"):
        runner._solve_new(output, world["checkpoint"], "a0025", .0025, world["plan"], derived)
    external.assert_not_called()


@pytest.mark.parametrize("fault", ["failed_series", "wrong_readback_binding"])
def test_sample_only_stage_cannot_bypass_new_series_provenance(
    runner, reference_world, monkeypatch, fault,
):
    world = reference_world
    output = world["data_root"] / "new_series"
    runner.run_series(world["plan"], output, stage="precheck")
    external = _new_bridge(world)
    monkeypatch.setattr(bridge, "run_command", external)
    runner._solve_new(output, world["checkpoint"], "a0025", .0025,
                      world["plan"], runner.derive_plan(world["plan"], .0025))
    path = output / "a0025/series_execution.json"
    record = json.loads(path.read_text())
    if fault == "failed_series":
        record["status"] = "FAIL"
    else:
        record["readback_sha256"] = "0" * 64
    _write(path, record)
    sampler = Mock(side_effect=AssertionError("Unverified new model reached sampling"))
    monkeypatch.setattr(runner.execution, "sample_branch", sampler)
    with pytest.raises(bridge.ModelValidationError):
        runner.run_series(world["plan"], output, stage="sample")
    sampler.assert_not_called()


def test_unrelated_existing_output_is_not_overwritten(runner, reference_world, monkeypatch):
    world = reference_world
    output = world["data_root"] / "unrelated_output"
    output.mkdir()
    sentinel = output / "history.txt"
    sentinel.write_bytes(b"must remain unchanged")
    external = Mock()
    monkeypatch.setattr(bridge, "run_command", external)
    with pytest.raises((bridge.ModelValidationError, FileNotFoundError)):
        runner.run_series(world["plan"], output, stage="solve")
    external.assert_not_called()
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"must remain unchanged"
