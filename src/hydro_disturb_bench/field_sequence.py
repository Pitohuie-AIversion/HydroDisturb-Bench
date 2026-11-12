"""Contiguous saved-solution sequences, stored as validated bounded NPZ batches."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from pathlib import Path

import numpy as np

from . import field_sampling as sampling
from .comsol_java import bridge


def _input_require(condition, message):
    if not condition:
        raise bridge.InvalidParameterError(message)


def _time_axis(start, stop, step):
    _input_require(np.isfinite([start, stop, step]).all() and stop > start and step > 0,
                      "Sequence needs finite start < stop and a positive time step")
    intervals = int(round((stop - start) / step))
    _input_require(1 <= intervals < 10000 and sampling._same(start + intervals * step, stop),
                      "Sequence endpoint must lie on the requested time grid (2–10000 frames)")
    return np.linspace(start, stop, intervals + 1)


def _same_grid_and_source(first, current):
    for key in ("source_model", "source_sha256", "model_tag", "dataset", "geometry", "channel_order", "units"):
        sampling._require(first["metadata"][key] == current["metadata"][key], f"Batch source/grid metadata changed: {key}")
    for key in ("x", "y", "cylinder_mask"):
        sampling._require(np.array_equal(first[key], current[key]), f"Batch coordinate/mask changed: {key}")
    sampling._require(np.all(current["mask"] == first["mask"][0]), "Sequence validity mask changed across frames or batches")


_SOURCE_KEYS = ("source_model", "source_sha256", "model_tag", "dataset", "geometry", "channel_order", "units")
_BATCH_HASHES = (("field_sample.npz", "npz_sha256"), ("metadata.json", "metadata_sha256"),
                 ("bridge_response.json", "response_sha256"))


def _static_reference(data):
    """Detach only source/grid evidence, never retaining a batch's field arrays."""
    return {"metadata": {key: deepcopy(data["metadata"][key]) for key in _SOURCE_KEYS},
            **{key: data[key].copy() for key in ("x", "y", "cylinder_mask")},
            "mask": data["mask"][:1].copy()}


def _integer(value):
    return isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_))


class FieldSequenceReader:
    """Validate a published sequence without materializing all its NPZ batches.

    Construction validates manifest topology, time/solution axes and directories.
    Each access rechecks the pinned manifest and each visited batch's three hashes.
    Partial reads certify only visited batches; ``verify_all`` traverses all batches.
    The reader retains only static grid/source evidence. Consumers of iter_batches
    control the lifetime of yielded arrays and should release batches as they go.
    """

    def __init__(self, output_dir):
        try:
            directory = Path(output_dir).resolve()
        except TypeError as exc:
            raise bridge.InvalidParameterError("Sequence directory must be a filesystem path") from exc
        raw = (directory / "sequence_manifest.json").read_bytes()
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise bridge.ModelValidationError("Malformed field sequence manifest") from exc
        self._initialize(directory, manifest, hashlib.sha256(raw).hexdigest(), published=True)

    @classmethod
    def _from_manifest(cls, directory, manifest):
        """Internal admission for the exporter's complete in-memory VALIDATING manifest."""
        reader = cls.__new__(cls)
        reader._initialize(Path(directory), manifest, None, published=False)
        return reader

    def _initialize(self, directory, manifest, digest, *, published):
        self._directory = directory
        self._manifest = deepcopy(manifest)
        self._manifest_sha256 = digest
        self._reference = None
        sampling._require(isinstance(manifest, dict), "Field sequence manifest must be an object")
        sampling._require(manifest.get("status") == "PASS" if published else
                          manifest.get("status") in ("PASS", "VALIDATING"),
                          "Sequence has not passed publication checks")
        try:
            self._validate_manifest()
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise bridge.ModelValidationError(f"Malformed field sequence manifest: {exc}") from exc
        self._check_directories()

    def _validate_manifest(self):
        manifest = self._manifest
        sampling._require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1
                          and manifest.get("operation") == "export_full_field_sequence", "Unknown field sequence schema")
        sampling._require(manifest.get("tensor_layout") == sampling.LAYOUT
                          and manifest.get("channel_order") == list(sampling.CHANNELS)
                          and manifest.get("units") == list(sampling.UNITS), "Invalid sequence channel/unit contract")
        requested = np.asarray(manifest.get("requested_times"), dtype=float)
        sampling._require(requested.ndim == 1 and len(requested) >= 2 and np.isfinite(requested).all(),
                          "Missing sequence time axis")
        step = manifest.get("time_step_s")
        sampling._require(isinstance(step, (int, float)) and not isinstance(step, bool)
                          and np.isfinite(step) and step > 0, "Invalid sequence time step")
        sampling._require(sampling._same(np.diff(requested), np.full(len(requested) - 1, step)),
                          "Requested sequence has duplicate or missing frames")
        sampling._require(sampling._same(manifest.get("actual_times"), requested),
                          "Full sequence actual times differ from requested times")
        sampling._require(sampling._same(np.diff(manifest["actual_times"]), np.full(len(requested) - 1, step)),
                          "Actual sequence has duplicate or missing frames")
        grid = manifest.get("grid_shape")
        sampling._require(isinstance(grid, list) and len(grid) == 2
                          and all(_integer(v) and v >= 3 for v in grid), "Invalid sequence grid shape")
        shape = manifest.get("shape")
        sampling._require(isinstance(shape, list) and all(_integer(v) for v in shape)
                          and shape == [len(requested), 3, grid[1], grid[0]], "Full sequence shape differs from manifest")
        numbers = manifest.get("solution_numbers")
        sampling._require(isinstance(numbers, list) and len(numbers) == len(requested)
                          and all(_integer(v) and v > 0 for v in numbers)
                          and all(b == a + 1 for a, b in zip(numbers, numbers[1:])),
                          "Sequence is missing saved frames or repeats a solution number")
        sampling._require(all(isinstance(manifest.get(key), str) and bool(manifest[key])
                              for key in ("source_model", "source_sha256")), "Missing sequence model identity")
        batches = manifest.get("batches")
        sampling._require(isinstance(batches, list) and bool(batches), "Sequence has no batches")
        names, cursor = set(), 0
        for entry in batches:
            sampling._require(isinstance(entry, dict), "Invalid sequence batch record")
            name = entry.get("path", "")
            sampling._require(isinstance(name, str) and name and Path(name).name == name
                              and "/" not in name and "\\" not in name and name not in names,
                              "Invalid or duplicate sequence batch path")
            names.add(name)
            start, stop, count = (entry.get(key) for key in ("start_index", "stop_index", "count"))
            sampling._require(all(_integer(v) for v in (start, stop, count)) and start == cursor
                              and stop == start + count and 1 <= count <= 32 and stop <= len(requested),
                              "Batch indices have a gap, overlap or invalid count")
            for _, key in _BATCH_HASHES:
                value = entry.get(key)
                sampling._require(isinstance(value, str) and len(value) == 64
                                  and all(c in "0123456789abcdefABCDEF" for c in value),
                                  f"Missing or invalid sequence batch hash: {key}")
            cursor = stop
        sampling._require(cursor == len(requested), "Sequence is missing saved frames or repeats a solution number")
        self._requested = requested
        self._names = names
        self._frame_count = len(requested)

    def _check_directories(self):
        actual = {p.name for p in self._directory.glob("batch_*") if p.is_dir()}
        sampling._require(actual == self._names, "Missing or extra sequence batch directories")
        for entry in self._manifest["batches"]:
            for filename, _ in _BATCH_HASHES:
                sampling._require((self._directory / entry["path"] / filename).is_file(),
                                  f"Missing sequence batch file: {entry['path']}/{filename}")

    def _assert_unchanged(self):
        if self._manifest_sha256 is not None:
            raw = (self._directory / "sequence_manifest.json").read_bytes()
            sampling._require(hashlib.sha256(raw).hexdigest() == self._manifest_sha256,
                              "Sequence manifest changed after reader construction")
        self._check_directories()

    @property
    def manifest(self):
        self._assert_unchanged()
        return deepcopy(self._manifest)

    @property
    def manifest_sha256(self):
        self._assert_unchanged()
        return self._manifest_sha256

    def __len__(self):
        self._assert_unchanged()
        return self._frame_count

    def _check_hashes(self, entry):
        for filename, key in _BATCH_HASHES:
            actual = bridge.compute_file_hash(self._directory / entry["path"] / filename).lower()
            sampling._require(actual == entry[key].lower(), f"Sequence batch hash mismatch: {filename}")

    def _load_batch(self, entry):
        self._assert_unchanged()
        self._check_hashes(entry)
        data = sampling.load_full_field_sample(self._directory / entry["path"])
        start, stop = entry["start_index"], entry["stop_index"]
        sampling._require(sampling._same(data["times"], self._requested[start:stop]), "Batch times differ from sequence slice")
        sampling._require(sampling._same(data["times"], self._manifest["actual_times"][start:stop]),
                          "Full sequence actual times differ from requested times")
        sampling._require(sampling._same(np.diff(data["times"]),
                                        np.full(stop - start - 1, self._manifest["time_step_s"])),
                          "Actual sequence has duplicate or missing frames")
        metadata = data["metadata"]
        sampling._require(metadata["source_sha256"] == self._manifest["source_sha256"]
                          and metadata["source_model"] == self._manifest["source_model"],
                          "Batch model identity differs from sequence")
        sampling._require(list(data["values"].shape[1:]) == [3, *self._manifest["grid_shape"][::-1]],
                          "Batch dimensions differ from sequence")
        numbers = [item.get("solnum") for item in metadata["unit_evidence"]]
        sampling._require(all(_integer(v) for v in numbers), "Missing saved solution number")
        sampling._require(numbers == self._manifest["solution_numbers"][start:stop],
                          "Saved solution numbers differ from manifest")
        _same_grid_and_source(self._reference if self._reference is not None else data, data)
        self._check_hashes(entry)
        self._assert_unchanged()
        if self._reference is None:
            self._reference = _static_reference(data)
        return data

    def _iter_entries(self, entries):
        previous_time = None
        for entry in entries:
            batch = self._load_batch(entry)
            if previous_time is not None:
                sampling._require(sampling._same(batch["times"][0] - previous_time,
                                                self._manifest["time_step_s"]),
                                  "Actual sequence has duplicate or missing frames")
            previous_time = float(batch["times"][-1])
            yield batch
            del batch
        self._assert_unchanged()

    def iter_batches(self):
        """Yield one verified sample dictionary at a time, without retaining fields."""
        self._assert_unchanged()
        return self._iter_entries(self._manifest["batches"])

    def _materialize(self, start, stop, *, window_metadata):
        count = stop - start
        nx, ny = self._manifest["grid_shape"]
        result = {"values": np.empty((count, 3, ny, nx), dtype=np.float64),
                  "mask": np.empty((count, ny, nx), dtype=bool),
                  "invalid_mask": np.empty((count, ny, nx), dtype=bool),
                  "times": np.empty(count, dtype=float)}
        visited = []
        for entry in self._manifest["batches"]:
            lo, hi = max(start, entry["start_index"]), min(stop, entry["stop_index"])
            if lo >= hi:
                continue
            data = self._load_batch(entry)
            source = slice(lo - entry["start_index"], hi - entry["start_index"])
            target = slice(lo - start, hi - start)
            for key in ("values", "mask", "invalid_mask", "times"):
                result[key][target] = data[key][source]
            if not visited:
                result.update({key: data[key].copy() for key in ("x", "y", "cylinder_mask")})
            visited.append(entry["path"])
            del data
        self._assert_unchanged()
        sampling._require(sampling._same(np.diff(result["times"]),
                                        np.full(count - 1, self._manifest["time_step_s"])),
                          "Actual sequence has duplicate or missing frames")
        metadata = deepcopy(self._manifest)
        if window_metadata:
            metadata.update(status="WINDOW_VALIDATED", shape=list(result["values"].shape),
                            requested_times=metadata["requested_times"][start:stop],
                            actual_times=metadata["actual_times"][start:stop],
                            solution_numbers=metadata["solution_numbers"][start:stop])
            metadata.pop("batches", None)
            metadata["read_scope"] = {"start_index": start, "stop_index": stop,
                                      "total_frames": self._frame_count, "visited_batches": visited,
                                      "full_sequence_verified": False,
                                      "manifest_sha256": self._manifest_sha256}
        result["metadata"] = metadata
        return result

    def read_frames(self, start, stop):
        """Read a nonempty half-open frame window; indices must be nonnegative integers."""
        self._assert_unchanged()
        _input_require(_integer(start) and _integer(stop) and 0 <= start < stop <= self._frame_count,
                       "Frame window requires integer 0 <= start < stop <= sequence length")
        return self._materialize(int(start), int(stop), window_metadata=True)

    def verify_all(self):
        """Visit every batch without concatenation; return only a small validation summary."""
        count, frames = 0, 0
        for batch in self.iter_batches():
            count += 1
            frames += len(batch["times"])
            del batch
        self._assert_unchanged()
        return {"status": "PASS", "validation_scope": "all_batches", "full_sequence_verified": True,
                "frames": frames, "batches": count, "shape": list(self._manifest["shape"]),
                "source_sha256": self._manifest["source_sha256"], "manifest_sha256": self._manifest_sha256}


def _read_batches(directory, manifest):
    reader = FieldSequenceReader._from_manifest(directory, manifest)
    return reader._materialize(0, reader._frame_count, window_metadata=False)


def load_full_field_sequence(output_dir):
    """Verify every batch and materialize the full legacy (T,3,Ny,Nx) structure."""
    reader = FieldSequenceReader(output_dir)
    return reader._materialize(0, len(reader), window_metadata=False)


def _check_overlap(sequence, reference_dir):
    if reference_dir is None:
        return {"status": "NOT_CHECKED", "frames_checked": 0}
    reference_dir = Path(reference_dir).resolve()
    reference = sampling.load_full_field_sample(reference_dir)
    first_batch_metadata = sequence["batch_metadata"]
    _same_grid_and_source({**sequence, "metadata": first_batch_metadata}, reference)
    comparisons = []
    for ti, time_value in enumerate(reference["times"]):
        hits = np.flatnonzero(np.abs(sequence["times"] - time_value) <= sampling.TIME_ATOL)
        sampling._require(len(hits) == 1, "Reference time is missing or duplicated in the continuous sequence")
        si = int(hits[0])
        sampling._require(np.array_equal(sequence["mask"][si], reference["mask"][ti])
                          and np.array_equal(sequence["invalid_mask"][si], reference["invalid_mask"][ti]),
                          "Reference and sequence masks differ at the overlapping time")
        sampling._require(np.allclose(sequence["values"][si], reference["values"][ti],
                                      rtol=sampling.VALUE_RTOL, atol=sampling.VALUE_ATOL, equal_nan=True),
                          "Sequence differs from the accepted sample at an overlapping frame")
        delta = np.abs(sequence["values"][si] - reference["values"][ti])
        comparisons.append(np.nanmax(delta, axis=(1, 2)))
    return {"status": "PASS", "reference": str(reference_dir),
            "reference_npz_sha256": bridge.compute_file_hash(reference_dir / "field_sample.npz"),
            "times": reference["times"].tolist(), "frames_checked": len(comparisons),
            "max_absolute_errors": np.max(comparisons, axis=0).tolist()}


def export_full_field_sequence(model_path, output_dir, *, start=170., stop=200., step=.2,
                                grid_shape=(161, 97), batch_size=32, overlap_reference=None,
                                root=None, process_runner=None):
    """Export a contiguous saved-time sequence to a new directory; retain failures."""
    model_path, output_dir = Path(model_path).resolve(), Path(output_dir).absolute()
    if not model_path.is_file():
        raise bridge.ModelNotFoundError(f"Model file does not exist: {model_path}")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(output_dir)
    times = _time_axis(start, stop, step)
    _input_require(isinstance(batch_size, int) and not isinstance(batch_size, bool) and 1 <= batch_size <= 32,
                      "Sequence batch size must be an integer from 1 to 32")
    _input_require(len(grid_shape) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and v >= 3 for v in grid_shape),
                      "Sequence grid requires integer nx,ny >=3")
    before = bridge.compute_file_hash(model_path)
    stage = output_dir.parent / (".staging_field_sequence_" + uuid.uuid4().hex[:12])
    stage.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "operation": "export_full_field_sequence", "status": "VALIDATING",
                "source_model": str(model_path), "source_sha256": before, "requested_times": times.tolist(),
                "actual_times": [], "time_step_s": float(step), "grid_shape": list(grid_shape), "batch_size": batch_size,
                "shape": [len(times), 3, grid_shape[1], grid_shape[0]], "tensor_layout": sampling.LAYOUT,
                "channel_order": list(sampling.CHANNELS), "units": list(sampling.UNITS), "batches": [],
                "solution_numbers": [], "solver_called": False}
    metrics = []
    try:
        process_options = {} if process_runner is None else {"process_runner": process_runner}
        first_data = None
        for batch_index, begin in enumerate(range(0, len(times), batch_size)):
            end = min(begin + batch_size, len(times))
            batch_dir = stage / f"batch_{batch_index:04d}"
            metric = sampling._export_grid_sample(model_path, batch_dir, times[begin:end], grid_shape,
                np.array([0., .4, 0., .24]), before, root=root, command="export-full-field-sequence-batch",
                **process_options)
            sampling._require(bridge.compute_file_hash(model_path) == before, "Source model changed during sequence sampling")
            data = sampling.load_full_field_sample(batch_dir)
            _same_grid_and_source(first_data or data, data)
            if first_data is None:
                first_data = _static_reference(data)
            manifest["batches"].append({"path": batch_dir.name, "start_index": begin, "stop_index": end,
                "count": end - begin, "npz_sha256": metric["npz_sha256"],
                "metadata_sha256": bridge.compute_file_hash(batch_dir / "metadata.json"),
                "response_sha256": bridge.compute_file_hash(batch_dir / "bridge_response.json")})
            manifest["actual_times"].extend(data["times"].tolist())
            manifest["solution_numbers"].extend(item["solnum"] for item in data["metadata"]["unit_evidence"])
            metrics.append(metric)
            sampling._write_json(stage / "progress.json", {"completed_frames": end, "total_frames": len(times), "batches": manifest["batches"]})
        complete = _read_batches(stage, manifest)
        complete["batch_metadata"] = first_data["metadata"]
        manifest["overlap_check"] = _check_overlap(complete, overlap_reference)
        manifest.update(status="PASS", source_unchanged=True)
        sampling._write_json(stage / "sequence_manifest.json", manifest)
        loaded = load_full_field_sequence(stage)
        sampling._require(np.array_equal(loaded["values"], complete["values"], equal_nan=True), "Sequence changed on full readback")
        report = {**manifest, "output_dir": str(output_dir.resolve()), "batch_metrics": metrics,
                  "readback_passed": True, "direct_point_checks": sum(m["direct_checks"] for m in metrics),
                  "max_direct_absolute_errors": np.max([m["max_direct_absolute_errors"] for m in metrics], axis=0).tolist(),
                  "fluid_entries": int(np.isfinite(loaded["values"]).sum()), "masked_entries": int(np.isnan(loaded["values"]).sum())}
        sampling._write_json(stage / "sequence_report.json", report)
        with bridge.file_publish_lock(output_dir.parent / ".locks" / (output_dir.name + ".lock")):
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError(output_dir)
            if os.name == "nt":
                os.rename(stage, output_dir)
            else:
                bridge._posix_rename_noreplace(stage, output_dir)
        return report
    except Exception as exc:
        after = bridge.compute_file_hash(model_path)
        sampling._write_json(stage / "failure.json", {"status": "FAIL", "error": str(exc), "details": getattr(exc, "details", ""),
            "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": before == after,
            "completed_batches": manifest["batches"], "preserved_staging_directory": str(stage), "intended_output": str(output_dir)})
        raise
