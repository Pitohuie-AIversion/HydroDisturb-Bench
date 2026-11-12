"""Independently audit baseline metrics from pinned indexes and saved NPZ files.

This module deliberately imports no project reader, forecaster or accumulator.
It checks sampled-node arithmetic, not COMSOL physics or held-out independence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

METHODS = ("persistence", "last_two_linear")
METRICS = ("sae", "sse", "count", "mae", "rmse")
ATOL, RTOL, TIME_ATOL = 1e-14, 1e-11, 1e-12
ARRAYS = {"values", "mask", "invalid_mask", "times", "x", "y", "cylinder_mask"}
BATCH_FILES = (("field_sample.npz", "npz_sha256"), ("metadata.json", "metadata_sha256"),
               ("bridge_response.json", "response_sha256"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def parse_json(content):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(content, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def load_json(path):
    return parse_json(Path(path).read_bytes())


def integer(value):
    return type(value) is int


def counts(windows):
    targets = Counter((w["trajectory_id"], i) for w in windows
                      for i in range(w["future_start"], w["future_stop"]))
    assignments = sum(targets.values())
    return {"window_count": len(windows), "forecast_frame_assignments": assignments,
            "unique_target_frames": len(targets), "repeated_future_assignments": assignments - len(targets),
            "target_frames_seen_more_than_once": sum(v > 1 for v in targets.values())}


def selection(index, report):
    payload = dict(index)
    stored = payload.pop("payload_sha256")
    require(hashlib.sha256(canonical(payload)).hexdigest() == stored, "Index payload digest mismatch")
    require(index["schema_version"] == 1 and index["status"] == "PASS"
            and index["operation"] == "build_window_index", "Invalid index contract")
    require(report["schema_version"] == 1 and report["status"] == "PASS", "Report did not pass")
    mode, split = report["mode"], report["split"]
    require((mode, split) in (("pipeline_smoke", "train"), ("heldout", "validation"), ("heldout", "test")),
            "Invalid evaluation population")
    require(report["heldout_evaluation"] is (mode == "heldout")
            and report["training_performed"] is False, "Invalid training/heldout claim")
    require(report["channels"] == ["u", "v", "p"] and report["units"] == ["m/s", "m/s", "Pa"],
            "Channels or physical units differ")
    expected_units = {name: ["m/s", "m/s", "Pa"] for name in ("mae", "rmse", "sae")}
    expected_units.update(sse=["(m/s)^2", "(m/s)^2", "Pa^2"], count=["1", "1", "1"])
    require(report["metric_units"] == expected_units, "Metric physical units differ")
    require(set(report["method_definitions"]) == set(METHODS), "Audit requires both fixed methods")
    require(report["identity_audit"]["index_payload_sha256"] == stored
            and report["identity_audit"]["inputs_unchanged"] is True, "Report index identity differs")
    spec = index["spec"]
    require(all(integer(spec[k]) and spec[k] > 0 for k in ("history_frames", "forecast_frames", "stride"))
            and spec["history_frames"] >= 2, "Invalid window specification")
    trajectories = index["trajectories"]
    require(len({t["trajectory_id"] for t in trajectories}) == len(trajectories), "Duplicate trajectory")
    expected = []
    length = spec["history_frames"] + spec["forecast_frames"]
    for item in trajectories:
        require(integer(item["frame_count"]) and item["frame_count"] >= length, "Invalid frame count")
        require(item["split"] == index["family_assignments"][item["family_id"]], "Family split differs")
        for start in range(0, item["frame_count"] - length + 1, spec["stride"]):
            middle = start + spec["history_frames"]
            expected.append({"window_id": f"{item['trajectory_id']}:{start:08d}",
                "trajectory_id": item["trajectory_id"], "family_id": item["family_id"], "split": item["split"],
                "history_start": start, "history_stop": middle, "future_start": middle,
                "future_stop": start + length})
    require(canonical(expected) == canonical(index["windows"]), "Index window boundaries differ")
    chosen = [t for t in trajectories if t["split"] == split]
    windows = [w for w in expected if w["split"] == split]
    require(chosen and windows, "Selected split is empty")
    if mode == "heldout":
        train = {t["family_id"] for t in trajectories if t["split"] == "train"}
        require(train and train.isdisjoint(t["family_id"] for t in chosen), "Heldout families overlap or lack train")
    return chosen, windows, spec


class SavedInputs:
    """Own hashing and bounded raw-NPZ reads, independent of production readers."""

    def __init__(self):
        self.files = {}

    def bind(self, path, expected, kind, name):
        path = Path(path).resolve()
        actual = sha(path)
        require(isinstance(expected, str) and actual == expected.lower(), f"Input hash mismatch: {path.name}")
        row = {"path": str(path), "sha256": actual, "kind": kind, "trajectory_id": name}
        require(str(path) not in self.files or self.files[str(path)] == row, "Input identity changed")
        self.files[str(path)] = row

    def manifest(self, record):
        directory = Path(record["sequence_dir"]).resolve()
        path = directory / "sequence_manifest.json"
        self.bind(path, record["manifest_sha256"], "manifest", record["trajectory_id"])
        manifest = load_json(path)
        require(manifest["status"] == "PASS" and manifest["schema_version"] == 1
                and manifest["operation"] == "export_full_field_sequence", "Unaccepted sequence")
        require(manifest["channel_order"] == ["u", "v", "p"] and manifest["units"] == ["m/s", "m/s", "Pa"]
                and manifest["tensor_layout"] == "(T, 3, Ny, Nx)", "Sequence channel contract differs")
        require(manifest["source_sha256"].lower() == record["source_sha256"].lower()
                and Path(manifest["source_model"]).resolve() == Path(record["source_model"]).resolve(),
                "Sequence source identity differs")
        times = np.asarray(manifest["actual_times"], dtype=float)
        require(times.shape == (record["frame_count"],) and np.isfinite(times).all()
                and np.all(np.diff(times) > 0), "Invalid sequence times")
        require(np.allclose(times, manifest["requested_times"], rtol=0, atol=TIME_ATOL)
                and np.allclose(np.diff(times), manifest["time_step_s"], rtol=0, atol=TIME_ATOL),
                "Sequence saved-time schedule differs")
        nx, ny = manifest["grid_shape"]
        require(integer(nx) and integer(ny) and min(nx, ny) >= 3
                and manifest["shape"] == [len(times), 3, ny, nx], "Invalid sequence shape")
        numbers = manifest["solution_numbers"]
        require(len(numbers) == len(times) and all(integer(n) and n > 0 for n in numbers)
                and all(b == a + 1 for a, b in zip(numbers, numbers[1:])), "Invalid saved solution numbers")
        cursor, names = 0, set()
        for batch in manifest["batches"]:
            name = batch["path"]
            require(isinstance(name, str) and Path(name).name == name and "/" not in name and "\\" not in name
                    and name not in names, "Invalid batch path")
            start, stop, count = (batch[k] for k in ("start_index", "stop_index", "count"))
            require(all(integer(v) for v in (start, stop, count)) and start == cursor
                    and stop - start == count and 1 <= count <= 32 and stop <= len(times), "Invalid batch topology")
            cursor = stop
            names.add(name)
        require(cursor == len(times) and names == {p.name for p in directory.glob("batch_*") if p.is_dir()},
                "Missing or extra batches")
        return manifest

    def window(self, record, manifest, window, reference):
        start, stop = window["history_start"], window["future_stop"]
        nx, ny = manifest["grid_shape"]
        values = np.empty((stop - start, 3, ny, nx))
        masks = np.empty((stop - start, ny, nx), dtype=bool)
        times = np.empty(stop - start)
        directory = Path(record["sequence_dir"])
        for batch in manifest["batches"]:
            a, b = batch["start_index"], batch["stop_index"]
            lo, hi = max(a, start), min(b, stop)
            if lo >= hi:
                continue
            folder = directory / batch["path"]
            for filename, key in BATCH_FILES:
                self.bind(folder / filename, batch[key], filename, record["trajectory_id"])
            metadata = load_json(folder / "metadata.json")
            require(metadata["source_model"] == manifest["source_model"]
                    and metadata["source_sha256"] == manifest["source_sha256"]
                    and metadata["channel_order"] == manifest["channel_order"]
                    and metadata["units"] == manifest["units"], "Batch source or units differ")
            require([e["solnum"] for e in metadata["unit_evidence"]] == manifest["solution_numbers"][a:b],
                    "Batch solution numbers differ")
            with np.load(folder / "field_sample.npz", allow_pickle=False) as archive:
                require(set(archive.files) == ARRAYS, "Unexpected NPZ arrays")
                data = {key: archive[key] for key in ARRAYS}
            field, mask, invalid, cylinder = (data[k] for k in ("values", "mask", "invalid_mask", "cylinder_mask"))
            require(field.dtype == np.float64 and field.shape == (b-a, 3, ny, nx), "Invalid NPZ field shape/dtype")
            require(mask.dtype == invalid.dtype == cylinder.dtype == np.bool_
                    and mask.shape == invalid.shape == (b-a, ny, nx) and cylinder.shape == (ny, nx),
                    "Invalid NPZ masks")
            require(np.array_equal(mask, invalid | cylinder[None]) and np.all(mask == mask[:1])
                    and np.any(~mask[0]), "Invalid or changing spatial mask")
            expanded = np.broadcast_to(mask[:, None], field.shape)
            require(np.isnan(field[expanded]).all() and np.isfinite(field[~expanded]).all(),
                    "Masked values must be NaN and fluid values finite")
            require(data["times"].shape == (b-a,) and np.isfinite(data["times"]).all()
                    and np.allclose(data["times"], manifest["actual_times"][a:b], rtol=0, atol=TIME_ATOL),
                    "NPZ actual times differ")
            require(data["x"].shape == (nx,) and data["y"].shape == (ny,)
                    and all(np.isfinite(data[k]).all() and np.all(np.diff(data[k]) > 0) for k in ("x", "y")),
                    "Invalid NPZ coordinates")
            static = {"mask": mask[0], "x": data["x"], "y": data["y"], "cylinder": cylinder}
            if not reference:
                reference.update({k: v.copy() for k, v in static.items()})
            require(all(np.array_equal(reference[k], v) for k, v in static.items()), "Trajectory mask/grid changed")
            source, target = slice(lo-a, hi-a), slice(lo-start, hi-start)
            values[target], masks[target], times[target] = field[source], mask[source], data["times"][source]
            del data, field, mask, invalid, cylinder, expanded, static
            for filename, key in BATCH_FILES:
                self.bind(folder / filename, batch[key], filename, record["trajectory_id"])
        require(np.all(masks == masks[:1]) and np.all(np.diff(times) > 0), "Window mask/time changed")
        return values, masks, times


def independent_sums(values, masks, times, history_count):
    """Formula evaluation and scalar fsum; no future values enter predictions."""
    last, previous = history_count - 1, history_count - 2
    dt = float(times[last] - times[previous])
    leads = times[history_count:] - times[last]
    require(np.isfinite(dt) and dt > 0 and np.isfinite(leads).all() and np.all(leads > 0), "Invalid past/future times")
    fluid = ~masks[0]
    observed, earlier = values[last][:, fluid], values[previous][:, fluid]
    result = {}
    for method in METHODS:
        sae, sse = [], []
        for lead_index, lead in enumerate(leads):
            prediction = observed.copy()
            if method == "last_two_linear":
                prediction += (observed - earlier) / dt * float(lead)
            require(np.isfinite(prediction).all(), "Independent prediction overflowed")
            errors = prediction - values[history_count + lead_index][:, fluid]
            sae.append([math.fsum(abs(e) for e in row.tolist()) for row in errors])
            sse.append([math.fsum(e * e for e in row.tolist()) for row in errors])
        result[method] = {"sae": sae, "sse": sse,
                          "count": [[int(np.count_nonzero(fluid))] * 3 for _ in leads]}
    return result, leads


def aggregate(parts):
    result = {}
    for method in METHODS:
        shape = np.asarray(parts[0][method]["sae"]).shape
        totals = {key: [[(sum if key == "count" else math.fsum)(part[method][key][i][j] for part in parts)
                        for j in range(3)] for i in range(shape[0])] for key in ("sae", "sse", "count")}
        totals["mae"] = [[totals["sae"][i][j] / totals["count"][i][j] for j in range(3)] for i in range(shape[0])]
        totals["rmse"] = [[math.sqrt(totals["sse"][i][j] / totals["count"][i][j]) for j in range(3)]
                          for i in range(shape[0])]
        result[method] = totals
    return result


def compare_metrics(actual, expected, label, residuals):
    require(set(actual) == set(METHODS), f"Methods differ: {label}")
    for method in METHODS:
        require(set(actual[method]) == set(METRICS), "Unexpected metric fields")
        for metric in METRICS:
            left, right = np.asarray(actual[method][metric]), np.asarray(expected[method][metric])
            require(left.shape == right.shape and left.dtype.kind in "fiu" and np.isfinite(left).all()
                    and np.all(left >= 0), f"Invalid metric array: {label}/{method}/{metric}")
            if metric == "count":
                require(left.dtype.kind in "iu" and np.array_equal(left, right), f"Count mismatch: {label}/{method}")
                continue
            error = np.abs(left - right)
            nonzero = right != 0
            residuals.append({"scope": label, "method": method, "metric": metric,
                "max_absolute_residual": float(error.max()),
                "max_relative_residual_nonzero_reference": float((error[nonzero] / np.abs(right[nonzero])).max())
                if nonzero.any() else 0.,
                "zero_reference_max_absolute_residual": float(error[~nonzero].max()) if (~nonzero).any() else 0.})
            require(np.all(error <= ATOL + RTOL * np.abs(right)), f"Metric mismatch: {label}/{method}/{metric}")


def audit_forecast(index_path, index_sha256, report_path, output):
    index_path, report_path, output = (Path(p).resolve() for p in (index_path, report_path, output))
    require(re.fullmatch(r"[0-9a-fA-F]{64}", index_sha256) is not None, "Invalid index SHA-256")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    require(output not in (index_path, report_path), "Output overlaps an input")
    index_bytes, report_bytes = index_path.read_bytes(), report_path.read_bytes()
    require(hashlib.sha256(index_bytes).hexdigest() == index_sha256.lower(), "Index file SHA-256 mismatch")
    index, report = parse_json(index_bytes), parse_json(report_bytes)
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    trajectories, windows, spec = selection(index, report)
    require(not any(output.is_relative_to(Path(t["sequence_dir"]).resolve()) for t in index["trajectories"]),
            "Output must not be inside a source sequence")
    inputs, all_parts, summaries, residuals = SavedInputs(), [], [], []
    reported = report["per_trajectory"]
    require([t["trajectory_id"] for t in reported] == [t["trajectory_id"] for t in trajectories],
            "Reported trajectory selection differs")
    expected_leads = np.asarray(report["lead_seconds"], dtype=float)
    require(expected_leads.shape == (spec["forecast_frames"],) and np.isfinite(expected_leads).all()
            and np.all(expected_leads > 0) and np.all(np.diff(expected_leads) > 0), "Invalid report leads")
    for record, stated in zip(trajectories, reported, strict=True):
        manifest = inputs.manifest(record)
        own_windows = [w for w in windows if w["trajectory_id"] == record["trajectory_id"]]
        parts, reference = [], {}
        for window in own_windows:
            values, masks, times = inputs.window(record, manifest, window, reference)
            sums, leads = independent_sums(values, masks, times, spec["history_frames"])
            require(np.allclose(leads, expected_leads, rtol=0, atol=TIME_ATOL), "Forecast leads differ")
            parts.append(sums)
            all_parts.append(sums)
            del values, masks, times
        require(stated["family_id"] == record["family_id"]
                and stated["static_mask_sha256"] == hashlib.sha256(reference["mask"].tobytes()).hexdigest(),
                "Reported family/mask differs")
        own_counts = counts(own_windows)
        require(all(integer(stated[k]) and stated[k] == v for k, v in own_counts.items()), "Trajectory counts differ")
        metrics = aggregate(parts)
        compare_metrics(stated["methods"], metrics, record["trajectory_id"], residuals)
        summaries.append({"trajectory_id": record["trajectory_id"], **own_counts, "methods": metrics})
    totals = counts(windows)
    require(all(integer(report["overall"][k]) and report["overall"][k] == v for k, v in totals.items()),
            "Overall window/frame counts differ")
    overall = aggregate(all_parts)
    compare_metrics(report["overall"]["methods"], overall, "overall", residuals)
    declared = report["identity_audit"]["files"]
    require(len(declared) == len(inputs.files) == report["identity_audit"]["files_checked"]
            and sorted(declared, key=lambda v: v["path"]) == sorted(inputs.files.values(), key=lambda v: v["path"]),
            "Reported source file identities differ")
    require(all(sha(row["path"]) == row["sha256"] for row in inputs.files.values())
            and sha(index_path) == index_sha256.lower() and sha(report_path) == report_sha,
            "Inputs changed during independent audit")
    result = {"schema_version": 1, "status": "PASS", "operation": "audit_forecast_baselines",
        "index_sha256": index_sha256.lower(), "index_payload_sha256": index["payload_sha256"],
        "report_sha256": report_sha, "mode": report["mode"], "split": report["split"],
        "lead_seconds": expected_leads.tolist(), "channels": ["u", "v", "p"], "units": ["m/s", "m/s", "Pa"],
        "tolerances": {"absolute": ATOL, "relative": RTOL, "lead_absolute_s": TIME_ATOL, "count": "exact"},
        "arithmetic": {"prediction": "independent float64 formulas from final one/two observed frames",
                       "summation": "math.fsum per sampled-node error and across windows",
                       "float64_mantissa_bits": np.finfo(np.float64).nmant,
                       "longdouble_mantissa_bits": np.finfo(np.longdouble).nmant},
        "overall": {**totals, "methods": overall}, "per_trajectory": summaries, "residuals": residuals,
        "source_identity": list(inputs.files.values()), "inputs_unchanged": True,
        "past_only_windows_checked": len(windows), "future_values_used_only_as_targets": True,
        "scope": "Selected forecast instances and original sampled fluid nodes; not CFD or native-state revalidation",
        "project_readers_or_forecasters_called": False, "solver_called": False, "new_tensor_files": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--index-sha256", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = audit_forecast(args.index, args.index_sha256, args.report, args.output)
    print(json.dumps({"status": result["status"], "window_count": result["overall"]["window_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
