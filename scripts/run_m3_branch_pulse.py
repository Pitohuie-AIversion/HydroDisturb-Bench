"""Run the frozen native-M3 zero/pulse experiment in explicit, retained stages."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench.comsol_java import bridge as bridge
from hydro_disturb_bench.experiments import branch_execution as execution
from hydro_disturb_bench.experiments import process_recording, solver_steps
from hydro_disturb_bench.experiments.evidence_io import (
    file_sha256 as _hash,
)
from hydro_disturb_bench.experiments.evidence_io import (
    persist_vectors as _vectors,
)
from hydro_disturb_bench.experiments.evidence_io import (
    read_json as _read,
)
from hydro_disturb_bench.experiments.evidence_io import (
    require as _require,
)
from hydro_disturb_bench.experiments.evidence_io import (
    write_json as _write,
)
from hydro_disturb_bench.field_sequence import (
    export_full_field_sequence as export_full_field_sequence,
)
from hydro_disturb_bench.field_sequence import load_full_field_sequence

# Compatibility for existing local review scripts; implementation lives in services.
_capture_processes = process_recording.ProcessRecorder
_parse_internal_steps = solver_steps.parse_internal_steps
_branch_step_validation = solver_steps.validate_branch_steps

PLAN_PATH = ROOT / "docs/evidence/m3_branch_pulse_20261105/preregistered_plan.json"
PLAN_SHA = "72343d29335f761b82a5c3aa5b269b027fca82217c6cb69adbd537e8787125ad"
RESTART_PROTOCOL_PATH = ROOT / "docs/evidence/m3_branch_pulse_20261105/restart_protocol_v2.json"
RESTART_PROTOCOL_SHA = "586a7065a7e9da4560501a368ab68037b0bbdaebf4957e6bab9541bb16e8acb8"
BASELINE = ROOT / "runs/re100_m3_field_sequence_20261105_verified"



def _execute(directory, command, source, target, operation):
    return execution.execute_branch(directory, command, source, target, operation, entrypoint=Path(__file__))


def _completed(directory):
    return execution.completed_stage(directory)


def _sample(output, name, plan):
    return execution.sample_branch(output, name, plan, entrypoint=Path(__file__))


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
