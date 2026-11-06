"""Configurable-pulse entrypoint tests with synthetic native and field reads.

The real JSON admission, freeze, execution evidence and validation paths run.
Only COMSOL commands and field export/load are replaced; no solver is started.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_disturb_bench.comsol_java.errors import InvalidParameterError, ModelValidationError
from hydro_disturb_bench.pulse_parameters import load_pulse_config, properties_payload
from tests import test_branch_evidence as native_fixtures
from tests import test_branch_pulse as field_fixtures

REPO = Path(__file__).resolve().parents[1]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location("local_pulse_runner_test", REPO / "scripts/run_m3_local_pulse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def catalog(runner, tmp_path, monkeypatch):
    """Pin real small files while retaining the accepted native-schema fixture."""
    native_case = native_fixtures.native_case.__wrapped__(tmp_path)
    experiment = field_fixtures.experiment.__wrapped__()
    repo, data = tmp_path / "repo", tmp_path / "data"
    source, checkpoint = data / "original.mph", data / "native/checkpoint.mph"
    zero_model = data / "zero/model.mph"
    for path, raw in ((source, b"synthetic original"), (checkpoint, b"synthetic checkpoint"),
                      (zero_model, b"synthetic zero model")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    prepare, _, _, base = native_case
    prepare.update(source_model=str(source), output_model=str(checkpoint),
                   source_sha256_before=runner.sha(source), source_sha256_after=runner.sha(source),
                   output_sha256=runner.sha(checkpoint))
    base.update(source_model=str(source), source_sha256=runner.sha(source))
    # A small analytic grid exercises the real response formulas, with unchanged thresholds.
    base["grid_shape_nx_ny"] = experiment[3]["grid_shape_nx_ny"]
    base_path, prepare_path = repo / "physical_plan.json", data / "reference/prepare/readback.json"
    _write(base_path, base)
    _write(prepare_path, prepare)
    controls = {}
    rows = []
    for name, model, fields in (("baseline", source, experiment[0]), ("zero", zero_model, experiment[1])):
        directory = data / name / "fields"
        manifest = directory / "sequence_manifest.json"
        _write(manifest, {"synthetic_fixture": True, "source_model": str(model),
                          "source_sha256": runner.sha(model)})
        # The actual sampling API retains compute_file_hash's uppercase hex;
        # the accepted data index and execution records normalize to lowercase.
        fields["metadata"].update(source_model=str(model), source_sha256=runner.sha(model).upper())
        controls[str(directory)] = fields
        rows.append({"trajectory_id": name, "sequence_dir": str(directory),
                     "manifest_sha256": runner.sha(manifest), "source_sha256": runner.sha(model)})
    series = {"checkpoint": {"path": "native/checkpoint.mph", "sha256": runner.sha(checkpoint)},
              "source_model": {"path": "original.mph", "sha256": runner.sha(source)},
              "reference_run": "reference",
              "reference_files": [{"path": "reference/prepare/readback.json", "sha256": runner.sha(prepare_path)}],
              "base_plan": {"path": "physical_plan.json", "sha256": runner.sha(base_path)},
              "baseline_fields": "baseline/fields", "references": {"zero": {"fields_dir": "zero/fields"}}}
    series_path, index_path = repo / "series/plan.json", repo / "index/index.json"
    _write(series_path, series)
    _write(index_path, {"trajectories": rows})
    # This file is hashed as an explicitly synthetic execution fixture, never compiled.
    java = repo / "src/hydro_disturb_bench/comsol_java/ConfigurablePulseBranch.java"
    java.parent.mkdir(parents=True)
    java.write_text("// synthetic source fixture; no Java invocation\n", encoding="ascii")
    monkeypatch.setattr(runner, "ROOT", repo)
    monkeypatch.setattr(runner, "SERIES_PATH", series_path)
    monkeypatch.setattr(runner, "SERIES_SHA", runner.sha(series_path))
    monkeypatch.setattr(runner, "INDEX_PATH", index_path)
    monkeypatch.setattr(runner, "INDEX_SHA", runner.sha(index_path))
    config = tmp_path / "config/pulse.json"
    config.parent.mkdir()
    config.write_bytes((REPO / "configs/m3_local_pulse.json").read_bytes())
    return SimpleNamespace(repo=repo, data=data, output=tmp_path / "results/run", config=config,
                           source=source, checkpoint=checkpoint, prepare_path=prepare_path,
                           base_path=base_path, series=series, series_path=series_path,
                           index_path=index_path, controls=controls, native_case=native_case,
                           fields=experiment[2], monkeypatch=monkeypatch)


def _repin_series(runner, catalog):
    _write(catalog.series_path, catalog.series)
    catalog.monkeypatch.setattr(runner, "SERIES_SHA", runner.sha(catalog.series_path))


@pytest.fixture
def backend(runner, catalog, monkeypatch):
    from tests.test_pulse_validation import make_pulse_evidence

    events, modes, requests = [], [], []
    state = SimpleNamespace(events=events, modes=modes, requests=requests, fault=None)
    real_validator = runner.pulse_validation.validate_pulse_readback

    def validate(response, config, *, mode, prepare):
        modes.append(mode)
        result = real_validator(response, config, mode=mode, prepare=prepare)
        return {**result, "status": "FAIL"} if state.fault == "validator_fail" else result

    def command(args, **kwargs):
        mode = "inspect" if args[0] == "inspect-local-pulse" else "run"
        events.append("inspect" if mode == "inspect" else "solve")
        requests.append(args.copy())
        assert callable(kwargs["process_runner"])
        raw = base64.b64decode(args[3].removeprefix("base64:"), validate=True)
        config = load_pulse_config(catalog.config)
        assert raw == properties_payload(config).encode("ascii")
        props = dict(line.split("=", 1) for line in raw.decode("ascii").splitlines())
        assert float(props["xc_m"]) == config["pulse"]["center_m"][0]
        assert float(props["direction_deg"]) == config["pulse"]["direction_deg"]
        if state.fault == "inspect_error" and mode == "inspect":
            raise ModelValidationError("injected inspect failure")
        response = make_pulse_evidence(config, mode, catalog.native_case)
        target = Path(args[2])
        target.write_bytes(("synthetic " + mode + " model").encode("ascii"))
        response.update(source_model=args[1], output_model=args[2],
                        properties_sha256=hashlib.sha256(raw).hexdigest())
        if state.fault == "wrong_payload":
            response["properties_sha256"] = "0" * 64
        if mode == "run":
            response["time_solver_log"] = "Step Time Stepsize\n0 180 - out\n" + "\n".join(
                f"{i} {180 + i * .05:.12g} 0.05" for i in range(1, 401))
            if state.fault == "bad_steps":
                response["time_solver_log"] = "Step Time Stepsize\n0 180 - out\n1 200 20\n"
        return response

    def export(model, directory, **kwargs):
        events.append("sample")
        assert kwargs["start"] == 180 and kwargs["stop"] == 200 and kwargs["step"] == .2
        fields = copy.deepcopy(catalog.fields)
        fields["metadata"].update(source_model=str(model), source_sha256=runner.sha(model).upper())
        catalog.controls[str(directory)] = fields
        _write(directory / "sequence_manifest.json", {"synthetic_fixture": True,
                                                       "source_sha256": runner.sha(model)})
        if state.fault == "checkpoint_changed_during_sample":
            catalog.checkpoint.write_bytes(b"injected checkpoint mutation after solve")
        return {"shape": fields["metadata"]["shape"]}

    def load(directory):
        return copy.deepcopy(catalog.controls[str(directory)])

    monkeypatch.setattr(runner.execution.bridge, "run_command", command)
    monkeypatch.setattr(runner.pulse_validation, "validate_pulse_readback", validate)
    monkeypatch.setattr(runner.execution, "export_full_field_sequence", export)
    monkeypatch.setattr(runner.execution, "load_full_field_sequence", load)
    monkeypatch.setattr(runner, "load_full_field_sequence", load)
    return state


def test_precheck_freezes_exact_config_properties_hash_and_small_input_identities(runner, catalog):
    report = runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert report == {"status": "PASS", "stage": "precheck", "time_solver_executed": False}
    plan = runner.read(catalog.output / "plan.json")
    assert (catalog.output / "config.input.json").read_bytes() == catalog.config.read_bytes()
    assert (catalog.output / "event.properties").read_bytes() == properties_payload(plan["config"]).encode("ascii")
    assert plan["properties_sha256"] == runner.sha(catalog.output / "event.properties")
    assert (catalog.output / "plan.sha256").read_text().strip() == runner.sha(catalog.output / "plan.json")
    assert plan["inputs"]["checkpoint_sha256"] == runner.sha(catalog.checkpoint)
    assert plan["inputs"]["prepare_sha256"] == runner.sha(catalog.prepare_path)
    before = {path.name: path.read_bytes() for path in catalog.output.iterdir()}
    runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert {path.name: path.read_bytes() for path in catalog.output.iterdir()} == before


def test_cli_precheck_reports_no_time_execution(runner, catalog, capsys):
    assert runner.main(["--config", str(catalog.config), "--output", str(catalog.output),
                        "--data-root", str(catalog.data), "--stage", "precheck"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "PASS" and report["stage"] == "precheck"
    assert not (catalog.output / "inspect").exists() and not (catalog.output / "solve").exists()


@pytest.mark.parametrize("value", [True, "0.005*sin(t)"])
def test_invalid_user_numbers_fail_before_output_publication(runner, catalog, value):
    config = load_pulse_config(catalog.config)
    config["pulse"]["acceleration_m_s2"] = value
    _write(catalog.config, config)
    with pytest.raises(InvalidParameterError):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert not catalog.output.exists()


@pytest.mark.parametrize("change", ["value", "raw_whitespace", "properties", "plan_hash"])
def test_resume_rejects_changed_config_or_frozen_bytes_without_overwrite(runner, catalog, change):
    runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    if change == "value":
        config = load_pulse_config(catalog.config)
        config["pulse"]["direction_deg"] = -90
        _write(catalog.config, config)
    elif change == "raw_whitespace":
        catalog.config.write_bytes(catalog.config.read_bytes() + b"\n")
    elif change == "properties":
        (catalog.output / "event.properties").write_bytes(b"event_id=changed\n")
    else:
        (catalog.output / "plan.sha256").write_text("0" * 64, encoding="ascii")
    before = {path.name: path.read_bytes() for path in catalog.output.iterdir()}
    with pytest.raises(ModelValidationError):
        runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert {path.name: path.read_bytes() for path in catalog.output.iterdir()} == before


@pytest.mark.parametrize("parent", ["config", "series", "index", "checkpoint", "prepare", "baseline", "zero"])
def test_output_cannot_be_nested_in_any_input_directory(runner, catalog, parent):
    directories = {"config": catalog.config.parent, "series": catalog.series_path.parent,
                   "index": catalog.index_path.parent, "checkpoint": catalog.checkpoint.parent,
                   "prepare": catalog.prepare_path.parent, "baseline": catalog.data / "baseline/fields",
                   "zero": catalog.data / "zero/fields"}
    output = directories[parent] / "nested_new_output"
    with pytest.raises(ModelValidationError, match="outside all input directories"):
        runner.run(catalog.config, output, catalog.data, "precheck")
    assert not output.exists()


def test_existing_unrelated_output_is_preserved(runner, catalog):
    catalog.output.mkdir(parents=True)
    marker = catalog.output / "previous.bin"
    marker.write_bytes(b"preserve")
    with pytest.raises((ModelValidationError, FileNotFoundError)):
        runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert list(catalog.output.iterdir()) == [marker] and marker.read_bytes() == b"preserve"


@pytest.mark.parametrize("fault", ["checkpoint", "manifest", "prepare_bytes", "catalogue", "index"])
def test_source_hash_failure_occurs_before_run_creation(runner, catalog, fault):
    paths = {"checkpoint": catalog.checkpoint, "manifest": catalog.data / "baseline/fields/sequence_manifest.json",
             "prepare_bytes": catalog.prepare_path, "catalogue": catalog.series_path, "index": catalog.index_path}
    paths[fault].write_bytes(paths[fault].read_bytes() + b" ")
    with pytest.raises(ModelValidationError):
        runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert not catalog.output.exists()


@pytest.mark.parametrize("fault", ["output_model", "output_sha256", "source_sha256_after"])
def test_prepare_semantic_binding_is_checked_after_container_repin(runner, catalog, fault):
    prepare = runner.read(catalog.prepare_path)
    prepare[fault] = str(catalog.source) if fault == "output_model" else "0" * 64
    _write(catalog.prepare_path, prepare)
    catalog.series["reference_files"][0]["sha256"] = runner.sha(catalog.prepare_path)
    _repin_series(runner, catalog)
    with pytest.raises(ModelValidationError):
        runner.run(catalog.config, catalog.output, catalog.data, "precheck")
    assert not catalog.output.exists()


@pytest.mark.parametrize("stage", ["sample", "analyze"])
def test_missing_solve_does_not_implicitly_start_inspect_or_time(runner, catalog, backend, stage):
    with pytest.raises(ModelValidationError, match="already validated"):
        runner.run(catalog.config, catalog.output, catalog.data, stage)
    assert backend.events == [] and not (catalog.output / "solve").exists()


@pytest.mark.parametrize("fault", ["inspect_error", "wrong_payload", "validator_fail"])
def test_inspect_failure_stops_before_solve_and_preserves_failure(runner, catalog, backend, fault):
    backend.fault = fault
    with pytest.raises(ModelValidationError):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert backend.events == ["inspect"]
    stage = catalog.output / "inspect"
    filename = "failure.json" if fault == "inspect_error" else "validation_failure.json"
    assert runner.read(stage / filename)["status"] == "FAIL"
    assert not (stage / "validation.json").exists()
    assert not (catalog.output / "solve").exists() and not (catalog.output / "analysis.json").exists()
    preserved = (stage / filename).read_bytes()
    with pytest.raises((ModelValidationError, FileNotFoundError)):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert backend.events == ["inspect"] and (stage / filename).read_bytes() == preserved


def test_full_entrypoint_uses_real_payload_validation_steps_and_order(runner, catalog, backend):
    report = runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert report["status"] == "PASS"
    assert backend.events == ["inspect", "solve", "sample"]
    assert backend.modes == ["inspect", "run"]
    assert runner.read(catalog.output / "solve/validation.json")["steps"]["accepted_step_count"] == 400
    plan = runner.read(catalog.output / "plan.json")
    for name in ("inspect", "solve"):
        response, model, record = runner.execution.completed_stage(catalog.output / name)
        assert record["source_sha256_before"] == record["source_sha256_after"] == plan["inputs"]["checkpoint_sha256"]
        assert record["output_sha256"] == runner.sha(model)
        assert response["properties_sha256"] == plan["properties_sha256"]
    again = runner.run(catalog.config, catalog.output, catalog.data, "analyze")
    assert again == report and backend.events == ["inspect", "solve", "sample"]


def test_all_exposed_parameters_reach_decoded_payload_and_actual_inspection(runner, catalog, backend):
    config = load_pulse_config(catalog.config)
    config["event_id"] = "all-fields-changed"
    config["pulse"] = {"center_m": [.22, .14], "direction_deg": -37.5,
                       "acceleration_m_s2": .0075, "sigma_m": .015,
                       "on_s": 182.2, "off_s": 184.6}
    _write(catalog.config, config)
    assert runner.run(catalog.config, catalog.output, catalog.data, "inspect")["status"] == "PASS"
    assert backend.events == ["inspect"] and backend.modes == ["inspect"]
    raw = base64.b64decode(backend.requests[0][3].removeprefix("base64:"), validate=True)
    assert raw == properties_payload(config).encode("ascii")
    assert runner.read(catalog.output / "plan.json")["config"] == config


def test_staged_inspect_solve_sample_analyze_never_reexecutes_completed_time(runner, catalog, backend):
    for stage, expected in (("inspect", ["inspect"]), ("solve", ["inspect", "solve"]),
                            ("sample", ["inspect", "solve", "sample"]),
                            ("analyze", ["inspect", "solve", "sample"])):
        assert runner.run(catalog.config, catalog.output, catalog.data, stage)["status"] == "PASS"
        assert backend.events == expected


def test_actual_step_failure_retains_readback_and_blocks_sampling(runner, catalog, backend):
    backend.fault = "bad_steps"
    with pytest.raises(ModelValidationError, match="step constraints"):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert backend.events == ["inspect", "solve"]
    assert (catalog.output / "solve/readback.json").is_file()
    assert runner.read(catalog.output / "solve/validation_failure.json")["status"] == "FAIL"
    assert not (catalog.output / "solve/validation.json").exists()


def test_analysis_fail_is_preserved_and_not_reported_as_success(runner, catalog, backend):
    # An absent synthetic response cannot meet the unchanged physical response floor.
    catalog.fields = copy.deepcopy(catalog.controls[str(catalog.data / "zero/fields")])
    with pytest.raises(ModelValidationError, match="Field response/restart"):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    assert backend.events == ["inspect", "solve", "sample"]
    assert runner.read(catalog.output / "analysis.json")["status"] == "FAIL"


@pytest.mark.parametrize("detectable_signal", [False, True])
def test_zero_amplitude_control_does_not_require_a_positive_response(runner, catalog, backend, detectable_signal):
    config = load_pulse_config(catalog.config)
    config["pulse"]["acceleration_m_s2"] = 0
    _write(catalog.config, config)
    if detectable_signal:
        with pytest.raises(ModelValidationError, match="Field response/restart"):
            runner.run(catalog.config, catalog.output, catalog.data, "all")
    else:
        catalog.fields = copy.deepcopy(catalog.controls[str(catalog.data / "zero/fields")])
        assert runner.run(catalog.config, catalog.output, catalog.data, "all")["status"] == "PASS"
    report = runner.read(catalog.output / "analysis.json")
    assert report["zero_control"]["passed"] is not detectable_signal
    assert report["status"] == ("FAIL" if detectable_signal else "PASS")


def test_late_checkpoint_change_cannot_leave_pass_analysis(runner, catalog, backend):
    backend.fault = "checkpoint_changed_during_sample"
    with pytest.raises(ModelValidationError, match="checkpoint"):
        runner.run(catalog.config, catalog.output, catalog.data, "all")
    report = catalog.output / "analysis.json"
    assert not report.exists() or runner.read(report)["status"] != "PASS"


@pytest.mark.parametrize("field", ["source_sha256", "source_model"])
def test_sample_cannot_be_relabelled_from_another_model(runner, catalog, backend, field):
    runner.run(catalog.config, catalog.output, catalog.data, "solve")
    runner.run(catalog.config, catalog.output, catalog.data, "sample")
    fields = catalog.controls[str(catalog.output / "sample_solve/fields")]
    fields["metadata"][field] = "0" * 64 if field == "source_sha256" else str(catalog.source)
    with pytest.raises(ModelValidationError, match="another model"):
        runner.run(catalog.config, catalog.output, catalog.data, "analyze")
    assert not (catalog.output / "analysis.json").exists()


@pytest.mark.parametrize("field", ["source_model", "source_sha256_before", "source_sha256_after", "operation"])
def test_completed_stage_must_still_bind_its_actual_checkpoint_and_operation(runner, catalog, backend, field):
    runner.run(catalog.config, catalog.output, catalog.data, "solve")
    path = catalog.output / "solve/execution.json"
    record = runner.read(path)
    record[field] = str(catalog.source) if field == "source_model" else "0" * 64
    _write(path, record)
    with pytest.raises(ModelValidationError):
        runner.run(catalog.config, catalog.output, catalog.data, "sample")
    assert backend.events == ["inspect", "solve"]


def test_unknown_stage_rejected_before_output_creation(runner, catalog):
    with pytest.raises(ModelValidationError, match="Unknown stage"):
        runner.run(catalog.config, catalog.output, catalog.data, "solve-everything")
    assert not catalog.output.exists()
