"""Forecast evaluation over real saved analytic NPZ fixtures, with no exporter."""
from __future__ import annotations

import hashlib
import json
import weakref
from copy import deepcopy

import numpy as np
import pytest

from hydro_disturb_bench import forecast_evaluation as evaluation
from hydro_disturb_bench.comsol_java.errors import InvalidParameterError, ModelValidationError
from hydro_disturb_bench.data_windows import TrajectoryRecord, WindowSpec, build_window_index
from tests.sequence_fixtures import write_saved_sequence


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def sequence_factory(tmp_path):
    def make(name, *, family="family", frames=10, shape=(17, 9), scale=1.):
        model = tmp_path / f"{name}.mph"
        model.write_bytes(name.encode())
        directory, manifest, arrays = write_saved_sequence(
            tmp_path / name, model, frames=frames, grid_shape=shape, batch_size=4)
        if scale != 1:
            arrays["values"] *= scale
            for batch in manifest["batches"]:
                target = directory / batch["path"]
                span = slice(batch["start_index"], batch["stop_index"])
                content = {k: v[span] if k in ("values", "mask", "invalid_mask", "times") else v
                           for k, v in arrays.items()}
                np.savez_compressed(target / "field_sample.npz", **content)
                metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
                metadata["npz_sha256"] = batch["npz_sha256"] = _sha(target / "field_sample.npz")
                (target / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                batch["metadata_sha256"] = _sha(target / "metadata.json")
            (directory / "sequence_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        record = TrajectoryRecord(name, family, directory, _sha(directory / "sequence_manifest.json"),
                                  hashlib.sha256(f"root:{family}".encode()).hexdigest())
        return record, arrays
    return make


def _index(records, assignments=None, spec=None):
    return build_window_index(records, assignments or {r.family_id: "train" for r in records},
                              spec or WindowSpec(3, 2, 2))


def test_real_sequences_pool_error_sums_and_disclose_unequal_weights(sequence_factory):
    one, a = sequence_factory("one", frames=10)
    two, b = sequence_factory("two", frames=14, shape=(9, 5), scale=2.)
    index = _index([one, two])
    before = deepcopy(index)
    report = evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")
    assert index == before
    assert report["status"] == "PASS" and report["heldout_evaluation"] is False
    assert report["training_performed"] is False
    assert report["weighting"]["trajectory_window_counts"] == {"one": 3, "two": 5}
    assert report["overall"]["forecast_frame_assignments"] == 16
    assert report["overall"]["unique_target_frames"] == 16
    assert report["overall"]["repeated_future_assignments"] == 0
    assert report["identity_audit"]["files_checked"] == 23
    assert report["identity_audit"]["inputs_unchanged"] is True
    assert not any(item["path"].endswith(".mph") for item in report["identity_audit"]["files"])
    metric = report["overall"]["methods"]["persistence"]
    counts = [np.count_nonzero(~arr["mask"][0]) * n for arr, n in ((a, 3), (b, 5))]
    leads = np.asarray(report["lead_seconds"])
    expected_sae = leads * (counts[0] + 2 * counts[1])
    expected_sse = leads**2 * (counts[0] + 4 * counts[1])
    for channel in range(3):
        np.testing.assert_allclose(np.asarray(metric["sae"])[:, channel], expected_sae, atol=1e-9)
        np.testing.assert_allclose(np.asarray(metric["sse"])[:, channel], expected_sse, atol=1e-9)
        np.testing.assert_allclose(np.asarray(metric["rmse"])[:, channel],
                                   np.sqrt(expected_sse / sum(counts)), atol=1e-12)
    for key in ("sae", "sse", "count"):
        total = sum(np.asarray(t["methods"]["persistence"][key]) for t in report["per_trajectory"])
        np.testing.assert_allclose(metric[key], total)
    mean_rmse = np.mean([t["methods"]["persistence"]["rmse"] for t in report["per_trajectory"]], axis=0)
    assert not np.allclose(metric["rmse"], mean_rmse)
    assert np.max(report["overall"]["methods"]["last_two_linear"]["rmse"]) < 1e-10
    json.dumps(report, allow_nan=False)


def test_future_overlap_counts_forecast_instances_and_keeps_trajectories_distinct(sequence_factory):
    records = [sequence_factory(name)[0] for name in ("one", "two")]
    report = evaluation.evaluate_baselines(_index(records, spec=WindowSpec(3, 3, 1)),
                                          split="train", mode="pipeline_smoke", methods=("persistence",))
    assert report["overall"]["window_count"] == 10
    assert report["overall"]["forecast_frame_assignments"] == 30
    assert report["overall"]["unique_target_frames"] == 14
    assert report["overall"]["repeated_future_assignments"] == 16
    assert report["overall"]["target_frames_seen_more_than_once"] == 10


@pytest.mark.parametrize("split,mode", [("validation", "heldout"), ("test", "heldout"),
                                       ("validation", "pipeline_smoke"), ("train", "heldout")])
def test_empty_or_wrong_mode_never_reads_npz(sequence_factory, monkeypatch, split, mode):
    index = _index([sequence_factory("one")[0]])

    def forbidden(*args, **kwargs):
        raise AssertionError("No NPZ read is allowed for an invalid/empty selection")

    monkeypatch.setattr(evaluation, "_input_files", forbidden)
    monkeypatch.setattr(evaluation, "read_window", forbidden)
    with pytest.raises(InvalidParameterError):
        evaluation.evaluate_baselines(index, split=split, mode=mode)


def test_heldout_requires_training_and_evaluates_only_independent_family(sequence_factory):
    train = sequence_factory("train", family="training")[0]
    test = sequence_factory("test", family="independent", scale=2.)[0]
    alone = _index([test], {"independent": "test"})
    with pytest.raises(InvalidParameterError, match="requires training"):
        evaluation.evaluate_baselines(alone, split="test", mode="heldout")
    index = _index([train, test], {"training": "train", "independent": "test"})
    report = evaluation.evaluate_baselines(index, split="test", mode="heldout")
    assert report["heldout_evaluation"] is True
    assert [t["trajectory_id"] for t in report["per_trajectory"]] == ["test"]
    assert {f["trajectory_id"] for f in report["identity_audit"]["files"]} == {"test"}


@pytest.mark.parametrize("methods", [(), ("unknown",), ("persistence", "persistence"),
                                     (lambda value: value,), "persistence"])
def test_only_registered_method_names_are_accepted(sequence_factory, methods):
    with pytest.raises(InvalidParameterError, match="registered method names"):
        evaluation.evaluate_baselines(_index([sequence_factory("one")[0]]),
                                      split="train", mode="pipeline_smoke", methods=methods)


def test_predictor_receives_only_history_and_schedule_and_target_changes_do_not_change_prediction(
        sequence_factory, monkeypatch):
    index = _index([sequence_factory("one")[0]])
    read, forecast = evaluation.read_window, evaluation.forecast_from_history
    current, predictions = {}, []

    def spy_read(*args):
        data = read(*args)
        data["future"] += current.get("target_offset", 0.)
        current["data"] = data
        return data

    def spy_forecast(*args, **kwargs):
        data = current["data"]
        assert len(args) == 4 and set(kwargs) == {"method"}
        for value, key in zip(args, ("history", "history_mask", "history_times", "future_times")):
            assert value is data[key]
        result = forecast(*args, **kwargs)
        predictions.append(result.copy())
        return result

    monkeypatch.setattr(evaluation, "read_window", spy_read)
    monkeypatch.setattr(evaluation, "forecast_from_history", spy_forecast)
    first = evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")
    first_predictions = predictions[:]
    predictions.clear()
    current["target_offset"] = 100.
    second = evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")
    assert first["overall"]["methods"] != second["overall"]["methods"]
    for a, b in zip(first_predictions, predictions, strict=True):
        assert np.array_equal(a, b, equal_nan=True)


@pytest.mark.parametrize("fault", ["lead", "mask_dynamic", "mask_between_windows", "shape"])
def test_changed_window_contract_is_rejected(sequence_factory, monkeypatch, fault):
    index = _index([sequence_factory("one")[0]])
    read = evaluation.read_window
    calls = 0

    def altered(*args):
        nonlocal calls
        data = read(*args)
        calls += 1
        if calls == 2:
            if fault == "lead":
                data["future_times"] += .01
            elif fault == "mask_dynamic":
                data["future_mask"][0, 0, 0] = True
            elif fault == "shape":
                data["future"] = data["future"][:, :, :, :-1]
            else:
                for part in ("history", "future"):
                    data[part + "_mask"][:, 0, 0] = True
                    data[part][:, :, 0, 0] = np.nan
        return data

    monkeypatch.setattr(evaluation, "read_window", altered)
    with pytest.raises(ModelValidationError):
        evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")


@pytest.mark.parametrize("fault", ["index", "manifest", "metadata", "npz"])
def test_input_identity_changes_during_evaluation_fail(sequence_factory, monkeypatch, fault):
    record, _ = sequence_factory("one", frames=5)
    index = _index([record])
    forecast = evaluation.forecast_from_history

    def mutate(*args, **kwargs):
        result = forecast(*args, **kwargs)
        if fault == "index":
            index["spec"]["stride"] = 99
        else:
            path = record.sequence_dir / {"manifest": "sequence_manifest.json",
                                           "metadata": "batch_0000/metadata.json",
                                           "npz": "batch_0000/field_sample.npz"}[fault]
            with path.open("ab") as stream:
                stream.write(b"\n")
        return result

    monkeypatch.setattr(evaluation, "forecast_from_history", mutate)
    with pytest.raises(ModelValidationError):
        evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke", methods=("persistence",))


def test_bad_index_digest_fails_before_reading(sequence_factory, monkeypatch):
    index = _index([sequence_factory("one")[0]])
    index["windows"][0]["future_start"] += 1
    monkeypatch.setattr(evaluation, "read_window", lambda *args: pytest.fail("Should reject index first"))
    with pytest.raises(ModelValidationError, match="digest"):
        evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")


def test_previous_window_arrays_are_released_before_next_read(sequence_factory, monkeypatch):
    index = _index([sequence_factory("one")[0]])
    read, prior = evaluation.read_window, []

    def observe(*args):
        assert all(reference() is None for reference in prior)
        data = read(*args)
        prior[:] = [weakref.ref(value) for value in data.values()]
        return data

    monkeypatch.setattr(evaluation, "read_window", observe)
    evaluation.evaluate_baselines(index, split="train", mode="pipeline_smoke")
    assert all(reference() is None for reference in prior)
