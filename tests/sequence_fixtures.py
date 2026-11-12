"""Analytic saved-sequence files for read-only data-layer tests.

These are synthetic inputs, not COMSOL export evidence. Tests use the production
NPZ loader and sequence reader, without invoking the unrelated publication path.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")


def write_saved_sequence(directory, model, *, start=170., frames=10, step=.2,
                         grid_shape=(17, 9), batch_size=4):
    """Write deterministic analytic arrays and their pinned saved-data contract."""
    directory, model = Path(directory).resolve(), Path(model).resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=False)
    nx, ny = grid_shape
    x, y = np.linspace(0., .4, nx), np.linspace(0., .24, ny)
    times = np.linspace(start, start + step * (frames - 1), frames)
    xx, yy = np.meshgrid(x, y)
    cylinder = np.hypot(xx - .2, yy - .12) <= .03 + 1e-12
    values = np.stack([np.asarray([t + xx + 2 * yy, -t + 3 * xx - yy,
                                  100 + t + 5 * xx + yy]) for t in times])
    values[:, :, cylinder] = np.nan
    mask = np.broadcast_to(cylinder, (frames, ny, nx)).copy()
    invalid = mask.copy()
    invalid[::2] = False
    full = {"values": values, "mask": mask, "invalid_mask": invalid, "times": times,
            "x": x, "y": y, "cylinder_mask": cylinder}
    manifest = {
        "schema_version": 1, "status": "PASS", "operation": "export_full_field_sequence",
        "synthetic_fixture": True, "tensor_layout": "(T, 3, Ny, Nx)",
        "channel_order": ["u", "v", "p"], "units": ["m/s", "m/s", "Pa"],
        "grid_shape": [nx, ny], "shape": [frames, 3, ny, nx],
        "source_model": str(model), "source_sha256": _sha(model),
        "requested_times": times.tolist(), "actual_times": times.tolist(), "time_step_s": step,
        "solution_numbers": list(range(901, 901 + frames)), "batch_size": batch_size,
        "batches": [], "solver_called": False, "source_unchanged": True,
    }
    geometry = {"length_unit": "m", "bounds": [0., .4, 0., .24],
                "circle": {"center": [.2, .12], "radius": .03, "unit": "m"}}
    for index, begin in enumerate(range(0, frames, batch_size)):
        end = min(begin + batch_size, frames)
        name = f"batch_{index:04d}"
        batch = directory / name
        batch.mkdir()
        arrays = {key: value[begin:end] if key in ("values", "mask", "invalid_mask", "times") else value
                  for key, value in full.items()}
        np.savez_compressed(batch / "field_sample.npz", **arrays)
        metadata = {
            "schema_version": 1, "synthetic_fixture": True,
            "tensor_layout": manifest["tensor_layout"], "channel_order": manifest["channel_order"],
            "units": manifest["units"], "source_model": manifest["source_model"],
            "source_sha256": manifest["source_sha256"], "model_tag": "AnalyticFixture",
            "dataset": {"tag": "dset1", "solution_tag": "sol1"}, "geometry": deepcopy(geometry),
            "shape": list(arrays["values"].shape), "requested_times": times[begin:end].tolist(),
            "actual_times": times[begin:end].tolist(), "coordinates": {"x": x.tolist(), "y": y.tolist()},
            "unit_evidence": [{"solnum": number} for number in manifest["solution_numbers"][begin:end]],
            "npz_sha256": _sha(batch / "field_sample.npz"),
        }
        _write(batch / "metadata.json", metadata)
        _write(batch / "bridge_response.json", {"synthetic_fixture": True,
               "description": "Analytic saved-data fixture; no COMSOL process was run"})
        manifest["batches"].append({
            "path": name, "start_index": begin, "stop_index": end, "count": end - begin,
            "npz_sha256": _sha(batch / "field_sample.npz"),
            "metadata_sha256": _sha(batch / "metadata.json"),
            "response_sha256": _sha(batch / "bridge_response.json"),
        })
    _write(directory / "sequence_manifest.json", manifest)
    return directory, manifest, full
