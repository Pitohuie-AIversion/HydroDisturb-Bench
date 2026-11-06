"""Exercise the registered CLI on saved analytic data, without COMSOL."""
from __future__ import annotations

import hashlib
import json

import pytest

from hydro_disturb_bench.data_windows import (
    TrajectoryRecord,
    WindowSpec,
    build_window_index,
    save_window_index,
)
from scripts import evaluate_m3_baselines as cli
from tests.sequence_fixtures import write_saved_sequence


@pytest.fixture
def registered(tmp_path, monkeypatch):
    model = tmp_path / "source.mph"
    model.write_bytes(b"synthetic input identity; no model is loaded")
    sequence, _, _ = write_saved_sequence(tmp_path / "sequence", model, frames=11)
    record = TrajectoryRecord("one", "family", sequence,
                              cli.file_sha256(sequence / "sequence_manifest.json"), "a" * 64)
    index = build_window_index([record], {"family": "train"}, WindowSpec(5, 5, 5))
    index_path = tmp_path / "catalog/index.json"
    index_path.parent.mkdir()
    save_window_index(index, index_path)
    identity = cli.file_sha256(index_path)
    config = {"schema_version": 1, "operation": "m3_forecast_baseline_smoke",
              "mode": "pipeline_smoke", "split": "train", "methods": cli.METHODS,
              "index": {"path": "catalog/index.json", "sha256": identity},
              "expected_window_spec": index["spec"], "expected_counts": index["counts"]}
    config_path = tmp_path / "configs/config.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "INDEX_SHA256", identity)
    return config_path, tmp_path / "result", index_path, sequence


def test_registered_entry_evaluates_saved_data_and_writes_small_json_only(registered):
    config, output, index, sequence = registered
    before = {path: cli.file_sha256(path) for path in sequence.rglob("*") if path.is_file()}
    report = cli.run_evaluation(config, output)
    assert report["overall"]["window_count"] == 1
    assert report["registered_input"]["index_sha256"] == hashlib.sha256(index.read_bytes()).hexdigest()
    assert report == json.loads((output / "report.json").read_text(encoding="utf-8"))
    execution = json.loads((output / "execution.json").read_text(encoding="utf-8"))
    assert execution["source_files_unchanged"] and execution["prediction_tensor_files"] == 0
    assert not execution["comsol_started"] and not execution["fitted_model"]
    assert {p.name for p in output.iterdir()} == {"report.json", "execution.json"}
    assert before == {path: cli.file_sha256(path) for path in before}


def test_existing_output_is_preserved(registered):
    config, output, _, _ = registered
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        cli.run_evaluation(config, output)
    assert sentinel.read_bytes() == b"keep" and len(list(output.iterdir())) == 1


@pytest.mark.parametrize("destination", ["sequence", "config", "index"])
def test_output_inside_inputs_is_rejected_before_creation(registered, destination):
    config, _, index, sequence = registered
    output = {"sequence": sequence, "config": config.parent, "index": index.parent}[destination] / "audit"
    with pytest.raises(ValueError, match="Output must not"):
        cli.run_evaluation(config, output)
    assert not output.exists()


@pytest.mark.parametrize("key,value", [("mode", "heldout"), ("split", "test"),
                                        ("methods", ["last_two_linear"]), ("schema_version", True)])
def test_unregistered_config_is_rejected(registered, key, value):
    config, output, _, _ = registered
    content = json.loads(config.read_text(encoding="utf-8"))
    content[key] = value
    config.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(ValueError):
        cli.run_evaluation(config, output)
    assert not output.exists()


def test_index_mutation_during_execution_cannot_leave_success(registered, monkeypatch):
    config, output, index_path, _ = registered
    actual_evaluator = cli.evaluate_baselines

    def mutate(index, **kwargs):
        report = actual_evaluator(index, **kwargs)
        with index_path.open("ab") as stream:
            stream.write(b"\n")
        return report

    monkeypatch.setattr(cli, "evaluate_baselines", mutate)
    with pytest.raises(ValueError, match="changed during execution"):
        cli.run_evaluation(config, output)
    assert {p.name for p in output.iterdir()} == {"failure.json"}
    assert json.loads((output / "failure.json").read_text())["status"] == "FAIL"


def test_evaluator_failure_is_preserved_without_success(registered, monkeypatch):
    config, output, _, _ = registered

    def fail(*args, **kwargs):
        raise RuntimeError("injected read failure")

    monkeypatch.setattr(cli, "evaluate_baselines", fail)
    with pytest.raises(RuntimeError, match="injected read failure"):
        cli.run_evaluation(config, output)
    assert {p.name for p in output.iterdir()} == {"failure.json"}
    assert "injected read failure" in (output / "failure.json").read_text()


def test_index_change_after_loading_is_rejected_before_evaluation(registered, monkeypatch):
    config, output, index_path, _ = registered
    loader = cli.load_window_index

    def mutate(*args, **kwargs):
        result = loader(*args, **kwargs)
        with index_path.open("ab") as stream:
            stream.write(b"\n")
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("Changed index must not reach evaluation")

    monkeypatch.setattr(cli, "load_window_index", mutate)
    monkeypatch.setattr(cli, "evaluate_baselines", forbidden)
    with pytest.raises(ValueError, match="Index changed during admission"):
        cli.run_evaluation(config, output)
    assert {p.name for p in output.iterdir()} == {"failure.json"}
