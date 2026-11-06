"""Window admission over real temporary sequence files, without COMSOL calls."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest
from sequence_fixtures import write_saved_sequence

from hydro_disturb_bench import data_windows as windows
from hydro_disturb_bench.comsol_java import bridge
from hydro_disturb_bench.field_sequence import (
    FieldSequenceReader,
    load_full_field_sequence,
)


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _rehash(index):
    index.pop("payload_sha256", None)
    index["payload_sha256"] = hashlib.sha256(json.dumps(
        index, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode()).hexdigest()


@pytest.fixture
def sequence_factory(tmp_path):
    """Construct saved analytic inputs; exercise real NPZ parsing and readers."""
    def create(name="one", *, family=None, start=170.0, frames=10):
        model = tmp_path / f"{name}.mph"
        model.write_bytes(f"distinct native source fixture {name}".encode())
        directory = tmp_path / name
        write_saved_sequence(directory, model, start=start, frames=frames,
                             grid_shape=(17, 9), batch_size=4)
        manifest_sha = hashlib.sha256((directory / "sequence_manifest.json").read_bytes()).hexdigest()
        return windows.TrajectoryRecord(name, family or name, directory, manifest_sha,
                                        _sha("root:" + name), _sha("checkpoint:" + name),
                                        _sha("native:" + name))
    return create


@pytest.mark.parametrize("field,value", [
    ("history_frames", True), ("forecast_frames", False), ("stride", True),
    ("history_frames", 0), ("forecast_frames", -1), ("stride", 0),
    ("history_frames", 1.5), ("forecast_frames", "2"), ("stride", None),
])
def test_window_spec_rejects_invalid_frame_counts(field, value):
    values = {"history_frames": 3, "forecast_frames": 2, "stride": 1, field: value}
    with pytest.raises(bridge.InvalidParameterError):
        windows.WindowSpec(**values)


def test_specs_and_records_are_immutable_and_hashes_are_validated(sequence_factory):
    record = sequence_factory()
    with pytest.raises(FrozenInstanceError):
        record.family_id = "another"
    spec = windows.WindowSpec(np.int64(3), 2)
    assert type(spec.history_frames) is int
    with pytest.raises(FrozenInstanceError):
        spec.stride = 2
    assert replace(record, manifest_sha256=record.manifest_sha256.upper()).manifest_sha256 == record.manifest_sha256
    for field in ("manifest_sha256", "root_model_sha256", "checkpoint_sha256", "native_state_sha256"):
        with pytest.raises(bridge.InvalidParameterError):
            replace(record, **{field: "g" * 64})
    with pytest.raises(bridge.InvalidParameterError):
        replace(record, sequence_dir=record.sequence_dir / "missing")


def test_explicit_independent_families_and_tail_aligned_windows(sequence_factory):
    records = [sequence_factory("train"), sequence_factory("val", start=172),
               sequence_factory("test", start=174)]
    assignments = {"train": "train", "val": "validation", "test": "test"}
    index = windows.build_window_index(records, assignments, windows.WindowSpec(3, 2, 5))
    assert index["splits"] == {"train": ["train"], "validation": ["val"], "test": ["test"]}
    assert index["counts"]["windows"] == 6
    for record in records:
        items = [w for w in index["windows"] if w["trajectory_id"] == record.trajectory_id]
        assert [(w["history_start"], w["history_stop"], w["future_start"], w["future_stop"])
                for w in items] == [(0, 3, 3, 5), (5, 8, 8, 10)]
    assert index["validation_scope"] == {
        "at_build": "all_batches", "at_read": "accessed_batches", "ancestry": "caller_supplied_not_inferred"}
    assert windows.validate_window_index(index) == index


def test_one_family_stays_in_one_split_without_inventing_validation_data(sequence_factory):
    records = [sequence_factory("a", family="shared"),
               sequence_factory("b", family="shared", start=172)]
    records[1] = replace(records[1], root_model_sha256=records[0].root_model_sha256)
    index = windows.build_window_index(records, {"shared": "train"}, windows.WindowSpec(3, 2, 2))
    assert index["counts"]["by_split"] == {
        "train": {"trajectories": 2, "windows": 6},
        "validation": {"trajectories": 0, "windows": 0},
        "test": {"trajectories": 0, "windows": 0}}
    assert index["splits"]["validation"] == index["splits"]["test"] == []


@pytest.mark.parametrize("assignments", [{}, {"one": "train", "extra": "test"}, {"one": "random"}])
def test_assignments_must_be_complete_explicit_and_known(sequence_factory, assignments):
    with pytest.raises(bridge.InvalidParameterError):
        windows.split_trajectory_families([sequence_factory()], assignments)


@pytest.mark.parametrize("fault", ["duplicate_id", "duplicate_directory", "wrong_manifest"])
def test_duplicate_or_unbound_trajectories_are_rejected(sequence_factory, fault):
    first, second = sequence_factory("a"), sequence_factory("b", start=172)
    if fault == "duplicate_id":
        second = replace(second, trajectory_id=first.trajectory_id)
    elif fault == "duplicate_directory":
        second = replace(first, trajectory_id="b")
    else:
        second = replace(second, manifest_sha256="0" * 64)
    assignments = {record.family_id: "train" for record in (first, second)}
    with pytest.raises((bridge.InvalidParameterError, bridge.ModelValidationError)):
        windows.split_trajectory_families([first, second], assignments)


@pytest.mark.parametrize("field", ["root_model_sha256", "checkpoint_sha256", "native_state_sha256"])
def test_shared_ancestry_cannot_be_relabeled_as_an_independent_family(sequence_factory, monkeypatch, field):
    first, second = sequence_factory("a"), sequence_factory("b", start=172)
    second = replace(second, **{field: getattr(first, field)})

    def no_batch_reads(self):
        raise AssertionError("Family admission must precede full batch verification")

    monkeypatch.setattr(FieldSequenceReader, "verify_all", no_batch_reads)
    with pytest.raises(bridge.ModelValidationError, match="conflicting family"):
        windows.build_window_index([first, second], {"a": "train", "b": "test"}, windows.WindowSpec(3, 2))


def test_model_identity_is_checked_across_ancestry_roles(sequence_factory):
    first, second = sequence_factory("a"), sequence_factory("b", start=172)
    source_sha = FieldSequenceReader(first.sequence_dir).manifest["source_sha256"]
    second = replace(second, checkpoint_sha256=source_sha)
    with pytest.raises(bridge.ModelValidationError, match="conflicting family"):
        windows.split_trajectory_families([first, second], {"a": "train", "b": "test"})


def test_repackaged_identical_npz_cannot_cross_family_split(sequence_factory):
    # Distinct model paths, model bytes and caller ancestry; identical sampled data.
    first, second = sequence_factory("a"), sequence_factory("b")
    a, b = (FieldSequenceReader(r.sequence_dir).manifest for r in (first, second))
    assert a["source_sha256"] != b["source_sha256"]
    assert a["batches"][0]["npz_sha256"] == b["batches"][0]["npz_sha256"]
    with pytest.raises(bridge.ModelValidationError, match="conflicting family"):
        windows.split_trajectory_families([first, second], {"a": "train", "b": "test"})


@pytest.mark.parametrize("fault", ["missing", "hash"])
def test_build_checks_every_batch_including_unused_tail(sequence_factory, fault):
    record = sequence_factory()
    tail = record.sequence_dir / "batch_0002" / "field_sample.npz"
    if fault == "missing":
        tail.unlink()
    else:
        tail.write_bytes(tail.read_bytes() + b"tampered")
    with pytest.raises(bridge.ModelValidationError):
        windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(2, 2, 10))


def test_window_reads_cross_batch_boundaries_without_exposing_diagnostics(sequence_factory, monkeypatch):
    record = sequence_factory()
    index = windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(3, 2))
    expected = load_full_field_sequence(record.sequence_dir)
    original_load = FieldSequenceReader._load_batch
    loaded = []

    def observe(self, entry):
        loaded.append(entry["path"])
        return original_load(self, entry)

    monkeypatch.setattr(FieldSequenceReader, "_load_batch", observe)
    monkeypatch.setattr(FieldSequenceReader, "verify_all",
                        lambda self: pytest.fail("Reading one window must not reverify all batches"))
    result = windows.read_window(index, "one:00000003")
    assert loaded == ["batch_0000", "batch_0001"]
    assert set(result) == {"history", "future", "history_mask", "future_mask", "x", "y",
                           "history_times", "future_times"}
    assert all(isinstance(value, np.ndarray) for value in result.values())
    for name, key, selected in [("history", "values", slice(3, 6)), ("future", "values", slice(6, 8)),
                                ("history_mask", "mask", slice(3, 6)), ("future_mask", "mask", slice(6, 8)),
                                ("history_times", "times", slice(3, 6)), ("future_times", "times", slice(6, 8))]:
        np.testing.assert_array_equal(result[name], expected[key][selected])
    assert result["history"].shape == (3, 3, 9, 17)
    assert np.isnan(result["history"][np.broadcast_to(result["history_mask"][:, None], result["history"].shape)]).all()


def test_reads_check_only_accessed_batch_hashes_but_never_accept_corrupt_accessed_data(sequence_factory):
    record = sequence_factory()
    index = windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(2, 2))
    path = record.sequence_dir / "batch_0002" / "field_sample.npz"
    path.write_bytes(path.read_bytes() + b"later corruption")
    assert windows.read_window(index, "one:00000000")["history"].shape[0] == 2
    with pytest.raises(bridge.ModelValidationError, match="hash"):
        windows.read_window(index, "one:00000006")


@pytest.mark.parametrize("fault", ["split", "family", "trajectory", "gap", "out_of_bounds", "duplicate", "count"])
def test_recomputed_digest_does_not_authorize_invalid_windows_or_splits(sequence_factory, fault):
    index = windows.build_window_index([sequence_factory()], {"one": "train"}, windows.WindowSpec(3, 2))
    altered = deepcopy(index)
    entry = altered["windows"][0]
    if fault == "split":
        entry["split"] = "test"
    elif fault == "family":
        entry["family_id"] = "foreign"
    elif fault == "trajectory":
        entry["trajectory_id"] = "foreign"
    elif fault == "gap":
        entry["future_start"] += 1
    elif fault == "out_of_bounds":
        entry["future_stop"] = 999
    elif fault == "duplicate":
        altered["windows"].append(deepcopy(entry))
    else:
        altered["counts"]["windows"] += 1
    _rehash(altered)
    with pytest.raises(bridge.ModelValidationError, match="contract"):
        windows.read_window(altered, entry["window_id"])


def test_saved_index_roundtrip_pins_bytes_and_refuses_overwrite(sequence_factory, tmp_path):
    index = windows.build_window_index([sequence_factory()], {"one": "train"}, windows.WindowSpec(3, 2))
    path = tmp_path / "index.json"
    windows.save_window_index(index, path)
    original = path.read_bytes()
    assert windows.load_window_index(path, expected_sha256=hashlib.sha256(original).hexdigest()) == index
    with pytest.raises(FileExistsError):
        windows.save_window_index(index, path)
    assert path.read_bytes() == original
    path.write_bytes(original + b" ")
    with pytest.raises(bridge.ModelValidationError, match="SHA-256"):
        windows.load_window_index(path, expected_sha256=hashlib.sha256(original).hexdigest())


def test_changed_manifest_or_index_digest_is_rejected_before_read(sequence_factory):
    record = sequence_factory()
    index = windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(3, 2))
    changed = deepcopy(index)
    changed["family_assignments"]["one"] = "test"
    with pytest.raises(bridge.ModelValidationError, match="digest"):
        windows.read_window(changed, "one:00000000")
    path = record.sequence_dir / "sequence_manifest.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(bridge.ModelValidationError, match="manifest SHA"):
        windows.read_window(index, "one:00000000")


def test_short_trajectory_unknown_window_and_non_id_lookup_rejected(sequence_factory):
    record = sequence_factory()
    with pytest.raises(bridge.InvalidParameterError, match="shorter"):
        windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(6, 5))
    index = windows.build_window_index([record], {"one": "train"}, windows.WindowSpec(3, 2))
    for invalid in ("one:00000099", index["windows"][0]):
        with pytest.raises(bridge.InvalidParameterError):
            windows.read_window(index, invalid)
