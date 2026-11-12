"""Registered-data entrypoint tests with actual small sequence files, no COMSOL."""
from __future__ import annotations

import importlib.util
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from hydro_disturb_bench.field_sequence import load_full_field_sequence
from tests.sequence_fixtures import write_saved_sequence

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_m3_data_usage", ROOT / "scripts/prepare_m3_data_usage.py")
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


@pytest.fixture
def registered_case(tmp_path, monkeypatch):
    config = json.loads((ROOT / "configs/m3_data_usage.json").read_text(encoding="utf-8"))
    plan = json.loads((ROOT / config["inputs"]["series_plan"]["path"]).read_text(encoding="utf-8"))
    inputs = {}

    def pin(key, relative, value):
        path = tmp_path / relative
        write(path, value)
        inputs[key] = {"scope": "data", "path": relative, "sha256": usage.sha(path)}

    model = tmp_path / "models/root.mph"
    model.parent.mkdir()
    model.write_bytes(b"small offline solved-model fixture")
    template = tmp_path / "template"
    write_saved_sequence(template, model, start=170., frames=20, step=.2,
                         grid_shape=(17, 9), batch_size=8)
    plan["source_model"] = {"path": "models/root.mph", "sha256": usage.sha(model)}
    plan["checkpoint"] = {"path": "models/checkpoint.mph", "sha256": "c" * 64}
    plan["baseline_fields"] = "sequences/baseline"
    for key in ("base_plan", "restart_protocol"):
        value = json.loads((ROOT / config["inputs"][key]["path"]).read_text(encoding="utf-8"))
        pin(key, f"contracts/{key}.json", value)
        plan[key]["sha256"] = inputs[key]["sha256"]
    pin("series_plan", "contracts/series_plan.json", plan)
    monkeypatch.setattr(usage, "SERIES_PLAN_SHA", inputs["series_plan"]["sha256"])
    checkpoint = str(tmp_path / plan["checkpoint"]["path"])
    prepare = {"status": "COMPLETE", "operation": "prepare_branch_state",
               "source_model": str(model), "output_model": checkpoint,
               "source_sha256_before": usage.sha(model), "source_sha256_after": usage.sha(model),
               "output_sha256": plan["checkpoint"]["sha256"]}
    pin("prepare_execution", "prepare/execution.json", prepare)

    def vectors(keys, derivative=False):
        identity = plan["native_identity"]
        return {key: {**({"available": True} if derivative else {}),
                      "solution_tag": "sol1" if key == "source" else "sol_branch_seed",
                      "solnum": 901, "time": 180., "count": identity["native_dof_count"],
                      "dtype": "float64", "byte_order": "little",
                      "sha256": identity["native_derivative_sha256" if derivative
                                         else "native_state_sha256"]} for key in keys}

    pin("prepare_vectors", "prepare/native_vectors.json", vectors(("source", "seed")))
    pin("prepare_derivatives", "prepare/native_derivative_vectors.json",
        vectors(("source", "seed"), True))
    for row in config["trajectories"]:
        name = row["trajectory_id"]
        row["sequence_dir"] = f"sequences/{name}"
        directory = tmp_path / row["sequence_dir"]
        shutil.copytree(template, directory)
        branch_model = model if name == "baseline" else tmp_path / f"models/{name}.mph"
        if branch_model != model:
            branch_model.write_bytes(name.encode())
        manifest = json.loads((directory / "sequence_manifest.json").read_text(encoding="utf-8"))
        manifest.update(source_model=str(branch_model), source_sha256=usage.sha(branch_model))
        for batch in manifest["batches"]:
            for filename, hashkey in (("metadata.json", "metadata_sha256"),
                                      ("bridge_response.json", "response_sha256")):
                path = directory / batch["path"] / filename
                value = json.loads(path.read_text(encoding="utf-8"))
                if filename == "metadata.json":
                    value.update(source_model=str(branch_model), source_sha256=usage.sha(branch_model))
                else:
                    value["model_path"] = str(branch_model)
                write(path, value)
                batch[hashkey] = usage.sha(path)
        pin(row["manifest_input"], row["sequence_dir"] + "/sequence_manifest.json", manifest)
        if name != "baseline":
            new = name in ("a0025", "a0100")
            command = ["run-pulse-series-branch" if new else "run-pulse-branch", checkpoint,
                       str(branch_model), str(row["amplitude_m_s2"]), plan["restart_protocol"]["id"]]
            if new:
                command.append("m3-pulse-amplitude-series-v1")
            execution = {"status": "COMPLETE", "operation": "run_pulse_branch", "command": command,
                         "source_model": checkpoint, "output_model": str(branch_model),
                         "source_sha256_before": plan["checkpoint"]["sha256"],
                         "source_sha256_after": plan["checkpoint"]["sha256"],
                         "output_sha256": usage.sha(branch_model)}
            pin(name + "_execution", f"{name}/execution.json", execution)
            pin(name + "_vectors", f"{name}/native_vectors.json", vectors(("seed", "input")))
            pin(name + "_derivatives", f"{name}/native_derivative_vectors.json",
                vectors(("seed", "input"), True))
    config["inputs"] = inputs
    path = tmp_path / "config.json"
    write(path, config)
    return path, tmp_path, config


def test_registered_entry_writes_only_small_index_and_audits(registered_case, monkeypatch):
    path, root, _ = registered_case
    monkeypatch.setattr(usage, "_reference_loader", lambda ref: (load_full_field_sequence, "f" * 64))
    report = usage.prepare_usage(path, root, root / "output", "a" * 40)
    assert report["status"] == "PASS"
    assert report["counts"]["windows"] == 15
    assert report["counts"]["trajectories"] == 5
    assert report["counts"]["by_split"]["validation"]["windows"] == 0
    assert report["counts"]["by_split"]["test"]["windows"] == 0
    audit = report["reference_comparison"]
    assert audit["batches"] == 15 and audit["windows"] == 15
    assert audit["cross_batch_windows"] > 0
    assert len(audit["tail_checks"]) == 5
    assert audit["max_absolute_difference"] == 0
    assert {p.name for p in (root / "output").iterdir()} == {
        "index.json", "lineage_audit.json", "report.json"}
    lineage = json.loads((root / "output/lineage_audit.json").read_text(encoding="utf-8"))
    assert lineage["inputs_unchanged"] is True


@pytest.mark.parametrize("fault", [
    "missing_pin", "wrong_checkpoint", "wrong_output", "wrong_native_derivative",
    "derivative_unavailable", "wrong_native_time", "wrong_baseline", "wrong_amplitude",
    "wrong_protocol", "split",
])
def test_lineage_failure_precedes_reader_and_keeps_only_failure(registered_case, monkeypatch, fault):
    path, root, config = registered_case
    key = "a0025_execution"
    if fault == "split":
        config["family_assignment"] = "test"
    else:
        key = {"wrong_native_derivative": "a0025_derivatives", "derivative_unavailable": "a0025_derivatives",
               "wrong_native_time": "a0025_vectors",
               "wrong_baseline": "baseline_manifest"}.get(fault, key)
        input_path = root / config["inputs"][key]["path"]
        value = json.loads(input_path.read_text(encoding="utf-8"))
        if fault == "wrong_checkpoint":
            value["source_sha256_after"] = "f" * 64
        elif fault == "wrong_output":
            value["output_sha256"] = "f" * 64
        elif fault == "wrong_native_derivative":
            value["input"]["sha256"] = "f" * 64
        elif fault == "derivative_unavailable":
            value["input"]["available"] = False
        elif fault == "wrong_native_time":
            value["input"]["time"] = 181.
        elif fault == "wrong_baseline":
            value["source_sha256"] = "f" * 64
        elif fault == "wrong_amplitude":
            value["command"][3] = "0.005"
        elif fault == "wrong_protocol":
            value["command"][4] = "unregistered"
        else:
            value["status"] = "tampered"
        write(input_path, value)
        if fault != "missing_pin":
            config["inputs"][key]["sha256"] = usage.sha(input_path)
    write(path, config)

    def forbidden(*args, **kwargs):
        raise AssertionError("Field reader must not run after invalid lineage")

    monkeypatch.setattr(usage, "build_window_index", forbidden)
    with pytest.raises(ValueError):
        usage.prepare_usage(path, root, root / "failed")
    assert [p.name for p in (root / "failed").iterdir()] == ["failure.json"]


def test_existing_output_is_never_modified(tmp_path):
    output = tmp_path / "old"
    output.mkdir()
    sentinel = output / "report.json"
    sentinel.write_bytes(b"accepted old report")
    with pytest.raises(FileExistsError):
        usage.prepare_usage(tmp_path / "missing.json", tmp_path, output)
    assert sentinel.read_bytes() == b"accepted old report"
    assert len(list(output.iterdir())) == 1


def test_output_inside_input_is_rejected_before_directory_creation(registered_case):
    path, root, config = registered_case
    sequence = root / config["trajectories"][0]["sequence_dir"]
    before = {p.relative_to(sequence) for p in sequence.rglob("*")}
    with pytest.raises(ValueError, match="inside an existing input"):
        usage.prepare_usage(path, root, sequence / "batch_audit")
    assert {p.relative_to(sequence) for p in sequence.rglob("*")} == before
    assert load_full_field_sequence(sequence)["values"].shape[0] == 20


def test_changed_input_during_window_build_prevents_publication(registered_case, monkeypatch):
    path, root, _ = registered_case
    build = usage.build_window_index

    def mutate(*args):
        index = build(*args)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return index

    monkeypatch.setattr(usage, "build_window_index", mutate)
    with pytest.raises(ValueError, match="Inputs changed"):
        usage.prepare_usage(path, root, root / "failed")
    assert [p.name for p in (root / "failed").iterdir()] == ["failure.json"]


def test_reference_comparison_rejects_changed_field_and_retains_failure(registered_case, monkeypatch):
    path, root, _ = registered_case

    def altered(directory):
        data = deepcopy(load_full_field_sequence(directory))
        data["values"][0, 0, 0, 0] += .1
        return data

    monkeypatch.setattr(usage, "_reference_loader", lambda ref: (altered, "f" * 64))
    with pytest.raises(ValueError, match="Reference mismatch"):
        usage.prepare_usage(path, root, root / "failed", "a" * 40)
    assert [p.name for p in (root / "failed").iterdir()] == ["failure.json"]


def test_reference_loader_requires_immutable_full_ref():
    with pytest.raises(ValueError, match="immutable Git commit"):
        usage._reference_loader("main")
