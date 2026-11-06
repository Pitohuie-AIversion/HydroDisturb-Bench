"""Audit archived branch fields offline, without COMSOL or an MPH model.

Run from a checkout with the project installed (or PYTHONPATH=src):
python audit_branch_fields.py --run EXTRACTED_RUN --baseline BASELINE_SEQUENCE --output NEW.json
The optional baseline enables comparison with the archived analysis and independent quadrature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np

from hydro_disturb_bench.branch_pulse import compare_branch_fields
from hydro_disturb_bench.field_sequence import load_full_field_sequence

ARRAYS = {"values", "mask", "invalid_mask", "cylinder_mask", "x", "y", "times"}
TIME_ATOL = COORD_ATOL = 1e-12
VALUE_ATOL, VALUE_RTOL = 1e-10, 1e-9


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_sequence(directory):
    sequence = load_full_field_sequence(directory)
    manifest = sequence["metadata"]
    require(sequence["values"].shape == (101, 3, 97, 161), "Expected full 101-frame branch")
    require(np.max(np.abs(sequence["times"] - np.linspace(180, 200, 101))) <= TIME_ATOL,
            "Branch saved-time schedule differs")
    result = {"shape": list(sequence["values"].shape), "batches": [], "raw_files": 0,
              "fluid_channel_values": 0, "raw_nan_channel_values": 0,
              "masked_tensor_nan_channel_values": 0, "direct_space_time_points": 0,
              "direct_channel_comparisons": 0, "maximum_raw_absolute_errors": [0., 0., 0.],
              "maximum_direct_absolute_errors": [0., 0., 0.],
              "maximum_coordinate_error_m": 0., "maximum_time_error_s": 0.}
    for entry in manifest["batches"]:
        batch_dir = directory / entry["path"]
        metadata = read_json(batch_dir / "metadata.json")
        response = read_json(batch_dir / "bridge_response.json")
        with np.load(batch_dir / "field_sample.npz", allow_pickle=False) as archive:
            require(set(archive.files) == ARRAYS, "NPZ must contain exactly seven arrays")
            batch = {name: archive[name] for name in ARRAYS}
        start, stop = entry["start_index"], entry["stop_index"]
        for name, array in batch.items():
            expected = sequence[name][start:stop] if name in {
                "values", "mask", "invalid_mask", "times"} else sequence[name]
            require(np.array_equal(array, expected, equal_nan=True), f"Batch array changed: {name}")
        x, y = batch["x"], batch["y"]
        xx, yy = np.meshgrid(x, y)
        expected_coordinates = np.column_stack((xx.ravel(), yy.ravel()))
        exports = metadata["unit_evidence"]
        require(exports == response["exports"], "Metadata export evidence differs from raw response")
        require(len(exports) == len(batch["times"]), "Export/time count mismatch")
        raw_names = set()
        for ti, export in enumerate(exports):
            filename = export["filename"]
            require(Path(filename).name == filename and "/" not in filename and "\\" not in filename,
                    "Unsafe raw filename")
            require(filename not in raw_names, "Duplicate raw filename")
            raw_names.add(filename)
            path = batch_dir / filename
            require(sha256(path) == metadata["raw_files"][filename].lower(), "Raw file hash mismatch")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            headers = [line[1:].strip() for line in lines if line.startswith("%")]
            require("Length unit:        m" in headers or any(
                h.startswith("Length unit:") and h.split(":", 1)[1].strip() == "m"
                for h in headers), "Missing actual length unit m")
            columns = [h for h in headers if re.match(r"^x\s+y\s+", h)]
            require(len(columns) == 1, "Missing/duplicate coordinate field header")
            remainder = re.sub(r"^x\s+y\s+", "", columns[0])
            pattern = r"([uvp])\s+\((m/s|Pa)\)\s+@\s+t=([^\s]+)"
            fields = re.findall(pattern, remainder)
            require(len(fields) == 3 and not re.sub(pattern, "", remainder).strip(),
                    "Unrecognized expression/unit/time header")
            require([(expr, unit) for expr, unit, _ in fields]
                    == [("u", "m/s"), ("v", "m/s"), ("p", "Pa")], "Wrong raw channels/units")
            actual_t = float(batch["times"][ti])
            time_error = max(abs(float(value) - actual_t) for _, _, value in fields)
            require(time_error <= TIME_ATOL and abs(export["actual_t"] - actual_t) <= TIME_ATOL,
                    "Header/export time differs from NPZ saved time")
            require(export["expressions"] == ["u", "v", "p"]
                    and export["units"] == ["m/s", "m/s", "Pa"], "Export units differ")
            raw = np.loadtxt(path, comments="%", encoding="utf-8-sig", ndmin=2)
            require(raw.shape == (len(x) * len(y), 5), "Wrong raw grid shape")
            require(np.isfinite(raw[:, :2]).all(), "Nonfinite raw coordinates")
            raw = raw[np.lexsort((raw[:, 0], raw[:, 1]))]
            require(len(np.unique(raw[:, :2], axis=0)) == len(raw), "Duplicate raw coordinate")
            coordinate_error = float(np.max(np.abs(raw[:, :2] - expected_coordinates)))
            require(coordinate_error <= COORD_ATOL, "Raw coordinates differ from tensor axes")
            raw_values = raw[:, 2:].reshape(len(y), len(x), 3).transpose(2, 0, 1)
            valid = ~batch["mask"][ti]
            require(np.isfinite(raw_values[:, valid]).all(), "Nonfinite raw fluid data")
            require(np.array_equal(raw_values[:, valid], batch["values"][ti][:, valid]),
                    "Raw fluid values differ from tensor")
            require(np.array_equal(~np.isfinite(raw_values).all(axis=0), batch["invalid_mask"][ti]),
                    "Raw invalid mask differs from NPZ")
            require(np.isnan(batch["values"][ti, :, ~valid]).all(), "Masked tensor data is not NaN")
            result["raw_files"] += 1
            result["fluid_channel_values"] += int(valid.sum()) * 3
            result["raw_nan_channel_values"] += int(np.isnan(raw_values).sum())
            result["masked_tensor_nan_channel_values"] += int((~valid).sum()) * 3
            result["maximum_coordinate_error_m"] = max(result["maximum_coordinate_error_m"], coordinate_error)
            result["maximum_time_error_s"] = max(result["maximum_time_error_s"], time_error)
        require(raw_names == set(metadata["raw_files"])
                == {p.name for p in batch_dir.glob("*.txt")}, "Raw file inventory differs")
        direct_rows = response["direct_evaluations"]
        require(len(direct_rows) == len(metadata["direct_eval_checks"]), "Direct check count differs")
        counts = [set() for _ in batch["times"]]
        for check, original in zip(metadata["direct_eval_checks"], direct_rows):
            require(all(check[key] == value for key, value in original.items()),
                    "Direct-check metadata changed actual COMSOL response")
            ti, ix, iy = (check[key] for key in ("time_index", "grid_ix", "grid_iy"))
            require(not batch["mask"][ti, iy, ix] and (ix, iy) not in counts[ti],
                    "Invalid/duplicate direct point")
            counts[ti].add((ix, iy))
            require(max(abs(check["actual_t"] - batch["times"][ti]),
                        abs(check["target_t"] - batch["times"][ti])) <= TIME_ATOL,
                    "Direct actual/requested time differs")
            require(max(abs(check["x"] - x[ix]), abs(check["y"] - y[iy]),
                        abs(check["requested_x"] - x[ix]),
                        abs(check["requested_y"] - y[iy])) <= COORD_ATOL, "Direct coordinate differs")
            sampled = batch["values"][ti, :, iy, ix]
            direct = np.asarray(check["values"])
            require(np.array_equal(sampled, check["sampled"]), "Stored sampled check differs")
            error = np.abs(sampled - direct)
            require(np.array_equal(error, check["absolute_errors"]), "Stored direct error differs")
            require(np.allclose(sampled, direct, rtol=VALUE_RTOL, atol=VALUE_ATOL),
                    "Direct COMSOL values differ beyond the existing sampling contract")
            result["maximum_direct_absolute_errors"] = np.maximum(
                result["maximum_direct_absolute_errors"], error).tolist()
            result["direct_space_time_points"] += 1
            result["direct_channel_comparisons"] += 3
        require(all(len(points) == 3 for points in counts), "Expected three direct points per frame")
        result["batches"].append({"path": entry["path"], "frames": len(batch["times"]),
                                  "seven_arrays_read_back": True})
    result["status"] = "PASS"
    return sequence, result


def trapezoid(values, axis_values, axis=-1):
    """Independent adjacent-interval quadrature, including endpoint half weights."""
    moved = np.moveaxis(values, axis, -1)
    return np.sum((moved[..., 1:] + moved[..., :-1]) * np.diff(axis_values) / 2, axis=-1)


def independent_metrics(baseline, zero, pulse, plan):
    indices = []
    for time in zero["times"]:
        matches = np.flatnonzero(np.abs(baseline["times"] - time) <= TIME_ATOL)
        require(len(matches) == 1, "Baseline time has no unique exact match")
        indices.append(int(matches[0]))
    b, z, p = baseline["values"][indices], zero["values"], pulse["values"]
    x, y, times = zero["x"], zero["y"], zero["times"]
    fluid = ~zero["mask"][0]

    def area_integral(values):
        return trapezoid(trapezoid(values, x, -1), y, -1)

    area = area_integral(fluid.astype(float))
    speed_scale = plan["scales"]["U_inf_m_s"]
    pressure_scale = plan["scales"]["rho_f_kg_m3"] * speed_scale**2
    difference = np.where(fluid, z - b, 0)
    velocity_squared = area_integral(np.sum(difference[:, :2]**2, axis=1)) / area / speed_scale**2
    pressure_difference = difference[:, 2]
    pressure_mean = area_integral(pressure_difference) / area
    pressure_centered = np.where(fluid, pressure_difference - pressure_mean[:, None, None], 0)
    pressure_squared = area_integral(pressure_centered**2) / area / pressure_scale**2
    xx, yy = np.meshgrid(x, y)
    left, right, bottom, top = plan["response_acceptance"]["roi_m"]
    roi = fluid & (xx >= left - COORD_ATOL) & (xx <= right + COORD_ATOL)
    roi &= (yy >= bottom - COORD_ATOL) & (yy <= top + COORD_ATOL)
    delta = np.where(roi, p[:, :2] - z[:, :2], 0)
    roi_response = np.sqrt(area_integral(np.sum(delta**2, axis=1))
                           / area_integral(roi.astype(float))) / speed_scale
    rules = plan["response_acceptance"]
    start, stop = rules["response_window_s"]
    window = times > start + TIME_ATOL if rules["response_window_left_open"] else times >= start - TIME_ATOL
    window &= times <= stop + TIME_ATOL
    return {"zero_velocity_time_rms_over_U": float(np.sqrt(trapezoid(velocity_squared, times) / (times[-1] - times[0]))),
            "zero_centered_pressure_time_rms_over_rhoU2": float(np.sqrt(trapezoid(pressure_squared, times) / (times[-1] - times[0]))),
            "response_roi_peak_over_U": float(roi_response[window].max()),
            "response_roi_peak_time_s": float(times[np.flatnonzero(window)[np.argmax(roi_response[window])]])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    branches, audits = {}, {}
    for name in ("zero", "pulse"):
        branches[name], audits[name] = audit_sequence(args.run / f"sample_{name}" / "fields")
    report = {"status": "PASS", "scope": "Offline artifact/point-value audit; no COMSOL launch or MPH dependency",
              "branches": audits, "raw_files_checked": sum(a["raw_files"] for a in audits.values()),
              "direct_space_time_points": sum(a["direct_space_time_points"] for a in audits.values()),
              "direct_channel_comparisons": sum(a["direct_channel_comparisons"] for a in audits.values()),
              "baseline_comparison": "NOT_REQUESTED"}
    if args.baseline:
        baseline = load_full_field_sequence(args.baseline)
        plan = read_json(args.run / "preregistered_plan.json")
        archived = read_json(args.run / "analysis" / "field_comparison.json")
        formal = compare_branch_fields(baseline, branches["zero"], branches["pulse"], plan)
        require(formal == archived, "Formal recomputation differs from archived analysis")
        metrics = independent_metrics(baseline, branches["zero"], branches["pulse"], plan)
        expected = {"zero_velocity_time_rms_over_U": archived["restart"]["velocity"]["time_rms"],
                    "zero_centered_pressure_time_rms_over_rhoU2": archived["restart"]["pressure_centered"]["time_rms"],
                    "response_roi_peak_over_U": archived["response"]["peak_roi_over_U"],
                    "response_roi_peak_time_s": archived["response"]["peak_time_s"]}
        require(all(np.isclose(value, expected[key], rtol=1e-12, atol=1e-14)
                    for key, value in metrics.items()), "Independent quadrature differs")
        report["baseline_comparison"] = {"status": "PASS", "formal_json_exact_match": True,
            "independent_adjacent_interval_quadrature": metrics,
            "absolute_difference_from_archived": {key: abs(value - expected[key]) for key, value in metrics.items()},
            "note": "Arithmetic agreement tolerance only; preregistered physical gates are unchanged."}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
