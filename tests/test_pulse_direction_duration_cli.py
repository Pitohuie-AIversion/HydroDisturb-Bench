"""Read-only comparison admission with real file hashes and analytic fields."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydro_disturb_bench import branch_pulse
from hydro_disturb_bench.comsol_java.errors import ModelValidationError
from tests.test_pulse_variant_comparison import study as analytic_study

REPO = Path(__file__).resolve().parents[1]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


@pytest.fixture
def case(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("pulse_direction_comparison_cli", REPO / "scripts/compare_m3_pulse_direction_duration.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    repo, data = tmp_path / "repo", tmp_path / "data"
    monkeypatch.setattr(cli, "ROOT", repo)
    baseline, zero, variants, plan = analytic_study.__wrapped__()
    fields, pinned = {}, []

    def add(path, value):
        _write(path, value)
        pinned.append({"path": path.relative_to(data).as_posix(), "sha256": cli.sha(path)})

    source, checkpoint = data / "source.mph", data / "checkpoint.mph"
    for path in (source, checkpoint):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode("ascii"))
        pinned.append({"path": path.name, "sha256": cli.sha(path)})
    prepare = {"source_model": str(source), "output_model": str(checkpoint), "output_sha256": cli.sha(checkpoint)}
    prepare_path = data / "reference/prepare/readback.json"
    add(prepare_path, prepare)
    add(data / "reference/analysis/report.json",
        {key: "PASS" for key in ("status", "native_status", "field_status", "step_status")})

    def sample(directory, model, values, original=False):
        values = deepcopy(values)
        values["metadata"].update(source_model=str(model), source_sha256=cli.sha(model).upper())
        fields[str(directory.resolve())] = values
        writer = add if original else _write
        writer(directory / "sequence_manifest.json", {"test_fixture": directory.name})
        writer(directory.parent / "execution.json", {"status": "COMPLETE", "source_model": str(model),
               "source_sha256": cli.sha(model), "manifest_sha256": cli.sha(directory / "sequence_manifest.json")})
        return {"sequence_dir": str(directory), "manifest_sha256": cli.sha(directory / "sequence_manifest.json"),
                "source_sha256": cli.sha(model)}

    def stage(directory, operation, properties=None, config=None, original=False):
        directory.mkdir(parents=True)
        model = directory / "model.mph"
        model.write_bytes(str(directory).encode("utf-8"))
        response = {"source_model": str(checkpoint), "output_model": str(model)}
        command = [operation.replace("_", "-"), str(checkpoint), str(model)]
        if properties is not None:
            response.update(properties_sha256=cli.hashlib.sha256(properties).hexdigest(), actual_pulse=config["pulse"])
            command.append("base64:" + base64.b64encode(properties).decode("ascii"))
        writer = add if original else _write
        writer(directory / "readback.json", response)
        writer(directory / "execution.json", {"status": "COMPLETE", "operation": operation, "command": command,
               "source_model": str(checkpoint), "source_sha256_before": cli.sha(checkpoint),
               "source_sha256_after": cli.sha(checkpoint), "output_model": str(model), "output_sha256": cli.sha(model),
               "readback_sha256": cli.sha(directory / "readback.json")})
        return model

    controls = {"baseline": sample(data / "baseline", source, baseline, original=True)}
    references = {}
    for name, key, values in (("zero", "zero", zero), ("pulse", "a0050", variants["default"])):
        model = stage(data / f"reference/{name}", "run_pulse_branch", original=True)
        directory = data / f"reference/sample_{name}/fields"
        row = sample(directory, model, values, original=True)
        references[key] = {"stage_dir": f"reference/{name}", "fields_dir": f"reference/sample_{name}/fields",
                           "amplitude_m_s2": 0. if name == "zero" else .005}
        if name == "zero":
            controls["zero"] = row
    series = {"checkpoint": {"path": "checkpoint.mph", "sha256": cli.sha(checkpoint)},
              "source_model": {"path": "source.mph", "sha256": cli.sha(source)},
              "reference_run": "reference", "baseline_fields": "baseline", "references": references,
              "reference_files": pinned}
    series_path = repo / "series.json"
    _write(series_path, series)
    monkeypatch.setattr(cli, "SERIES_PATH", series_path)
    monkeypatch.setattr(cli, "SERIES_SHA", cli.sha(series_path))
    validation = {"status": "PASS", "prescribed_source_diagnostics": {"passed": True},
                  "actual_saved_time_source": {"passed": True}}
    step_validation = {"status": "PASS", "accepted_step_count": 450, "dt_max_s": .05}
    calls = []

    def validate(response, config, *, mode, prepare):
        calls.append(mode)
        assert response["actual_pulse"] == config["pulse"]
        return deepcopy(validation)

    monkeypatch.setattr(cli.pulse_validation, "validate_pulse_readback", validate)
    monkeypatch.setattr(cli, "validate_branch_steps", lambda *args: deepcopy(step_validation))
    monkeypatch.setattr(cli, "load_full_field_sequence", lambda path: deepcopy(fields[str(path.resolve())]))
    study = {"study_id": "synthetic-pipeline", "frozen_at_utc": "2026-11-09T00:00:00Z",
             "checkpoint": series["checkpoint"], "default_reference": references["a0050"],
             "zero_reference": references["zero"], "baseline_fields": "baseline", "common_analysis_plan": plan,
             "cases": {}}
    for name in cli.NEW_CASES:
        config = json.loads((REPO / f"configs/m3_pulse_direction_duration/{name}.json").read_text())
        config_path, output = repo / f"configs/{name}.json", data / f"runs/{name}"
        _write(config_path, config)
        _write(output / "config.input.json", config)
        properties = cli.pulse_parameters.properties_payload(config).encode("ascii")
        (output / "event.properties").write_bytes(properties)
        run_plan = {"config": config, "config_sha256": cli.sha(config_path),
                    "properties_sha256": cli.hashlib.sha256(properties).hexdigest(), "analysis_plan": plan,
                    "inputs": {"checkpoint": str(checkpoint), "checkpoint_sha256": cli.sha(checkpoint),
                               "prepare_readback": str(prepare_path), "prepare_sha256": cli.sha(prepare_path),
                               "series_sha256": cli.SERIES_SHA, "controls": controls}}
        _write(output / "plan.json", run_plan)
        (output / "plan.sha256").write_text(cli.sha(output / "plan.json") + "\n")
        for mode in ("inspect", "solve"):
            operation = "inspect_local_pulse" if mode == "inspect" else "run_local_pulse"
            model = stage(output / mode, operation, properties, config)
            stored = deepcopy(validation)
            if mode == "solve":
                stored["steps"] = step_validation
            _write(output / mode / "validation.json", stored)
        sample(output / "sample_solve/fields", model, variants[name])
        individual = branch_pulse.compare_branch_fields(baseline, zero, variants[name], plan)
        individual.update(config_sha256=run_plan["config_sha256"], source_validation=stored)
        _write(output / "analysis.json", individual)
        study["cases"][name] = {"config_path": f"configs/{name}.json", "config": config,
                                "config_sha256": cli.sha(config_path), "run_dir": f"runs/{name}",
                                "run_plan_sha256": cli.sha(output / "plan.json")}
    study_path = repo / "study/preregistered_plan.json"
    _write(study_path, study)
    _write(study_path.with_name("freeze_record.json"), {"plan_sha256": cli.sha(study_path),
           "frozen_at_utc": study["frozen_at_utc"], "time_solver_executed": False})
    return SimpleNamespace(cli=cli, repo=repo, data=data, plan=study_path, sha=cli.sha(study_path),
                           fields=fields, calls=calls, validation=validation, steps=step_validation)


def test_collection_verifies_files_and_uses_actual_numeric_comparison(case):
    report = case.cli.collect_comparison(case.plan, case.sha, case.data)
    assert report["status"] == "PASS"
    assert case.calls == ["inspect", "run"] * 3
    assert report["cases"]["default"]["common_window_peak_roi_over_U"] == pytest.approx(.02)
    assert report["cases"]["long"]["common_window_peak_roi_over_U"] == pytest.approx(.04)
    assert report["against_default"]["short"]["common_window_peak_roi_over_U"] == pytest.approx(.01)
    assert report["case_evidence"]["reverse"]["actual_pulse"]["direction_deg"] == -90
    assert report["case_evidence"]["long"]["steps"]["accepted_step_count"] == 450
    assert len(report["input_file_sha256"]) > 45
    assert len(json.dumps(report, allow_nan=False)) < 100_000


@pytest.mark.parametrize("target", ["plan", "freeze", "reference", "config", "properties", "model", "validation", "analysis"])
def test_changed_or_incomplete_evidence_is_rejected(case, target):
    cli = case.cli
    output = case.data / "runs/reverse"
    paths = {"plan": case.plan, "freeze": case.plan.with_name("freeze_record.json"),
             "reference": case.data / "reference/pulse/readback.json", "config": output / "config.input.json",
             "properties": output / "event.properties", "model": output / "solve/model.mph",
             "validation": output / "solve/validation.json", "analysis": output / "analysis.json"}
    path = paths[target]
    if target in ("freeze", "validation", "analysis"):
        value = cli.read(path)
        value["plan_sha256" if target == "freeze" else "status"] = "invalid"
        _write(path, value)
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ModelValidationError):
        cli.collect_comparison(case.plan, case.sha, case.data)


@pytest.mark.parametrize("target", ["baseline_sha", "default_sha", "new_sha", "new_path"])
def test_loader_source_binding_is_enforced(case, target):
    relative = {"baseline_sha": "baseline", "default_sha": "reference/sample_pulse/fields",
                "new_sha": "runs/reverse/sample_solve/fields", "new_path": "runs/reverse/sample_solve/fields"}[target]
    metadata = case.fields[str((case.data / relative).resolve())]["metadata"]
    metadata["source_model" if target == "new_path" else "source_sha256"] = "another_model" if target == "new_path" else "0" * 64
    with pytest.raises(ModelValidationError, match="source|another"):
        case.cli.collect_comparison(case.plan, case.sha, case.data)


@pytest.mark.parametrize("target", ["native", "steps"])
def test_recomputed_failure_is_not_overridden_by_saved_pass(case, target):
    (case.validation if target == "native" else case.steps)["status"] = "FAIL"
    with pytest.raises(ModelValidationError):
        case.cli.collect_comparison(case.plan, case.sha, case.data)


def test_cli_writes_exclusively_and_returns_status(case, tmp_path):
    destination = tmp_path / "report.json"
    args = ["--plan", str(case.plan), "--expected-sha256", case.sha, "--data-root", str(case.data),
            "--output", str(destination)]
    assert case.cli.main(args) == 0
    raw = destination.read_bytes()
    with pytest.raises(ModelValidationError, match="already exists"):
        case.cli.main(args)
    assert destination.read_bytes() == raw


def test_frozen_configs_keep_bytes_when_git_autocrlf_is_enabled(tmp_path):
    """Both Git blobs and a fresh index checkout retain the frozen LF bytes."""
    repository = tmp_path / "git_round_trip"
    repository.mkdir()

    def git(*arguments):
        return subprocess.run(["git", *arguments], cwd=repository, check=True,
                              capture_output=True).stdout

    git("init", "--quiet")
    git("config", "core.autocrlf", "true")
    (repository / ".gitattributes").write_bytes((REPO / ".gitattributes").read_bytes())
    original = {}
    for source in sorted((REPO / "configs/m3_pulse_direction_duration").glob("*.json")):
        relative = source.relative_to(REPO)
        raw = source.read_bytes()
        assert b"\r" not in raw and b"\n" in raw
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        original[relative.as_posix()] = raw
    assert len(original) == 3
    git("add", ".gitattributes", *original)
    for relative, raw in original.items():
        blob = git("show", ":" + relative)
        assert hashlib.sha256(blob).digest() == hashlib.sha256(raw).digest()
        (repository / relative).unlink()
    git("checkout-index", "--all")
    for relative, raw in original.items():
        checked_out = (repository / relative).read_bytes()
        assert checked_out == raw
        assert b"\r" not in checked_out
