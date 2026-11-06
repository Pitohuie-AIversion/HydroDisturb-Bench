"""Index the registered M3 trajectories without solving or copying field tensors."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench.data_windows import (
    TrajectoryRecord,
    WindowSpec,
    build_window_index,
    load_window_index,
    read_window,
    save_window_index,
)
from hydro_disturb_bench.field_sequence import FieldSequenceReader

ARRAYS = ("values", "mask", "invalid_mask", "times", "x", "y", "cylinder_mask")
TEMPORAL = ("values", "mask", "invalid_mask", "times")
SERIES_PLAN_SHA = "75b08df846652372f7c01a1e8699f67cf57912c44b45b719135d701b7f8c4f8d"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def same_sha(left, right):
    return (isinstance(left, str) and isinstance(right, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", left) is not None
            and re.fullmatch(r"[0-9a-fA-F]{64}", right) is not None
            and left.lower() == right.lower())


def inside(root, relative):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts, "Non-relative input path")
    target = (root / relative).resolve()
    require(target.is_relative_to(root.resolve()), "Input path escapes its root")
    return target


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _native_identity(vectors, derivatives, keys, identity, solnum, time):
    for data, expected in ((vectors, identity["native_state_sha256"]),
                           (derivatives, identity["native_derivative_sha256"])):
        for key in keys:
            value = data[key]
            if data is derivatives:
                require(value.get("available") is True, f"Native {key} derivative unavailable")
            require(same_sha(value["sha256"], expected), f"Native {key} identity differs")
            require(value["solnum"] == solnum and value["time"] == time
                    and value["count"] == identity["native_dof_count"]
                    and value["dtype"] == "float64" and value["byte_order"] == "little",
                    f"Native {key} coordinates or representation differ")
            require(value["solution_tag"] == ("sol1" if key == "source" else "sol_branch_seed"),
                    f"Native {key} solution differs")


def validate_lineage(config, data_root):
    """Read pinned small JSONs and join ancestry before assigning a family."""
    require(config["schema_version"] == 1 and config["family_assignment"] == "train",
            "Registered family must remain entirely in train")
    loaded, snapshots = {}, {}
    for key, item in config["inputs"].items():
        require(item["scope"] in ("repository", "data"), "Unknown input scope")
        path = inside(ROOT if item["scope"] == "repository" else data_root, item["path"])
        require(path.suffix == ".json" and path.stat().st_size < 1024 * 1024,
                "Lineage inputs must be small JSON files")
        require(same_sha(sha(path), item["sha256"]), f"Pinned input changed: {key}")
        snapshots[str(path)] = sha(path)
        loaded[key] = json.loads(path.read_text(encoding="utf-8"))
    require(same_sha(config["inputs"]["series_plan"]["sha256"], SERIES_PLAN_SHA),
            "Unregistered series plan")
    plan = loaded["series_plan"]
    for key in ("base_plan", "restart_protocol"):
        require(same_sha(config["inputs"][key]["sha256"], plan[key]["sha256"]),
                f"Series does not bind {key}")
    source, checkpoint = plan["source_model"], plan["checkpoint"]
    source_path, checkpoint_path = inside(data_root, source["path"]), inside(data_root, checkpoint["path"])
    prepare = loaded["prepare_execution"]
    require(prepare["status"] == "COMPLETE" and prepare["operation"] == "prepare_branch_state",
            "Preparation did not complete")
    require(Path(prepare["source_model"]).resolve() == source_path
            and Path(prepare["output_model"]).resolve() == checkpoint_path
            and same_sha(prepare["source_sha256_before"], source["sha256"])
            and same_sha(prepare["source_sha256_after"], source["sha256"])
            and same_sha(prepare["output_sha256"], checkpoint["sha256"]), "Preparation ancestry differs")
    identity, contract = plan["native_identity"], plan["unchanged_contract"]
    native_args = identity, contract["source_solnum"], contract["branch_time_s"]
    _native_identity(loaded["prepare_vectors"], loaded["prepare_derivatives"],
                     ("source", "seed"), *native_args)
    rows = config["trajectories"]
    require([r["trajectory_id"] for r in rows] == ["baseline", "zero", "a0025", "a0050", "a0100"],
            "Registered five trajectories are required in order")
    amplitudes = {"zero": 0., **{c["name"]: c["amplitude_m_s2"] for c in plan["cases"]}}
    records, evidence = [], []
    for row in rows:
        name = row["trajectory_id"]
        directory = inside(data_root, row["sequence_dir"])
        pin = config["inputs"][row["manifest_input"]]
        require(inside(data_root, pin["path"]) == directory / "sequence_manifest.json",
                "Manifest belongs to another sequence")
        manifest = loaded[row["manifest_input"]]
        require(manifest["status"] == "PASS", "Unaccepted sequence")
        if name == "baseline":
            require(directory == inside(data_root, plan["baseline_fields"])
                    and Path(manifest["source_model"]).resolve() == source_path
                    and same_sha(manifest["source_sha256"], source["sha256"]), "Baseline ancestry differs")
        else:
            execution = loaded[name + "_execution"]
            command = execution["command"]
            require(execution["status"] == "COMPLETE" and execution["operation"] == "run_pulse_branch"
                    and Path(execution["source_model"]).resolve() == checkpoint_path
                    and same_sha(execution["source_sha256_before"], checkpoint["sha256"])
                    and same_sha(execution["source_sha256_after"], checkpoint["sha256"])
                    and same_sha(execution["output_sha256"], manifest["source_sha256"])
                    and Path(execution["output_model"]).resolve() == Path(manifest["source_model"]).resolve(),
                    f"Branch {name} source/output binding differs")
            new = name in ("a0025", "a0100")
            require(len(command) == (6 if new else 5)
                    and command[0] == ("run-pulse-series-branch" if new else "run-pulse-branch")
                    and Path(command[1]).resolve() == checkpoint_path
                    and Path(command[2]).resolve() == Path(manifest["source_model"]).resolve()
                    and float(command[3]) == row["amplitude_m_s2"] == amplitudes[name]
                    and command[4] == plan["restart_protocol"]["id"]
                    and (not new or command[5] == "m3-pulse-amplitude-series-v1"),
                    f"Branch {name} command differs")
            _native_identity(loaded[name + "_vectors"], loaded[name + "_derivatives"],
                             ("seed", "input"), *native_args)
        records.append(TrajectoryRecord(name, config["family_id"], str(directory), pin["sha256"],
                                        source["sha256"], checkpoint["sha256"],
                                        identity["native_state_sha256"]))
        evidence.append({"trajectory_id": name, "manifest_sha256": pin["sha256"],
                         "source_sha256": manifest["source_sha256"], "frames": manifest["shape"][0]})
        for batch in manifest["batches"]:
            path = inside(directory, batch["path"] + "/field_sample.npz")
            actual = sha(path)
            require(same_sha(actual, batch["npz_sha256"]), f"Batch identity differs: {path.name}")
            snapshots[str(path)] = actual
    return records, {"status": "PASS", "family_id": config["family_id"],
                     "root_model_sha256": source["sha256"], "checkpoint_sha256": checkpoint["sha256"],
                     "native_identity": identity, "trajectories": evidence,
                     "scope": "Pinned JSON ancestry and NPZ bytes; no MPH/bin read or COMSOL call"}, snapshots


def _reference_loader(ref):
    require(re.fullmatch(r"[0-9a-fA-F]{40}", ref) is not None, "Use a full immutable Git commit")
    result = subprocess.run(["git", "show", f"{ref}:src/hydro_disturb_bench/field_sequence.py"],
                            cwd=ROOT, capture_output=True, check=True)
    module = types.ModuleType("hydro_disturb_bench._data_usage_reference")
    module.__package__ = "hydro_disturb_bench"
    exec(compile(result.stdout, f"git:{ref}/field_sequence.py", "exec"), module.__dict__)
    return module.load_full_field_sequence, hashlib.sha256(result.stdout).hexdigest()


def compare_reference(index, loader):
    """Keep only one full reference trajectory; compare batches, windows and final frames."""
    stats = {"status": "PASS", "batches": 0, "windows": 0, "array_comparisons": 0,
             "elements_compared": 0, "max_absolute_difference": 0., "cross_batch_windows": 0,
             "nan_positions_compared": 0, "tail_checks": []}

    def equal(actual, expected, label):
        require(actual.shape == expected.shape and actual.dtype == expected.dtype,
                f"Reference shape/dtype mismatch: {label}")
        finite = np.isfinite(actual) & np.isfinite(expected)
        delta = np.abs(actual[finite].astype(np.float64) - expected[finite].astype(np.float64))
        stats["max_absolute_difference"] = max(stats["max_absolute_difference"],
                                               float(delta.max()) if delta.size else 0.)
        stats["nan_positions_compared"] += int(np.isnan(expected).sum())
        require(np.array_equal(actual, expected, equal_nan=True),
                f"Reference mismatch: {label}")
        stats["array_comparisons"] += 1
        stats["elements_compared"] += actual.size

    for trajectory in index["trajectories"]:
        name = trajectory["trajectory_id"]
        reference = loader(trajectory["sequence_dir"])
        reader = FieldSequenceReader(trajectory["sequence_dir"])
        for batch, data in zip(reader.manifest["batches"], reader.iter_batches(), strict=True):
            span = slice(batch["start_index"], batch["stop_index"])
            for key in ARRAYS:
                equal(data[key], reference[key][span] if key in TEMPORAL else reference[key], key)
            stats["batches"] += 1
        windows = [w for w in index["windows"] if w["trajectory_id"] == name]
        for window in windows:
            actual = read_window(index, window["window_id"])
            expected = {key: reference[key] for key in ("x", "y")}
            for part in ("history", "future"):
                span = slice(window[part + "_start"], window[part + "_stop"])
                expected.update({part: reference["values"][span],
                                 part + "_mask": reference["mask"][span],
                                 part + "_times": reference["times"][span]})
            require(set(actual) == set(expected), "Window leaked metadata or omitted fields")
            for key in expected:
                equal(actual[key], expected[key], window["window_id"] + "/" + key)
            stats["windows"] += 1
            stats["cross_batch_windows"] += any(window["history_start"] < b["stop_index"]
                < window["future_stop"] for b in reader.manifest["batches"])
        tail_start = max(0, len(reader) - 3)
        tail = reader.read_frames(tail_start, len(reader))
        for key in ARRAYS:
            equal(tail[key], reference[key][tail_start:] if key in TEMPORAL else reference[key], key)
        stats["tail_checks"].append({"trajectory_id": name, "start": tail_start, "stop": len(reader),
            "frames_after_last_window": len(reader) - max(w["future_stop"] for w in windows)})
        del reference, reader, tail, actual, expected, data
    return stats


def prepare_usage(config_path, data_root, output, audit_reference_ref=None):
    output, data_root = Path(output).absolute(), Path(data_root).resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output = output.resolve()
    config_path = Path(config_path).resolve()
    config_bytes = config_path.read_bytes()
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    config = json.loads(config_bytes)
    protected = [inside(data_root, row["sequence_dir"]) for row in config["trajectories"]]
    protected.extend(inside(ROOT if item["scope"] == "repository" else data_root,
                            item["path"]).parent for item in config["inputs"].values())
    require(not any(output.is_relative_to(path) for path in protected),
            "Output must not be inside an existing input sequence or evidence directory")
    output.mkdir(parents=True, exist_ok=False)
    try:
        records, lineage, before = validate_lineage(config, data_root)
        before[str(config_path)] = config_sha
        index = build_window_index(records, {config["family_id"]: "train"},
                                   WindowSpec(**config["window_spec"]))
        comparison = {"status": "NOT_REQUESTED"}
        if audit_reference_ref:
            loader, source_sha = _reference_loader(audit_reference_ref)
            comparison = {**compare_reference(index, loader), "reference_commit": audit_reference_ref,
                          "reference_source_sha256": source_sha}
        require(all(sha(Path(p)) == expected for p, expected in before.items()), "Inputs changed during audit")
        save_window_index(index, output / "index.json")
        index_sha = sha(output / "index.json")
        require(load_window_index(output / "index.json", expected_sha256=index_sha) == index,
                "Saved index changed on readback")
        lineage["input_sha256_before_after"] = before
        lineage["inputs_unchanged"] = True
        report = {"status": "PASS", "config_sha256": config_sha, "index_sha256": index_sha,
                  "counts": index["counts"], "reference_comparison": comparison,
                  "unchanged_input_file_count": len(before),
                  "solver_called": False, "new_tensor_files": 0,
                  "split_scope": "One shared-ancestor family in train; validation/test empty",
                  "path_scope": "Recorded absolute model paths must match data-root; relocation is not implicit"}
        write_json(output / "lineage_audit.json", lineage)
        write_json(output / "report.json", report)
        return report
    except Exception as exc:
        for name in ("index.json", "lineage_audit.json", "report.json"):
            (output / name).unlink(missing_ok=True)
        write_json(output / "failure.json", {"status": "FAIL", "error_type": type(exc).__name__,
                                            "error": str(exc), "solver_called": False})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("HYDRO_DATA_ROOT", ROOT)))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/m3_data_usage.json")
    parser.add_argument("--audit-reference-ref")
    args = parser.parse_args(argv)
    report = prepare_usage(args.config, args.data_root, args.output, args.audit_reference_ref)
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
