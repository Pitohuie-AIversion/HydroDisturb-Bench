"""Contiguous saved-solution sequences, stored as validated bounded NPZ batches."""
from __future__ import annotations

import json
import os
import uuid
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


def _read_batches(directory, manifest):
    sampling._require(manifest.get("schema_version") == 1 and manifest.get("operation") == "export_full_field_sequence",
                      "Unknown field sequence schema")
    sampling._require(manifest.get("tensor_layout") == sampling.LAYOUT and manifest.get("channel_order") == list(sampling.CHANNELS)
                      and manifest.get("units") == list(sampling.UNITS), "Invalid sequence channel/unit contract")
    requested = np.asarray(manifest.get("requested_times"), dtype=float)
    sampling._require(requested.ndim == 1 and len(requested) >= 2 and np.isfinite(requested).all(), "Missing sequence time axis")
    step = manifest.get("time_step_s")
    sampling._require(isinstance(step, (int, float)) and np.isfinite(step) and step > 0,
                      "Invalid sequence time step")
    sampling._require(sampling._same(np.diff(requested), np.full(len(requested)-1, step)), "Requested sequence has duplicate or missing frames")
    batches = manifest.get("batches", [])
    sampling._require(bool(batches), "Sequence has no batches")
    arrays, names, solution_numbers = [], set(), []
    cursor = 0
    for entry in batches:
        name = entry.get("path", "")
        sampling._require(name and Path(name).name == name and "/" not in name and "\\" not in name and name not in names,
                          "Invalid or duplicate sequence batch path")
        names.add(name)
        start, stop, count = (entry.get(key) for key in ("start_index", "stop_index", "count"))
        sampling._require(all(isinstance(v, int) and not isinstance(v, bool) for v in (start, stop, count))
                          and start == cursor and stop == start + count and 1 <= count <= 32 and stop <= len(requested),
                          "Batch indices have a gap, overlap or invalid count")
        batch_dir = directory / name
        for filename, key in (("field_sample.npz", "npz_sha256"), ("metadata.json", "metadata_sha256"),
                              ("bridge_response.json", "response_sha256")):
            sampling._require(bridge.compute_file_hash(batch_dir / filename).lower() == str(entry.get(key, "")).lower(),
                              f"Sequence batch hash mismatch: {filename}")
        data = sampling.load_full_field_sample(batch_dir)
        sampling._require(sampling._same(data["times"], requested[start:stop]), "Batch times differ from sequence slice")
        metadata = data["metadata"]
        sampling._require(metadata["source_sha256"] == manifest["source_sha256"] and metadata["source_model"] == manifest["source_model"],
                          "Batch model identity differs from sequence")
        sampling._require(list(data["values"].shape[1:]) == [3, manifest["grid_shape"][1], manifest["grid_shape"][0]],
                          "Batch dimensions differ from sequence")
        if arrays:
            _same_grid_and_source(arrays[0], data)
        else:
            _same_grid_and_source(data, data)
        for item in metadata["unit_evidence"]:
            number = item.get("solnum")
            sampling._require(isinstance(number, int) and not isinstance(number, bool), "Missing saved solution number")
            solution_numbers.append(number)
        arrays.append(data)
        cursor = stop
    sampling._require(cursor == len(requested) and len(solution_numbers) == cursor
                      and np.all(np.diff(solution_numbers) == 1), "Sequence is missing saved frames or repeats a solution number")
    sampling._require(solution_numbers == manifest.get("solution_numbers"), "Saved solution numbers differ from manifest")
    sampling._require({p.name for p in directory.glob("batch_*") if p.is_dir()} == names, "Missing or extra sequence batch directories")
    result = {key: np.concatenate([data[key] for data in arrays], axis=0)
              for key in ("values", "mask", "invalid_mask", "times")}
    result.update({key: arrays[0][key] for key in ("x", "y", "cylinder_mask")})
    sampling._require(list(result["values"].shape) == manifest.get("shape"), "Full sequence shape differs from manifest")
    sampling._require(sampling._same(result["times"], manifest.get("actual_times"))
                      and sampling._same(result["times"], requested), "Full sequence actual times differ from requested times")
    sampling._require(sampling._same(np.diff(result["times"]), np.full(cursor-1, step)), "Actual sequence has duplicate or missing frames")
    result["metadata"] = manifest
    return result


def load_full_field_sequence(output_dir):
    """Verify every batch and concatenate the full (T,3,Ny,Nx) sequence."""
    directory = Path(output_dir)
    manifest = json.loads((directory / "sequence_manifest.json").read_text(encoding="utf-8"))
    sampling._require(manifest.get("status") == "PASS", "Sequence has not passed publication checks")
    return _read_batches(directory, manifest)


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
                first_data = data
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
