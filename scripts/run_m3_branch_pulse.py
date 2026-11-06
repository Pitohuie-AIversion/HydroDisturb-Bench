"""Run the frozen native-M3 zero/pulse experiment in explicit, retained stages."""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import json
import locale
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench.comsol_java import bridge
from hydro_disturb_bench.field_sequence import (
    export_full_field_sequence,
    load_full_field_sequence,
)

PLAN_PATH = ROOT / "docs/evidence/m3_branch_pulse_20261105/preregistered_plan.json"
PLAN_SHA = "72343d29335f761b82a5c3aa5b269b027fca82217c6cb69adbd537e8787125ad"
RESTART_PROTOCOL_PATH = ROOT / "docs/evidence/m3_branch_pulse_20261105/restart_protocol_v2.json"
RESTART_PROTOCOL_SHA = "586a7065a7e9da4560501a368ab68037b0bbdaebf4957e6bab9541bb16e8acb8"
BASELINE = ROOT / "runs/re100_m3_field_sequence_20261105_verified"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path, data):
    with path.open("xb") as handle:
        handle.write((json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def _hash(path):
    return bridge.compute_file_hash(path).lower()


def _require(condition, message):
    if not condition:
        raise bridge.ModelValidationError(message)


def _vectors(directory, vectors, prefix="native"):
    records = {}
    for name, item in vectors.items():
        if item is None:
            continue
        _require(re.fullmatch(r"[A-Za-z0-9_-]+", name), "Unsafe native vector name")
        _require(item.get("dtype") == "float64" and item.get("byte_order") == "little",
                 "Native vectors must declare float64 little-endian")
        raw = base64.b64decode(item["base64"], validate=True)
        count = item["count"]
        _require(isinstance(count, int) and not isinstance(count, bool) and count > 0,
                 "Native vector count must be a positive integer")
        _require(len(raw) == count * 8 and np.isfinite(np.frombuffer(raw, dtype="<f8")).all(),
                 "Native vector length/finiteness mismatch")
        _require(hashlib.sha256(raw).hexdigest() == item["sha256"].lower(),
                 "Native vector bytes do not match actual readback SHA-256")
        filename = f"{prefix}_{name}.bin"
        with (directory / filename).open("xb") as handle:
            handle.write(raw)
        records[name] = {**{key: value for key, value in item.items() if key != "base64"},
                         "path": filename}
    _write(directory / f"{prefix}_vectors.json", records)
    return records


@contextlib.contextmanager
def _capture_processes(directory):
    """Retain raw bytes even when the established bridge raises a typed error."""
    original_run = subprocess.run
    records = []

    def captured(command, **kwargs):
        started = time.time()
        text_mode = kwargs.pop("text", False)
        text_mode = kwargs.pop("universal_newlines", False) or text_mode
        encoding = kwargs.pop("encoding", None)
        errors = kwargs.pop("errors", None) or "strict"
        text_mode = text_mode or encoding is not None
        process = original_run(command, **kwargs)
        stem = f"process_{len(records):03d}"
        for channel in ("stdout", "stderr"):
            raw = getattr(process, channel) or b""
            with (directory / f"{stem}_{channel}.log").open("xb") as handle:
                handle.write(raw)
        record = {"command": [str(value) for value in command], "returncode": process.returncode,
                  "started_unix": started, "finished_unix": time.time(),
                  "stdout": f"{stem}_stdout.log", "stderr": f"{stem}_stderr.log"}
        records.append(record)
        _write(directory / f"{stem}_execution.json", record)
        stdout = (process.stdout or b"").decode(encoding or "utf-8", errors="replace")
        if "<<<JSON_START>>>" in stdout and "<<<JSON_END>>>" in stdout:
            payload = json.loads(stdout.split("<<<JSON_START>>>", 1)[1].split("<<<JSON_END>>>", 1)[0])
            _write(directory / f"{stem}_payload.json", payload)
            for field, suffix in (("partial_native_vectors", "partial"),
                                  ("partial_native_derivatives", "partial_derivative")):
                partial = payload.get(field)
                if partial:
                    try:
                        _vectors(directory, partial, prefix=f"{stem}_{suffix}")
                    except Exception as exc:
                        _write(directory / f"{stem}_{suffix}_decode_failure.json", {"error": str(exc)})
        if text_mode:
            decoder = encoding or locale.getpreferredencoding(False)
            return subprocess.CompletedProcess(
                process.args, process.returncode,
                process.stdout.decode(decoder, errors=errors) if process.stdout is not None else None,
                process.stderr.decode(decoder, errors=errors) if process.stderr is not None else None,
            )
        return process

    try:
        with patch.object(bridge.subprocess, "run", captured):
            yield
    finally:
        _write(directory / "processes.json", records)


def _execute(directory, command, source, target, operation):
    directory.mkdir(exist_ok=False)
    before = _hash(source)
    implementation_files = (
        Path(__file__).resolve(), Path(bridge.__file__).resolve(),
        Path(bridge.__file__).resolve().with_name("ComsolBridge.java"),
    )
    implementation_before = {str(path): _hash(path) for path in implementation_files}
    started = time.time()

    def implementation_evidence():
        files = {path: {"sha256_before": expected,
                        "sha256_after": _hash(Path(path)) if Path(path).is_file() else "MISSING"}
                 for path, expected in implementation_before.items()}
        unchanged = all(item["sha256_before"] == item["sha256_after"] for item in files.values())
        java_class = Path(bridge.__file__).resolve().with_name("ComsolBridge.class")
        return {"source_files": files, "all_source_files_unchanged": unchanged,
                "compiled_class_path": str(java_class),
                "compiled_class_sha256_after": _hash(java_class) if java_class.is_file() else None,
                "compiled_class_note": "End-of-operation bytes; compilation may occur during the operation"}

    try:
        _require(not target.exists() and not target.is_symlink(), "Output model already exists")
        with _capture_processes(directory):
            response = bridge._run_bridge(command)
        _write(directory / "readback.json", response)
        if "time_solver_log" in response:
            (directory / "time_solver.log").write_bytes(response["time_solver_log"].encode("utf-8"))
        _require(response.get("status") == "SUCCESS" and response.get("operation") == operation,
                 "Unexpected branch operation response")
        _require(Path(response["source_model"]).resolve() == source.resolve()
                 and Path(response["output_model"]).resolve() == target.resolve(),
                 "Actual model paths do not match requested branch paths")
        _require(target.is_file(), "COMSOL did not save the branch model")
        _require(_hash(source) == before, "Input model changed during COMSOL operation")
        _vectors(directory, response["native_vectors"])
        if "native_derivatives" in response:
            _vectors(directory, response["native_derivatives"], prefix="native_derivative")
        implementation = implementation_evidence()
        _require(implementation["all_source_files_unchanged"],
                 "Runner, bridge or Java source changed during the operation")
        record = {"status": "COMPLETE", "operation": operation, "command": command,
                  "source_model": str(source), "source_sha256_before": before,
                  "source_sha256_after": _hash(source), "output_model": str(target),
                  "output_sha256": _hash(target), "readback_sha256": _hash(directory / "readback.json"),
                  "implementation": implementation,
                  "elapsed_seconds": time.time() - started}
        _write(directory / "execution.json", record)
        return response, target, record
    except Exception as exc:
        _write(directory / "failure.json", {
            "status": "FAIL", "error": str(exc), "details": getattr(exc, "details", ""),
            "traceback": traceback.format_exc(), "source_sha256_before": before,
            "source_sha256_after": _hash(source) if source.is_file() else "MISSING",
            "implementation": implementation_evidence(),
            "partial_output_model": str(target), "elapsed_seconds": time.time() - started,
        })
        raise


def _completed(directory):
    record = _read(directory / "execution.json")
    _require(record.get("status") == "COMPLETE", "Stage is not complete")
    target = Path(record["output_model"])
    _require(_hash(target) == record["output_sha256"], "Completed model bytes changed")
    _require(_hash(directory / "readback.json") == record["readback_sha256"],
             "Completed stage readback changed")
    return _read(directory / "readback.json"), target, record


def _prepare(output, source, args, plan):
    directory = output / "prepare"
    if directory.exists():
        return _completed(directory)
    if args.checkpoint is None:
        target = directory / "common_checkpoint.mph"
        return _execute(directory, ["prepare-branch-state", str(source), str(target),
                        str(plan["branch_time_s"])], source, target, "prepare_branch_state")
    checkpoint, readback_path = args.checkpoint.resolve(), args.readback.resolve()
    response = _read(readback_path)
    _require(response.get("status") == "SUCCESS" and response.get("operation") == "prepare_branch_state",
             "Reused checkpoint requires a successful preparation response")
    _require(Path(response["source_model"]).resolve() == source
             and Path(response["output_model"]).resolve() == checkpoint,
             "Reused preparation model paths mismatch")
    _require(response.get("output_sha256", "").lower() == _hash(checkpoint),
             "Reused checkpoint needs a matching actual output_sha256 in its readback")
    _require(response.get("source_sha256_before", "").lower() == plan["source_sha256"]
             and response.get("source_sha256_after", "").lower() == plan["source_sha256"],
             "Reused preparation lacks unchanged source-model hash evidence")
    directory.mkdir()
    (directory / "readback.json").write_bytes(readback_path.read_bytes())
    _vectors(directory, response["native_vectors"])
    if "native_derivatives" in response:
        _vectors(directory, response["native_derivatives"], prefix="native_derivative")
    record = {"status": "COMPLETE", "operation": "prepare_branch_state", "reused": True,
              "source_model": str(source), "source_sha256_before": _hash(source),
              "source_sha256_after": _hash(source), "output_model": str(checkpoint),
              "output_sha256": _hash(checkpoint), "readback_sha256": _hash(directory / "readback.json"),
              "original_readback": str(readback_path)}
    _write(directory / "execution.json", record)
    return response, checkpoint, record


def _solve(output, checkpoint, name, plan):
    directory = output / name
    protocol_id = plan["restart_protocol"]["id"]
    if directory.exists():
        completed = _completed(directory)
        _require(completed[2].get("command", [])[4:] == [protocol_id],
                 "Completed branch lacks this explicit restart protocol command")
        return completed
    amplitude = plan["pulse"]["zero_branch_amplitude_m_s2" if name == "zero" else "acceleration_amplitude_m_s2"]
    target = directory / f"{name}.mph"
    return _execute(directory, ["run-pulse-branch", str(checkpoint), str(target), str(amplitude), protocol_id],
                    checkpoint, target, "run_pulse_branch")


def _sample(output, name, plan):
    _, model, solved = _completed(output / name)
    directory = output / f"sample_{name}"
    if directory.exists():
        record = _read(directory / "execution.json")
        _require(record["source_sha256"] == solved["output_sha256"], "Sample model identity changed")
        _require(_hash(directory / "fields/sequence_manifest.json") == record["manifest_sha256"],
                 "Completed sample manifest changed")
        load_full_field_sequence(directory / "fields")
        return
    directory.mkdir()
    started = time.time()
    try:
        with _capture_processes(directory):
            report = export_full_field_sequence(
                model, directory / "fields", start=plan["branch_time_s"], stop=plan["stop_s"],
                step=plan["output_step_s"], grid_shape=tuple(plan["grid_shape_nx_ny"]),
                batch_size=32, overlap_reference=None,
            )
        _require(_hash(model) == solved["output_sha256"], "Branch model changed during field export")
        _write(directory / "execution.json", {"status": "COMPLETE", "source_model": str(model),
            "source_sha256": _hash(model), "manifest_sha256": _hash(directory / "fields/sequence_manifest.json"),
            "shape": report["shape"], "elapsed_seconds": time.time() - started})
    except Exception as exc:
        _write(directory / "failure.json", {"status": "FAIL", "error": str(exc),
            "details": getattr(exc, "details", ""), "traceback": traceback.format_exc(),
            "source_sha256_after": _hash(model), "elapsed_seconds": time.time() - started})
        raise


def _parse_internal_steps(text, start=180.0, stop=200.0, cap=0.05):
    """Read accepted internal rows; output-only rows never count as steps."""
    header = re.compile(r"^\s*Step\s+Time\s+Stepsize(?:\s|$)")
    candidate = re.compile(r"^\s*(\d+|-)\s+(\S+)\s+(\S+)(?:\s|$)")
    rows, initial_times, output_rows = [], [], 0
    headers = 0
    for line in text.splitlines():
        if header.match(line):
            headers += 1
            continue
        if not headers:
            continue
        match = candidate.match(line)
        if not match:
            continue
        step_text, time_text, dt_text = match.groups()
        if step_text == "-" and not re.search(r"\sout(?:\s|$)", line):
            # COMSOL prints progress lines such as '- Current Progress: 10 %'.
            # They share the leading dash with output rows, but have no 'out'.
            continue
        try:
            actual_time = float(time_text)
        except ValueError as exc:
            raise bridge.ModelValidationError("Malformed time in internal-step table") from exc
        _require(np.isfinite(actual_time), "Non-finite internal-step time")
        if dt_text == "-":
            _require(re.search(r"\sout(?:\s|$)", line), "Malformed output-only solver row")
            output_rows += 1
            if abs(actual_time - start) <= 1e-8:
                initial_times.append(actual_time)
            continue
        _require(step_text != "-", "Accepted internal step lacks its number")
        try:
            dt = float(dt_text)
        except ValueError as exc:
            raise bridge.ModelValidationError("Malformed dt in internal-step table") from exc
        _require(np.isfinite(dt), "Non-finite internal-step size")
        rows.append((int(step_text), actual_time, dt))
    _require(headers > 0, "Missing actual Step Time Stepsize header")
    _require(bool(rows), "Missing accepted internal-step records")
    first = rows[0][0]
    _require(first in (0, 1) and [row[0] for row in rows] == list(range(first, first + len(rows))),
             "Accepted internal-step numbers must be contiguous and unique from 0 or 1")
    _require(initial_times or abs(rows[0][1] - start) <= 1e-8,
             "Internal-step log does not establish the actual 180 s start")
    _require(rows[0][1] >= start - 1e-8 and rows[-1][1] >= stop - 1e-8,
             "Missing complete accepted internal-step log through the final time")
    # Printed times can coincide when a tiny positive step rounds to the same value.
    _require(all(left[1] <= right[1] for left, right in zip(rows, rows[1:])),
             "Displayed internal-step times move backwards")
    cap_limit = cap * (1.0 + 1e-4)
    _require(all(0 < row[2] <= cap_limit for row in rows),
             "Actual accepted dt violates the frozen positive step cap")
    _require(len(rows) * cap_limit >= stop - start - 1e-8,
             "Too few accepted internal steps to cover the requested time interval at the frozen cap")
    return {"status": "PASS", "headers_seen": headers, "accepted_step_count": len(rows),
            "first_step_number": first, "last_step_number": rows[-1][0],
            "initial_time_s": start, "first_accepted_time_s": rows[0][1],
            "final_internal_time_s": rows[-1][1], "output_only_rows_excluded": output_rows,
            "dt_min_s": min(row[2] for row in rows), "dt_max_s": max(row[2] for row in rows),
            "cap_s": cap, "cap_display_relative_tolerance": 1e-4,
            "contiguous_unique_accepted_step_ids": True, "displayed_time_nondecreasing": True,
            "last_internal_step_may_exceed_last_output_time": True}


def _branch_step_validation(directory, response, plan):
    getter_text = response.get("time_solver_log")
    getter_available = isinstance(getter_text, str) and bool(getter_text.strip())
    header = re.compile(r"^\s*Step\s+Time\s+Stepsize(?:\s|$)", re.MULTILINE)
    getter = {"available": getter_available,
              "character_count": len(getter_text) if isinstance(getter_text, str) else 0,
              "has_step_table_header": bool(getter_available and header.search(getter_text)),
              "complete_step_table": False, "truncated_or_invalid": False}
    getter_result = None
    if getter_available:
        getter["text_utf8_sha256"] = hashlib.sha256(getter_text.encode("utf-8")).hexdigest()
        try:
            getter_result = _parse_internal_steps(
                getter_text, plan["branch_time_s"], plan["stop_s"], plan["solver"]["maxstepbdf_s"])
            getter["complete_step_table"] = True
        except bridge.ModelValidationError as exc:
            getter["truncated_or_invalid"] = True
            getter["validation_error"] = str(exc)
    source = {"source": None, "getter_diagnostic": getter}
    try:
        candidates = []
        for path in sorted(directory.glob("process_*_stdout.log")):
            raw = path.read_bytes()
            decoded = raw.decode("utf-8", errors="replace")
            if header.search(decoded):
                candidates.append((path, decoded))
        source["stdout_table_candidates"] = [path.name for path, _ in candidates]
        _require(len(candidates) <= 1, "Ambiguous actual solver stdout tables")
        if candidates:
            path, text = candidates[0]
            source.update({"source": path.name, "source_bytes_sha256": _hash(path),
                           "reason": "Original process stdout is the primary actual step-log source"})
            result = _parse_internal_steps(text, plan["branch_time_s"], plan["stop_s"],
                                           plan["solver"]["maxstepbdf_s"])
        else:
            _require(getter_available, "Missing actual solver stdout table and time_solver_log getter")
            source.update({"source": "readback.json:time_solver_log",
                           "source_text_utf8_sha256": getter["text_utf8_sha256"],
                           "reason": "No original stdout step table is available; complete actual getter used"})
            _require(getter_result is not None,
                     "No original stdout step table and getter is incomplete: " + getter.get("validation_error", ""))
            result = getter_result
        return {**result, **source}
    except bridge.ModelValidationError as exc:
        return {"status": "FAIL", **source, "error": str(exc)}


def _analyze(output, plan):
    from hydro_disturb_bench.branch_pulse import (
        compare_branch_fields,
        validate_native_branch_evidence,
    )

    directory = output / "analysis"
    directory.mkdir(exist_ok=False)
    try:
        prepare, checkpoint, prepared = _completed(output / "prepare")
        zero, zero_model, zero_run = _completed(output / "zero")
        pulse, pulse_model, pulse_run = _completed(output / "pulse")
        for response, record, operation in (
            (prepare, prepared, "prepare_branch_state"),
            (zero, zero_run, "run_pulse_branch"),
            (pulse, pulse_run, "run_pulse_branch"),
        ):
            _require(record.get("operation") == response.get("operation") == operation,
                     "Completed stage operation does not match its role")
        for record in (zero_run, pulse_run):
            _require(record.get("command", [])[4:] == [plan["restart_protocol"]["id"]],
                     "Solved branch lacks this explicit restart protocol command")
            _require(Path(record["source_model"]).resolve() == checkpoint.resolve()
                     and record["source_sha256_before"].lower() == prepared["output_sha256"].lower()
                     and record["source_sha256_after"].lower() == prepared["output_sha256"].lower(),
                     "Solved branch is not bound to the common checkpoint bytes")
        baseline_fields = load_full_field_sequence(BASELINE)
        zero_fields = load_full_field_sequence(output / "sample_zero/fields")
        pulse_fields = load_full_field_sequence(output / "sample_pulse/fields")
        _require(baseline_fields["metadata"]["source_sha256"].lower() == plan["source_sha256"].lower(),
                 "Baseline fields are not bound to the frozen original M3 model")
        for fields, target, record in (
            (zero_fields, zero_model, zero_run), (pulse_fields, pulse_model, pulse_run),
        ):
            metadata = fields["metadata"]
            _require(metadata["source_sha256"].lower() == record["output_sha256"].lower()
                     and Path(metadata["source_model"]).resolve() == target.resolve(),
                     "Sampled fields are not bound to the corresponding solved branch model")
        native = validate_native_branch_evidence(prepare, zero, pulse, plan)
        _write(directory / "native_validation.json", native)
        step_checks = {"zero": _branch_step_validation(output / "zero", zero, plan),
                       "pulse": _branch_step_validation(output / "pulse", pulse, plan)}
        step_checks["status"] = "PASS" if all(
            item["status"] == "PASS" for item in step_checks.values()) else "FAIL"
        _write(directory / "step_validation.json", step_checks)
        fields = compare_branch_fields(baseline_fields, zero_fields, pulse_fields, plan)
        _write(directory / "field_comparison.json", fields)
        report = {"status": "PASS" if native["status"] == fields["status"] == step_checks["status"] == "PASS" else "FAIL",
                  "plan_sha256": PLAN_SHA, "native_status": native["status"], "field_status": fields["status"],
                  "restart_protocol_sha256": RESTART_PROTOCOL_SHA,
                  "restart_protocol_id": plan["restart_protocol"]["id"],
                  "step_status": step_checks["status"], "step_validation": "step_validation.json",
                  "native_validation": "native_validation.json", "field_comparison": "field_comparison.json",
                  "source_model_sha256_after": _hash(ROOT / plan["source_model"])}
        _require(report["source_model_sha256_after"] == plan["source_sha256"], "Original source changed")
        _write(directory / "report.json", report)
        return report
    except Exception as exc:
        _write(directory / "failure.json", {"status": "FAIL", "error": str(exc),
            "details": getattr(exc, "details", ""), "traceback": traceback.format_exc()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New run directory, or a matching staged run")
    parser.add_argument("--stage", choices=("all", "prepare", "solve-zero", "solve-pulse", "sample", "analyze"), default="all")
    parser.add_argument("--checkpoint", type=Path, help="Reuse an already verified native preparation")
    parser.add_argument("--readback", type=Path, help="Preparation response with actual source/output SHA-256")
    args = parser.parse_args()
    if (args.checkpoint is None) != (args.readback is None):
        parser.error("--checkpoint and --readback must be provided together")
    _require(_hash(PLAN_PATH) == PLAN_SHA, "Frozen preregistered plan bytes changed")
    _require(_hash(RESTART_PROTOCOL_PATH) == RESTART_PROTOCOL_SHA, "Frozen restart protocol bytes changed")
    protocol = _read(RESTART_PROTOCOL_PATH)
    _require(protocol.get("physical_plan_sha256") == PLAN_SHA,
             "Restart protocol is not bound to this frozen physical plan")
    plan = copy.deepcopy(_read(PLAN_PATH))
    plan["restart_protocol"] = copy.deepcopy(protocol)
    source = (ROOT / plan["source_model"]).resolve()
    _require(_hash(source) == plan["source_sha256"], "Original M3 differs from the frozen source")
    output = args.output.absolute()
    if output.is_symlink():
        raise FileExistsError(output)
    if output.exists():
        marker = _read(output / "run_manifest.json")
        _require(marker["plan_sha256"] == PLAN_SHA and marker["source_sha256"] == _hash(source),
                 "Existing directory is not this frozen staged experiment")
        _require(marker.get("restart_protocol_sha256") == RESTART_PROTOCOL_SHA
                 and marker.get("restart_protocol_id") == protocol["id"],
                 "Existing directory lacks this frozen restart protocol marker")
        _require(_hash(output / "preregistered_plan.json") == PLAN_SHA, "Run plan copy changed")
        _require(_hash(output / RESTART_PROTOCOL_PATH.name) == RESTART_PROTOCOL_SHA,
                 "Run restart protocol copy changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "preregistered_plan.json").write_bytes(PLAN_PATH.read_bytes())
        (output / RESTART_PROTOCOL_PATH.name).write_bytes(RESTART_PROTOCOL_PATH.read_bytes())
        _write(output / "run_manifest.json", {"schema_version": 2, "plan_sha256": PLAN_SHA,
            "restart_protocol_sha256": RESTART_PROTOCOL_SHA, "restart_protocol_id": protocol["id"],
            "source_model": str(source), "source_sha256": _hash(source), "created_unix": time.time()})
    if args.stage in ("sample", "analyze"):
        _, checkpoint, _ = _completed(output / "prepare")
    else:
        _, checkpoint, _ = _prepare(output, source, args, plan)
    if args.stage in ("all", "solve-zero"):
        _solve(output, checkpoint, "zero", plan)
    if args.stage in ("all", "solve-pulse"):
        _solve(output, checkpoint, "pulse", plan)
    if args.stage in ("all", "sample"):
        for name in ("zero", "pulse"):
            _sample(output, name, plan)
    report = _analyze(output, plan) if args.stage in ("all", "analyze") else {
        "status": "STAGE_COMPLETE", "stage": args.stage, "output": str(output),
    }
    _require(_hash(source) == plan["source_sha256"], "Original M3 changed during stage")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["status"] != "FAIL" else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
