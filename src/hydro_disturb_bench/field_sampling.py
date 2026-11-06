"""Strict Cartesian u/v/p samples from an existing COMSOL solution.

Grid resolution changes only exported sampling locations. This module never
builds a mesh, modifies physics, runs a study, or saves a COMSOL model.
"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from pathlib import Path

import numpy as np

from .comsol_java import bridge

CHANNELS = ("u", "v", "p")
UNITS = ("m/s", "m/s", "Pa")
TIME_ATOL = 1e-12
COORD_ATOL = 1e-12
VALUE_ATOL = 1e-10
VALUE_RTOL = 1e-9
LAYOUT = "(T, 3, Ny, Nx)"


def _require(condition, message):
    if not condition:
        raise bridge.ModelValidationError(message)


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _same(a, b, atol=TIME_ATOL):
    try:
        left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        return left.shape == right.shape and np.isfinite(left).all() and np.isfinite(right).all() and bool(np.allclose(left, right, rtol=0, atol=atol))
    except (TypeError, ValueError):
        return False


def _cylinder_mask(x, y, geometry):
    circle = geometry["circle"]
    # ⚡ Bolt: using NumPy broadcasting instead of np.meshgrid to avoid (Ny, Nx) memory allocation
    dx = x[None, :] - circle["center"][0]
    dy = y[:, None] - circle["center"][1]
    return np.hypot(dx, dy) <= circle["radius"] + COORD_ATOL


def _parse_grid(path, x, y, actual_time):
    """Read explicit COMSOL headers; require every Cartesian coordinate once."""
    header = None
    length_unit = None
    rows = []
    pattern = re.compile(r"([^\s()@]+)\s+\(([^()]*)\)\s+@\s+t=([^\s]+)")
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("%"):
            body = line[1:].strip()
            if body.lower().startswith("length unit:"):
                length_unit = body.split(":", 1)[1].strip()
            match = re.match(r"^x\s+y\s+(.+)$", body)
            if match:
                _require(header is None and not rows, "Duplicate or misplaced COMSOL field header")
                fields = match[1]
                matches = list(pattern.finditer(fields))
                _require(len(matches) == 3, "Field header must contain exactly three expression/unit/time columns")
                remainder = pattern.sub("", fields)
                _require(not remainder.strip(), "Unrecognized text in COMSOL field header")
                header = [(m[1], m[2].strip(), float(m[3])) for m in matches]
                _require(sorted(item[0] for item in header) == sorted(CHANNELS), "Field header requires unique u, v, p columns")
                for expression, unit, time_value in header:
                    _require(unit == UNITS[CHANNELS.index(expression)], f"Field header unit mismatch for {expression}: {unit}")
                    _require(_same(time_value, actual_time), "Field header time differs from the actual selected saved time")
            continue
        _require(header is not None, f"Data precedes field header at line {line_number}")
        tokens = line.split()
        _require(len(tokens) == 5, f"Expected five coordinate/value columns at line {line_number}")
        try:
            rows.append([float(token) for token in tokens])
        except ValueError as exc:
            raise bridge.ModelValidationError(f"Non-numeric data at line {line_number}") from exc
    _require(header is not None and length_unit == "m", "Missing COMSOL field header or length unit m")
    _require(len(rows) == len(x) * len(y), "COMSOL grid is missing or has extra coordinate rows")
    raw_values = np.asarray(rows, dtype=float)
    _require(np.isfinite(raw_values[:, :2]).all(), "Grid coordinates must be finite")
    _require(not np.isinf(raw_values[:, 2:]).any(), "Infinite field values are invalid")
    values = np.full((3, len(y), len(x)), np.nan)
    seen = np.zeros((len(y), len(x)), dtype=bool)
    column_indices = [2 + [item[0] for item in header].index(c) for c in CHANNELS]
    for row in raw_values:
        ix = int(np.argmin(np.abs(x - row[0])))
        iy = int(np.argmin(np.abs(y - row[1])))
        _require(abs(x[ix] - row[0]) <= COORD_ATOL and abs(y[iy] - row[1]) <= COORD_ATOL, "Exported coordinates do not match the requested full-domain grid")
        _require(not seen[iy, ix], "Duplicate Cartesian coordinate row")
        values[:, iy, ix] = row[column_indices]
        seen[iy, ix] = True
    _require(seen.all(), "Incomplete Cartesian grid")
    return values


def _validate_response(response, model_path, times, shape, bounds):
    nx, ny = shape
    _require(response.get("status") == "SUCCESS" and response.get("operation") == "export_full_field_sample", "Missing successful full-field response")
    _require(Path(response.get("model_path", "")).resolve() == model_path.resolve() and bool(response.get("model_tag")), "Model identity does not match the sampled source")
    dataset = response.get("dataset", {})
    _require(dataset.get("type") == "Solution" and bool(dataset.get("tag")) and bool(dataset.get("solution_tag")), "Missing actual Dataset–Solution association")
    geometry = response.get("geometry", {})
    _require(geometry.get("length_unit") == "m" and _same(geometry.get("bounds"), bounds, COORD_ATOL), "Sampling bounds must match the actual full model domain in metres")
    circle = geometry.get("circle", {})
    _require(circle.get("unit") == "m" and circle.get("feature_tag") and circle.get("source"), "Missing actual cylinder geometry provenance")
    center, radius = np.asarray(circle.get("center"), dtype=float), circle.get("radius")
    _require(center.shape == (2,) and np.isfinite(center).all() and isinstance(radius, (int, float)) and math.isfinite(radius) and radius > 0, "Invalid actual cylinder geometry")
    _require(bounds[0] < center[0] - radius < center[0] + radius < bounds[1] and bounds[2] < center[1] - radius < center[1] + radius < bounds[3], "Cylinder must lie inside the full sampling domain")
    saved = np.asarray(response.get("saved_times"), dtype=float)
    _require(saved.ndim == 1 and saved.size > 0 and np.isfinite(saved).all() and np.all(np.diff(saved) > 0), "Missing unique increasing saved solution times")
    _require(_same(response.get("requested_times"), times), "Requested time echo mismatch")
    actual = np.asarray(response.get("actual_times"), dtype=float)
    _require(_same(actual, times), "Actual times differ from requested saved times")
    selected_indices = []
    for target, at in zip(times, actual):
        matches = np.flatnonzero(np.abs(saved - target) <= TIME_ATOL)
        _require(len(matches) == 1 and saved[matches[0]] == at, "Requested time does not uniquely identify the reported saved solution")
        selected_indices.append(int(matches[0]) + 1)
    x, y = np.linspace(bounds[0], bounds[1], nx), np.linspace(bounds[2], bounds[3], ny)
    grid = response.get("grid", {})
    _require(grid.get("nx") == nx and grid.get("ny") == ny and _same([grid.get(k) for k in ("xmin", "xmax", "ymin", "ymax")], bounds, COORD_ATOL), "Actual grid readback does not match requested dimensions and bounds")
    exports = response.get("exports", [])
    _require(len(exports) == len(times), "Expected exactly one grid export per selected time")
    filenames = []
    for ti, export in enumerate(exports):
        filename = export.get("filename", "")
        _require(filename and Path(filename).name == filename and "/" not in filename and "\\" not in filename and filename not in filenames, "Export filename is missing, duplicate, or unsafe")
        filenames.append(filename)
        _require(export.get("export_type") == "Data" and export.get("export_tag"), "Missing actual Data export node")
        _require(export.get("dataset_tag") == dataset["tag"], "Export dataset differs from inspected Solution dataset")
        _require(export.get("expressions") == list(CHANNELS) and export.get("units") == list(UNITS), "Export expression/unit readback must be u,v,p in m/s,m/s,Pa")
        _require(export.get("solnum") == selected_indices[ti] and _same(export.get("target_t"), times[ti]) and _same(export.get("actual_t"), actual[ti]), "Export selection does not match its exact saved solution time")
        _require(export.get("innerinput") == "manual", "Export must use explicit saved solution numbers")
        _require(_same(export.get("gridx"), x, COORD_ATOL) and _same(export.get("gridy"), y, COORD_ATOL), "Export node coordinate arrays differ from the requested grid")
    return x, y, actual


def _check_direct(response, values, x, y, times, mask):
    node = response.get("direct_evaluation_node", {})
    _require(node.get("type") == "EvalPoint" and node.get("tag") and node.get("dataset_tag") and node.get("point_dataset_type") == "CutPoint2D", "Missing actual direct evaluation node and point dataset")
    _require(node.get("source_dataset_tag") == response["dataset"]["tag"], "Direct evaluation source dataset differs from exported Solution dataset")
    _require(node.get("expressions") == ["u", "v", "p", "x", "y", "t"] and node.get("units") == ["m/s", "m/s", "Pa", "m", "m", "s"], "Direct evaluation expression/unit readback mismatch")
    _require(node.get("solrepresentation") == "solnum" and node.get("innerinput") == "manual", "Direct evaluation must select saved solution numbers")
    checks = []
    counts = [set() for _ in times]
    for item in response.get("direct_evaluations", []):
        _require(item.get("numerical_tag") == node["tag"] and item.get("point_dataset_tag") == node["dataset_tag"], "Direct evaluation row is not bound to its actual node")
        ti, ix, iy = item.get("time_index"), item.get("grid_ix"), item.get("grid_iy")
        _require(all(isinstance(index, int) and not isinstance(index, bool) for index in (ti, ix, iy)), "Direct evaluation indices must be integers")
        _require(0 <= ti < len(times) and 0 <= ix < len(x) and 0 <= iy < len(y) and not mask[ti, iy, ix], "Direct evaluation must select a valid fluid grid coordinate")
        _require((ix, iy) not in counts[ti], "Duplicate direct evaluation cannot count as a new spatial check")
        counts[ti].add((ix, iy))
        _require(_same([item.get("target_t"), item.get("actual_t")], [response["requested_times"][ti], times[ti]]), "Direct evaluation time is missing or mismatched")
        _require(_same([item.get("requested_x"), item.get("requested_y"), item.get("x"), item.get("y")], [x[ix], y[iy], x[ix], y[iy]], COORD_ATOL), "Direct evaluation coordinate differs from the sampled grid coordinate")
        direct = np.asarray(item.get("values"), dtype=float)
        sampled = values[ti, :, iy, ix]
        _require(direct.shape == (3,) and np.isfinite(direct).all(), "Missing finite direct u/v/p evaluation")
        _require(np.allclose(sampled, direct, rtol=VALUE_RTOL, atol=VALUE_ATOL), "Grid sample differs from direct COMSOL evaluation")
        checks.append({**item, "sampled": sampled.tolist(), "absolute_errors": np.abs(sampled-direct).tolist()})
    _require(all(len(points) >= 3 for points in counts), "Require at least three distinct fluid points at every selected time")
    return checks


def load_full_field_sample(grid_dir):
    """Read and validate an exported NPZ using explicit axes, masks and metadata."""
    grid_dir = Path(grid_dir)
    metadata = json.loads((grid_dir / "metadata.json").read_text(encoding="utf-8"))
    _require(metadata.get("schema_version") == 1 and metadata.get("tensor_layout") == LAYOUT, "Unknown field sample schema or tensor layout")
    _require(metadata.get("channel_order") == list(CHANNELS) and metadata.get("units") == list(UNITS), "Invalid field channel order or units")
    path = grid_dir / "field_sample.npz"
    _require(bridge.compute_file_hash(path).lower() == metadata["npz_sha256"].lower(), "NPZ SHA-256 mismatch")
    with np.load(path, allow_pickle=False) as archive:
        required = {"values", "mask", "cylinder_mask", "invalid_mask", "x", "y", "times"}
        _require(set(archive.files) == required, "NPZ has missing or unexpected arrays")
        data = {key: archive[key] for key in required}
    x, y, times = data["x"], data["y"], data["times"]
    _require(all(a.ndim == 1 and np.isfinite(a).all() and np.all(np.diff(a) > 0) for a in (x, y, times)), "NPZ axes/times must be finite and strictly increasing")
    _require(_same(x, metadata["coordinates"]["x"], COORD_ATOL) and _same(y, metadata["coordinates"]["y"], COORD_ATOL) and _same(times, metadata["actual_times"]), "NPZ coordinates/time differ from metadata")
    _require(_same(times, metadata.get("requested_times")), "Requested times differ from actual saved NPZ times")
    _require(data["values"].shape == (len(times), 3, len(y), len(x)) and data["values"].dtype == np.float64, "NPZ tensor must be float64 (T,3,Ny,Nx)")
    _require(metadata.get("shape") == list(data["values"].shape), "Metadata tensor shape differs from NPZ")
    _require(data["cylinder_mask"].dtype == bool and data["cylinder_mask"].shape == (len(y), len(x)), "Cylinder mask must be bool (Ny,Nx)")
    _require(np.array_equal(data["cylinder_mask"], _cylinder_mask(x, y, metadata["geometry"])), "Cylinder mask differs from actual geometry")
    for name in ("mask", "invalid_mask"):
        _require(data[name].dtype == bool and data[name].shape == (len(times), len(y), len(x)), f"{name} must be bool (T,Ny,Nx)")
    _require(not (data["invalid_mask"] & ~data["cylinder_mask"][None]).any(), "Invalid raw samples occur in the fluid domain")
    _require(np.array_equal(data["mask"], data["cylinder_mask"][None] | data["invalid_mask"]), "Combined mask is inconsistent")
    expanded_mask = np.broadcast_to(data["mask"][:, None], data["values"].shape)
    _require(np.isnan(data["values"][expanded_mask]).all() and np.isfinite(data["values"][~expanded_mask]).all(), "Masked entries must be NaN and fluid entries finite")
    data["metadata"] = metadata
    return data


def _export_grid_sample(model_path, grid_dir, times, shape, bounds, before, *, root=None,
                        command="export-full-field-sample", process_runner=None):
    """Run the shared per-grid validation and write one immutable NPZ batch."""
    nx, ny = shape
    grid_dir.mkdir()
    process_options = {} if process_runner is None else {"process_runner": process_runner}
    response = bridge.run_command([command, str(model_path), str(grid_dir),
        ",".join(format(t, ".17g") for t in times), str(nx), str(ny),
        *(format(v, ".17g") for v in bounds)], root=root, **process_options)
    _write_json(grid_dir / "bridge_response.json", response)
    if command == "export-full-field-sequence-batch":
        _require(response.get("batch_mode") is True, "Missing explicit sequence batch mode")
    x, y, actual = _validate_response(response, model_path, times, (nx, ny), bounds)
    raw_files = {item["filename"] for item in response["exports"]}
    _require({path.name for path in grid_dir.glob("*.txt")} == raw_files, "Missing or extra raw field exports")
    raw_values = np.stack([_parse_grid(grid_dir / item["filename"], x, y, actual[ti]) for ti, item in enumerate(response["exports"])])
    cylinder = _cylinder_mask(x, y, response["geometry"])
    invalid = ~np.isfinite(raw_values).all(axis=1)
    _require(not (invalid & ~cylinder[None]).any(), "COMSOL produced invalid values outside the cylinder mask")
    mask = cylinder[None] | invalid
    values = np.where(mask[:, None], np.nan, raw_values)
    checks = _check_direct(response, values, x, y, actual, mask)
    np.savez_compressed(grid_dir / "field_sample.npz", values=values, mask=mask,
                        cylinder_mask=cylinder, invalid_mask=invalid, x=x, y=y, times=actual)
    metadata = {"schema_version": 1, "source_model": str(model_path), "source_sha256": before,
        "model_tag": response["model_tag"], "dataset": response["dataset"], "geometry": response["geometry"],
        "requested_times": times.tolist(), "actual_times": actual.tolist(),
        "tensor_layout": LAYOUT, "shape": list(values.shape), "channel_order": list(CHANNELS),
        "units": response["exports"][0]["units"], "coordinates": {"x": x.tolist(), "y": y.tolist(), "unit": "m", "includes_endpoints": True, "increasing": True},
        "mask_semantics": {"cylinder_mask": "r <= R + 1e-12 m, includes the cylinder boundary", "invalid_mask": "any raw u/v/p value non-finite at this time and coordinate", "mask": "union of cylinder_mask and invalid_mask; masked values stored as NaN; non-finite fluid values reject export"},
        "unit_evidence": response["exports"], "direct_evaluation_node": response["direct_evaluation_node"], "direct_eval_checks": checks,
        "validation_tolerances": {"time_absolute_s": TIME_ATOL, "coordinate_absolute_m": COORD_ATOL, "value_absolute": VALUE_ATOL, "value_relative": VALUE_RTOL},
        "npz_sha256": bridge.compute_file_hash(grid_dir / "field_sample.npz"),
        "raw_files": {name: bridge.compute_file_hash(grid_dir / name) for name in sorted(raw_files)}}
    _write_json(grid_dir / "metadata.json", metadata)
    loaded = load_full_field_sample(grid_dir)
    _require(np.array_equal(loaded["values"], values, equal_nan=True), "NPZ values changed on readback")
    return {"path": grid_dir.name, "shape": list(values.shape),
        "masked_points_per_time": mask.sum(axis=(1, 2)).tolist(), "raw_invalid_points_per_time": invalid.sum(axis=(1, 2)).tolist(),
        "cylinder_points": int(cylinder.sum()), "direct_checks": len(checks),
        "max_direct_absolute_errors": np.max([c["absolute_errors"] for c in checks], axis=0).tolist(),
        "readback_passed": True, "npz_sha256": metadata["npz_sha256"]}


def export_full_field_sample(model_path, output_dir, requested_times=(180., 185., 190., 195., 200.),
                             grid_shapes=((81, 49), (161, 97)), bounds=(0., .4, 0., .24),
                             *, allow_overwrite=False, root=None, process_runner=None):
    """Export two small samples into a new directory; retain failed staging evidence."""
    model_path, output_dir = Path(model_path).resolve(), Path(output_dir).absolute()
    if not model_path.is_file():
        raise bridge.ModelNotFoundError(f"Model file does not exist: {model_path}")
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Sampling output already exists: {output_dir}")
    if allow_overwrite:
        raise bridge.InvalidParameterError("Field samples always require a new output directory")
    times = np.asarray(requested_times, dtype=float)
    if times.ndim != 1 or not 3 <= len(times) <= 5 or not np.isfinite(times).all() or not np.all(np.diff(times) > 0):
        raise bridge.InvalidParameterError("Request 3–5 finite, unique, increasing saved times")
    if len(grid_shapes) != 2 or len({tuple(shape) for shape in grid_shapes}) != 2:
        raise bridge.InvalidParameterError("Require two different Cartesian grid resolutions")
    for shape in grid_shapes:
        if len(shape) != 2 or any(not isinstance(v, (int, np.integer)) or isinstance(v, (bool, np.bool_)) or v < 3 for v in shape):
            raise bridge.InvalidParameterError("Grid nx,ny must be integers >=3")
    bounds = np.asarray(bounds, dtype=float)
    if bounds.shape != (4,) or not np.isfinite(bounds).all() or bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
        raise bridge.InvalidParameterError("Require finite increasing x/y bounds")
    before = bridge.compute_file_hash(model_path)
    stage = output_dir.parent / (".staging_full_field_" + uuid.uuid4().hex[:12])
    stage.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "operation": "export_full_field_sample", "source_model": str(model_path),
              "source_sha256": before, "requested_times": times.tolist(), "tensor_layout": LAYOUT,
              "channel_order": list(CHANNELS), "units": list(UNITS), "grids": [], "solver_called": False}
    try:
        process_options = {} if process_runner is None else {"process_runner": process_runner}
        for nx, ny in grid_shapes:
            grid_dir = stage / f"grid_{nx}x{ny}"
            report["grids"].append(_export_grid_sample(
                model_path, grid_dir, times, (nx, ny), bounds, before, root=root,
                **process_options))
        _require(bridge.compute_file_hash(model_path) == before, "Source model changed during sampling")
        report.update(status="PASS", output_dir=str(output_dir.resolve()), source_unchanged=True)
        _write_json(stage / "sample_report.json", report)
        with bridge.file_publish_lock(output_dir.parent / ".locks" / (output_dir.name + ".lock")):
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError(f"Sampling output already exists: {output_dir}")
            if os.name == "nt":
                os.rename(stage, output_dir)
            else:
                bridge._posix_rename_noreplace(stage, output_dir)
        return report
    except Exception as exc:
        after = bridge.compute_file_hash(model_path)
        _write_json(stage / "failure.json", {"status": "FAIL", "error": str(exc), "details": getattr(exc, "details", ""),
                    "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": before == after,
                    "preserved_staging_directory": str(stage), "intended_output": str(output_dir)})
        raise
