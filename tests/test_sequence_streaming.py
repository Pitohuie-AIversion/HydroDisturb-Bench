"""Streaming sequence admission using small real NPZ files and pinned manifests."""

from __future__ import annotations

import gc
import hashlib
import json
import weakref
from copy import deepcopy

import numpy as np
import pytest

from hydro_disturb_bench import field_sampling, field_sequence
from hydro_disturb_bench.comsol_java import bridge
from hydro_disturb_bench.field_sequence import FieldSequenceReader


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin_batch(directory, manifest, index):
    entry = manifest["batches"][index]
    batch = directory / entry["path"]
    for name, key in (("field_sample.npz", "npz_sha256"), ("metadata.json", "metadata_sha256"),
                      ("bridge_response.json", "response_sha256")):
        entry[key] = _sha(batch / name)


@pytest.fixture
def sequence(tmp_path):
    directory = tmp_path / "sequence"
    directory.mkdir()
    x, y = np.linspace(0, .4, 5), np.linspace(0, .24, 5)
    times = np.linspace(170, 171.8, 10)
    xx, yy = np.meshgrid(x, y)
    cylinder = np.hypot(xx - .2, yy - .12) <= .03 + 1e-12
    values = np.stack([np.asarray([t + xx + 2 * yy, -t + 3 * xx - yy,
                                  100 + t + 5 * xx + yy]) for t in times])
    values[:, :, cylinder] = np.nan
    mask = np.broadcast_to(cylinder, (10, 5, 5)).copy()
    invalid = mask.copy()
    invalid[::2] = False
    full = {"values": values, "mask": mask, "invalid_mask": invalid, "times": times,
            "x": x, "y": y, "cylinder_mask": cylinder}
    manifest = {"schema_version": 1, "status": "PASS", "operation": "export_full_field_sequence",
                "tensor_layout": "(T, 3, Ny, Nx)", "channel_order": ["u", "v", "p"],
                "units": ["m/s", "m/s", "Pa"], "grid_shape": [5, 5], "shape": [10, 3, 5, 5],
                "source_model": str(tmp_path / "original.mph"), "source_sha256": "a" * 64,
                "requested_times": times.tolist(), "actual_times": times.tolist(), "time_step_s": .2,
                "solution_numbers": list(range(901, 911)), "batch_size": 4, "batches": []}
    geometry = {"length_unit": "m", "bounds": [0., .4, 0., .24],
                "circle": {"center": [.2, .12], "radius": .03, "unit": "m"}}
    for index, (start, stop) in enumerate(((0, 3), (3, 7), (7, 10))):
        name = f"batch_{index:04d}"
        batch = directory / name
        batch.mkdir()
        arrays = {key: value[start:stop] if key in ("values", "mask", "invalid_mask", "times") else value
                  for key, value in full.items()}
        np.savez_compressed(batch / "field_sample.npz", **arrays)
        metadata = {"schema_version": 1, "tensor_layout": manifest["tensor_layout"],
                    "channel_order": manifest["channel_order"], "units": manifest["units"],
                    "source_model": manifest["source_model"], "source_sha256": manifest["source_sha256"],
                    "model_tag": "Model", "dataset": {"tag": "dset1", "solution_tag": "sol1"},
                    "geometry": deepcopy(geometry), "shape": list(arrays["values"].shape),
                    "requested_times": times[start:stop].tolist(), "actual_times": times[start:stop].tolist(),
                    "coordinates": {"x": x.tolist(), "y": y.tolist()},
                    "unit_evidence": [{"solnum": number} for number in manifest["solution_numbers"][start:stop]],
                    "npz_sha256": _sha(batch / "field_sample.npz")}
        _write(batch / "metadata.json", metadata)
        _write(batch / "bridge_response.json", {"status": "SUCCESS", "operation": "export_full_field_sample"})
        manifest["batches"].append({"path": name, "start_index": start, "stop_index": stop, "count": stop - start})
        _pin_batch(directory, manifest, index)
    _write(directory / "sequence_manifest.json", manifest)
    return directory, manifest, full


def test_constructor_and_manifest_access_do_not_load_npz_and_return_defensive_copies(sequence, monkeypatch):
    directory, manifest, _ = sequence
    monkeypatch.setattr(field_sampling, "load_full_field_sample", lambda *a: pytest.fail("Constructor loaded a batch"))
    reader = FieldSequenceReader(directory)
    assert len(reader) == 10
    assert reader.manifest_sha256 == _sha(directory / "sequence_manifest.json")
    copy = reader.manifest
    copy["batches"][0]["path"] = "mutated"
    copy["requested_times"][0] = -1
    assert reader.manifest == manifest


@pytest.mark.parametrize("start,stop", [(0, 1), (2, 8), (3, 7), (6, 9), (9, 10), (0, 10)])
def test_windows_match_independent_analytic_arrays_across_batches_and_at_last_frame(sequence, start, stop):
    directory, _, full = sequence
    reader = FieldSequenceReader(directory)
    window = reader.read_frames(start, stop)
    for key, expected in full.items():
        if key in ("values", "mask", "invalid_mask", "times"):
            expected = expected[start:stop]
        np.testing.assert_array_equal(window[key], expected)
    assert window["metadata"]["shape"] == [stop - start, 3, 5, 5]
    assert window["metadata"]["actual_times"] == full["times"][start:stop].tolist()
    assert window["metadata"]["solution_numbers"] == list(range(901 + start, 901 + stop))
    assert window["metadata"]["status"] == "WINDOW_VALIDATED"
    assert window["metadata"]["read_scope"]["full_sequence_verified"] is False
    assert window["metadata"]["read_scope"]["start_index"] == start


@pytest.mark.parametrize("start,stop", [(-1, 1), (0, 11), (10, 11), (3, 3), (4, 2),
                                       (True, 2), (0, False), (0., 2), (0, 2.),
                                       ("0", 2), (None, 2), (np.bool_(False), 2)])
def test_invalid_frame_indices_reject_before_loading(sequence, monkeypatch, start, stop):
    reader = FieldSequenceReader(sequence[0])
    monkeypatch.setattr(field_sampling, "load_full_field_sample", lambda *a: pytest.fail("Invalid window loaded data"))
    with pytest.raises(bridge.InvalidParameterError):
        reader.read_frames(start, stop)


def test_numpy_integer_indices_and_legacy_full_loader_remain_compatible(sequence):
    directory, manifest, full = sequence
    reader = FieldSequenceReader(directory)
    np.testing.assert_array_equal(reader.read_frames(np.int64(9), np.int64(10))["times"], full["times"][-1:])
    loaded = field_sequence.load_full_field_sequence(directory)
    assert loaded["metadata"] == manifest
    for key in full:
        np.testing.assert_array_equal(loaded[key], full[key])
    internal = deepcopy(manifest)
    internal["status"] = "VALIDATING"
    result = field_sequence._read_batches(directory, internal)
    np.testing.assert_array_equal(result["values"], full["values"])
    assert result["metadata"]["status"] == "VALIDATING"


def test_unvisited_corrupt_tail_is_not_certified_by_local_read(sequence):
    directory, _, _ = sequence
    path = directory / "batch_0002/field_sample.npz"
    path.write_bytes(path.read_bytes() + b"changed tail")
    reader = FieldSequenceReader(directory)
    local = reader.read_frames(0, 2)
    assert local["metadata"]["read_scope"]["visited_batches"] == ["batch_0000"]
    assert local["metadata"]["read_scope"]["full_sequence_verified"] is False
    for operation in (reader.verify_all, lambda: reader.read_frames(9, 10),
                      lambda: field_sequence.load_full_field_sequence(directory)):
        with pytest.raises(bridge.ModelValidationError, match="hash mismatch"):
            operation()


@pytest.mark.parametrize("fault", ["gap", "overlap", "duplicate_path", "missing_entry", "boolean_count",
                                   "bad_shape", "boolean_grid", "duplicate_time", "actual_time",
                                   "solution_gap", "boolean_solnum", "unpublished"])
def test_manifest_topology_time_and_solnums_reject_without_npz_access(sequence, monkeypatch, fault):
    directory, manifest, _ = sequence
    if fault == "gap":
        manifest["batches"][1]["start_index"] += 1
    elif fault == "overlap":
        manifest["batches"][1]["start_index"] -= 1
    elif fault == "duplicate_path":
        manifest["batches"][1]["path"] = manifest["batches"][0]["path"]
    elif fault == "missing_entry":
        manifest["batches"].pop()
    elif fault == "boolean_count":
        manifest["batches"][0]["count"] = True
    elif fault == "bad_shape":
        manifest["shape"][0] -= 1
    elif fault == "boolean_grid":
        manifest["grid_shape"][0] = True
    elif fault == "duplicate_time":
        manifest["requested_times"][2] = manifest["requested_times"][1]
    elif fault == "actual_time":
        manifest["actual_times"][-1] += .01
    elif fault == "solution_gap":
        manifest["solution_numbers"][3] += 1
    elif fault == "boolean_solnum":
        manifest["solution_numbers"][0] = True
    else:
        manifest["status"] = "VALIDATING"
    _write(directory / "sequence_manifest.json", manifest)
    monkeypatch.setattr(field_sampling, "load_full_field_sample", lambda *a: pytest.fail("Bad topology loaded a batch"))
    with pytest.raises(bridge.ModelValidationError):
        FieldSequenceReader(directory)


@pytest.mark.parametrize("fault", ["missing", "extra"])
def test_all_batch_directories_checked_before_any_read(sequence, fault):
    directory, _, _ = sequence
    reader = FieldSequenceReader(directory)
    if fault == "missing":
        (directory / "batch_0002").rename(directory / "hidden_tail")
    else:
        (directory / "batch_extra").mkdir()
    with pytest.raises(bridge.ModelValidationError, match="batch directories"):
        FieldSequenceReader(directory)
    with pytest.raises(bridge.ModelValidationError, match="batch directories"):
        reader.read_frames(0, 1)


@pytest.mark.parametrize("fault", ["time", "solnum", "source", "fluid_mask", "geometry", "axes"])
def test_rehashed_batch_cannot_bypass_time_source_grid_and_mask_contracts(sequence, fault):
    directory, manifest, _ = sequence
    batch = directory / "batch_0001"
    metadata = json.loads((batch / "metadata.json").read_text(encoding="utf-8"))
    with np.load(batch / "field_sample.npz", allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    if fault == "time":
        arrays["times"] += .01
        metadata["actual_times"] = metadata["requested_times"] = arrays["times"].tolist()
    elif fault == "solnum":
        metadata["unit_evidence"][0]["solnum"] += 1
    elif fault == "source":
        metadata["source_sha256"] = "b" * 64
    elif fault == "fluid_mask":
        arrays["invalid_mask"][0, 0, 0] = True
        arrays["mask"][0, 0, 0] = True
        arrays["values"][0, :, 0, 0] = np.nan
    elif fault == "axes":
        arrays["x"][0] -= .01
        metadata["coordinates"]["x"] = arrays["x"].tolist()
    else:
        metadata["geometry"]["circle"]["radius"] = .11
        xx, yy = np.meshgrid(arrays["x"], arrays["y"])
        solid = np.hypot(xx - .2, yy - .12) <= .11 + 1e-12
        arrays["cylinder_mask"] = solid
        arrays["mask"] = np.broadcast_to(solid, arrays["mask"].shape).copy()
        arrays["invalid_mask"] = arrays["mask"].copy()
        arrays["values"][:, :, solid] = np.nan
    np.savez_compressed(batch / "field_sample.npz", **arrays)
    metadata["npz_sha256"] = _sha(batch / "field_sample.npz")
    _write(batch / "metadata.json", metadata)
    _pin_batch(directory, manifest, 1)
    _write(directory / "sequence_manifest.json", manifest)
    with pytest.raises(bridge.ModelValidationError):
        FieldSequenceReader(directory).verify_all()


@pytest.mark.parametrize("access", ["manifest", "hash", "len", "window", "iterator", "all"])
def test_manifest_byte_changes_reject_every_public_access(sequence, access):
    directory, _, _ = sequence
    reader = FieldSequenceReader(directory)
    path = directory / "sequence_manifest.json"
    path.write_bytes(path.read_bytes() + b"\n")
    operations = {"manifest": lambda: reader.manifest, "hash": lambda: reader.manifest_sha256,
                  "len": lambda: len(reader), "window": lambda: reader.read_frames(0, 1),
                  "iterator": reader.iter_batches, "all": reader.verify_all}
    with pytest.raises(bridge.ModelValidationError, match="manifest changed"):
        operations[access]()


def test_manifest_and_target_hash_are_rechecked_between_yields(sequence):
    directory, _, _ = sequence
    reader = FieldSequenceReader(directory)
    iterator = reader.iter_batches()
    first = next(iterator)
    del first
    path = directory / "batch_0001/bridge_response.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(bridge.ModelValidationError, match="hash mismatch"):
        next(iterator)


def test_reader_retains_no_batch_arrays_and_never_calls_the_full_loader(sequence, monkeypatch):
    directory, _, full = sequence
    original = field_sampling.load_full_field_sample
    refs, visits = [], []

    def tracked(path):
        gc.collect()
        assert all(ref() is None for ref in refs), "Reader retained a prior batch's values"
        data = original(path)
        refs.append(weakref.ref(data["values"]))
        visits.append(path.name)
        return data

    monkeypatch.setattr(field_sampling, "load_full_field_sample", tracked)
    monkeypatch.setattr(field_sequence, "load_full_field_sequence", lambda *a: pytest.fail("Streaming called full loader"))
    monkeypatch.setattr(np, "concatenate", lambda *a, **k: pytest.fail("Streaming concatenated batches"))
    reader = FieldSequenceReader(directory)
    for batch in reader.iter_batches():
        del batch
    gc.collect()
    assert all(ref() is None for ref in refs)
    summary = reader.verify_all()
    assert summary["full_sequence_verified"] is True
    assert summary["frames"] == 10 and summary["batches"] == 3
    window = reader.read_frames(2, 8)
    np.testing.assert_array_equal(window["values"], full["values"][2:8])
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert visits == ["batch_0000", "batch_0001", "batch_0002"] * 3


def test_caller_mutation_of_yielded_static_arrays_does_not_poison_reader(sequence):
    reader = FieldSequenceReader(sequence[0])
    iterator = reader.iter_batches()
    first = next(iterator)
    first["x"][0] = -999
    first["metadata"]["geometry"]["circle"]["radius"] = 999
    del first
    next(iterator)
    next(iterator)
    with pytest.raises(StopIteration):
        next(iterator)


def _shift_batch_times(directory, manifest, index, offsets):
    batch = directory / manifest["batches"][index]["path"]
    metadata = json.loads((batch / "metadata.json").read_text(encoding="utf-8"))
    with np.load(batch / "field_sample.npz", allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    arrays["times"] += offsets
    metadata["actual_times"] = arrays["times"].tolist()
    np.savez_compressed(batch / "field_sample.npz", **arrays)
    metadata["npz_sha256"] = _sha(batch / "field_sample.npz")
    _write(batch / "metadata.json", metadata)
    _pin_batch(directory, manifest, index)


def test_saved_time_roundoff_is_preserved_in_streaming_and_full_reads(sequence):
    directory, manifest, _ = sequence
    offset = np.spacing(170.)
    for index in range(3):
        _shift_batch_times(directory, manifest, index, offset)
    manifest["actual_times"] = (np.asarray(manifest["actual_times"]) + offset).tolist()
    _write(directory / "sequence_manifest.json", manifest)
    reader = FieldSequenceReader(directory)
    actual = np.asarray(manifest["actual_times"])
    np.testing.assert_array_equal(reader.read_frames(2, 8)["times"], actual[2:8])
    assert not np.array_equal(actual, manifest["requested_times"])
    assert reader.verify_all()["status"] == "PASS"
    np.testing.assert_array_equal(field_sequence.load_full_field_sequence(directory)["times"], actual)


def test_cross_batch_time_step_check_is_not_weakened_by_per_frame_tolerance(sequence):
    directory, manifest, _ = sequence
    # Each actual time is within the existing 1e-12 s tolerance, but the two
    # opposite errors together exceed that tolerance at the batch boundary.
    _shift_batch_times(directory, manifest, 0, [0., 0., 9e-13])
    _shift_batch_times(directory, manifest, 1, [-9e-13, 0., 0., 0.])
    _write(directory / "sequence_manifest.json", manifest)
    reader = FieldSequenceReader(directory)
    for operation in (reader.verify_all, lambda: reader.read_frames(2, 4),
                      lambda: field_sequence.load_full_field_sequence(directory)):
        with pytest.raises(bridge.ModelValidationError, match="duplicate or missing frames"):
            operation()


def test_batch_actual_times_must_match_actual_manifest_not_only_requested_times(sequence):
    directory, manifest, _ = sequence
    _shift_batch_times(directory, manifest, 1, 9e-13)
    manifest["actual_times"] = (np.asarray(manifest["actual_times"]) - 9e-13).tolist()
    _write(directory / "sequence_manifest.json", manifest)
    with pytest.raises(bridge.ModelValidationError, match="actual times"):
        FieldSequenceReader(directory).read_frames(3, 7)


def test_manifest_change_during_batch_loading_rejects_before_return(sequence, monkeypatch):
    directory, _, _ = sequence
    reader = FieldSequenceReader(directory)
    original = field_sampling.load_full_field_sample

    def changing(path):
        data = original(path)
        manifest = directory / "sequence_manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        return data

    monkeypatch.setattr(field_sampling, "load_full_field_sample", changing)
    with pytest.raises(bridge.ModelValidationError, match="manifest changed"):
        reader.read_frames(0, 1)
