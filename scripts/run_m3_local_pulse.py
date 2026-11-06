"""Configure, inspect and solve one local Gaussian pulse from the accepted M3 seed."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench import branch_pulse, pulse_parameters, pulse_validation
from hydro_disturb_bench.experiments import branch_execution as execution
from hydro_disturb_bench.experiments.evidence_io import file_sha256 as sha
from hydro_disturb_bench.experiments.evidence_io import read_json as read
from hydro_disturb_bench.experiments.evidence_io import require
from hydro_disturb_bench.experiments.evidence_io import write_json as write
from hydro_disturb_bench.experiments.solver_steps import validate_branch_steps
from hydro_disturb_bench.field_sequence import load_full_field_sequence

SERIES_PATH = ROOT / "docs/evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json"
SERIES_SHA = "75b08df846652372f7c01a1e8699f67cf57912c44b45b719135d701b7f8c4f8d"
INDEX_PATH = ROOT / "docs/evidence/data_usage_20261106/index.json"
INDEX_SHA = "63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5"


def _same_sha(left, right):
    return all(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
               for value in (left, right)) and left.lower() == right.lower()


def _inside(root, relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Reference must be relative")
    result = (root / path).resolve()
    require(result.is_relative_to(root), "Reference escapes data root")
    return result


def _inputs(data_root):
    """Pin the previously accepted checkpoint, original evidence and control data."""
    require(sha(SERIES_PATH) == SERIES_SHA and sha(INDEX_PATH) == INDEX_SHA,
            "Accepted source catalogue identity changed")
    series, index = read(SERIES_PATH), read(INDEX_PATH)
    checkpoint = _inside(data_root, series["checkpoint"]["path"])
    prepare_path = _inside(data_root, series["reference_run"] + "/prepare/readback.json")
    rows = {row["path"]: row for row in series["reference_files"]}
    prepare_row = rows[series["reference_run"] + "/prepare/readback.json"]
    require(sha(checkpoint) == series["checkpoint"]["sha256"], "Native checkpoint changed")
    require(sha(prepare_path) == prepare_row["sha256"], "Native prepare evidence changed")
    prepare = read(prepare_path)
    require(Path(prepare["output_model"]).resolve() == checkpoint
            and prepare["output_sha256"] == series["checkpoint"]["sha256"],
            "Prepare evidence does not bind the common checkpoint")
    require(prepare["source_sha256_before"] == prepare["source_sha256_after"]
            == series["source_model"]["sha256"], "Original native source identity differs")
    base_path = _inside(ROOT, series["base_plan"]["path"])
    require(sha(base_path) == series["base_plan"]["sha256"], "Original physical contract changed")
    base = read(base_path)
    references = {}
    for name in ("baseline", "zero"):
        record = next(item for item in index["trajectories"] if item["trajectory_id"] == name)
        relative = series["baseline_fields"] if name == "baseline" else series["references"]["zero"]["fields_dir"]
        directory = _inside(data_root, relative)
        require(Path(record["sequence_dir"]).resolve() == directory, "Control path differs from pinned index")
        manifest = directory / "sequence_manifest.json"
        require(sha(manifest) == record["manifest_sha256"], "Control sequence manifest changed")
        references[name] = {"sequence_dir": str(directory), "manifest_sha256": record["manifest_sha256"],
                            "source_sha256": record["source_sha256"]}
    return {"checkpoint": str(checkpoint), "checkpoint_sha256": series["checkpoint"]["sha256"],
            "prepare_readback": str(prepare_path), "prepare_sha256": prepare_row["sha256"],
            "controls": references, "series_sha256": SERIES_SHA, "index_sha256": INDEX_SHA}, prepare, base


def _analysis_plan(base, config):
    plan = copy.deepcopy(base)
    pulse = config["pulse"]
    plan["pulse"].update(acceleration_amplitude_m_s2=pulse["acceleration_m_s2"],
                         center_m=pulse["center_m"], sigma_m=pulse["sigma_m"],
                         direction=list(pulse_parameters.pulse_direction(config)),
                         on_s=pulse["on_s"], off_s=pulse["off_s"])
    x, y = pulse["center_m"]
    radius = 3 * pulse["sigma_m"]
    plan["response_acceptance"].update(
        roi_m=[x-radius, x+radius, y-radius, y+radius], pre_event_end_s=pulse["on_s"],
        response_window_s=[pulse["on_s"], min(200., pulse["off_s"] + 3.)])
    return plan


def freeze_run(config_path, output, data_root):
    config_path, output, data_root = Path(config_path).resolve(), Path(output).resolve(), Path(data_root).resolve()
    raw = config_path.read_bytes()
    config = pulse_parameters.load_pulse_config(config_path)
    require(sha(config_path) == hashlib.sha256(raw).hexdigest(), "Config changed during admission")
    inputs, prepare, base = _inputs(data_root)
    protected = [config_path.parent, SERIES_PATH.parent, INDEX_PATH.parent,
                 Path(inputs["checkpoint"]).parent, Path(inputs["prepare_readback"]).parent,
                 *(Path(row["sequence_dir"]) for row in inputs["controls"].values())]
    require(not any(output.is_relative_to(path) for path in protected), "Output must be outside all input directories")
    properties = pulse_parameters.properties_payload(config).encode("ascii")
    plan = {"schema_version": 1, "operation": "configurable_m3_local_pulse",
            "config": config, "config_sha256": hashlib.sha256(raw).hexdigest(),
            "properties_sha256": hashlib.sha256(properties).hexdigest(), "inputs": inputs,
            "source_integral_reference": pulse_parameters.source_integral_reference(config),
            "analysis_plan": _analysis_plan(base, config),
            "scope": "Engineering admission and source checks; no accuracy guarantee over parameter space",
            "restart_protocol_id": "native-restart-consistent-off-v2"}
    if output.exists():
        require(output.is_dir() and not output.is_symlink(), "Output must be a regular run directory")
        require(read(output / "plan.json") == plan, "Existing run has another config or input identity")
        require((output / "config.input.json").read_bytes() == raw
                and (output / "event.properties").read_bytes() == properties,
                "Frozen configuration or executable properties changed")
        require(sha(output / "plan.json") == (output / "plan.sha256").read_text().strip(), "Run plan bytes changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        with (output / "config.input.json").open("xb") as stream:
            stream.write(raw)
        with (output / "event.properties").open("xb") as stream:
            stream.write(properties)
        write(output / "plan.json", plan)
        with (output / "plan.sha256").open("x", encoding="ascii") as stream:
            stream.write(sha(output / "plan.json") + "\n")
    return plan, prepare


def _sources(output):
    return [pulse_parameters.__file__, pulse_validation.__file__, branch_pulse.__file__,
            ROOT / "src/hydro_disturb_bench/comsol_java/ConfigurablePulseBranch.java",
            output / "plan.json", output / "event.properties", output / "config.input.json"]


def _native_stage(output, plan, prepare, mode):
    directory = output / mode
    source, target = Path(plan["inputs"]["checkpoint"]), directory / "model.mph"
    properties = (output / "event.properties").read_bytes()
    require(hashlib.sha256(properties).hexdigest() == plan["properties_sha256"], "Executable properties changed")
    command = ["inspect-local-pulse" if mode == "inspect" else "run-local-pulse", str(source), str(target),
               "base64:" + base64.b64encode(properties).decode("ascii")]
    operation = "inspect_local_pulse" if mode == "inspect" else "run_local_pulse"
    if directory.exists():
        require(not (directory / "validation_failure.json").exists(), "Prior stage validation failed; use a new output")
        response, target, record = execution.completed_stage(directory)
        require(record["command"] == command, "Saved stage used another executable configuration")
    else:
        response, target, record = execution.execute_branch(directory, command, source, target, operation,
            entrypoint=__file__, additional_sources=_sources(output))
    try:
        require(record.get("status") == "COMPLETE" and record.get("operation") == operation
                and record.get("command") == command, "Saved stage operation or command differs")
        require(Path(record["source_model"]).resolve() == source.resolve()
                and Path(response["source_model"]).resolve() == source.resolve()
                and Path(record["output_model"]).resolve() == target.resolve()
                and Path(response["output_model"]).resolve() == target.resolve(), "Saved stage model association differs")
        require(record["source_sha256_before"] == record["source_sha256_after"]
                == plan["inputs"]["checkpoint_sha256"], "Saved stage checkpoint identity differs")
        require(response.get("properties_sha256") == plan["properties_sha256"], "Actual parameter payload identity differs")
        validation = pulse_validation.validate_pulse_readback(response, plan["config"],
            mode="inspect" if mode == "inspect" else "run", prepare=prepare)
        require(validation["status"] == "PASS", "Actual local pulse validation failed")
        if mode == "solve":
            steps = validate_branch_steps(directory, response, plan["analysis_plan"])
            require(steps["status"] == "PASS", "Actual internal step constraints failed")
            validation["steps"] = steps
        if (directory / "validation.json").exists():
            require(read(directory / "validation.json") == validation, "Previous stage validation changed")
        else:
            write(directory / "validation.json", validation)
        return response, target, record
    except Exception as exc:
        if not (directory / "validation_failure.json").exists():
            write(directory / "validation_failure.json", {"status": "FAIL", "error": str(exc),
                                                           "traceback": traceback.format_exc()})
        raise


def _analyze(output, plan):
    references = plan["inputs"]["controls"]
    controls = {}
    for name, row in references.items():
        directory = Path(row["sequence_dir"])
        require(sha(directory / "sequence_manifest.json") == row["manifest_sha256"], "Control manifest changed")
        controls[name] = load_full_field_sequence(directory)
        require(_same_sha(controls[name]["metadata"]["source_sha256"], row["source_sha256"]), "Control model identity changed")
    fields = load_full_field_sequence(output / "sample_solve/fields")
    _, model, execution_record = execution.completed_stage(output / "solve")
    require(_same_sha(fields["metadata"]["source_sha256"], execution_record["output_sha256"])
            and Path(fields["metadata"]["source_model"]).resolve() == model.resolve(), "Fields came from another model")
    report = branch_pulse.compare_branch_fields(controls["baseline"], controls["zero"], fields, plan["analysis_plan"])
    if plan["config"]["pulse"]["acceleration_m_s2"] == 0:
        response = report["response"]
        bound = max(plan["analysis_plan"]["response_acceptance"]["pre_event_floor_over_U"],
                    2 * response["noise_floor_over_U"])
        peak = max(response["roi_velocity_per_frame_over_U"])
        report["zero_control"] = {"peak_roi_over_U": peak, "bound_over_U": bound, "passed": peak <= bound}
        report["status"] = "PASS" if report["restart"]["passed"] and peak <= bound else "FAIL"
        report["scope"] += "; zero-amplitude control requires no detectable signal, not a response floor"
    report["config_sha256"] = plan["config_sha256"]
    report["source_validation"] = read(output / "solve/validation.json")
    require(sha(Path(plan["inputs"]["checkpoint"])) == plan["inputs"]["checkpoint_sha256"], "Common checkpoint changed")
    destination = output / "analysis.json"
    if destination.exists():
        require(read(destination) == report, "Previous analysis changed")
    else:
        write(destination, report)
    require(report["status"] == "PASS", "Field response/restart gates failed; preserved analysis.json")
    return report


def run(config_path, output, data_root=ROOT, stage="all"):
    require(stage in ("precheck", "inspect", "solve", "sample", "analyze", "all"), "Unknown stage")
    output = Path(output).resolve()
    plan, prepare = freeze_run(config_path, output, data_root)
    if stage == "precheck":
        return {"status": "PASS", "stage": stage, "time_solver_executed": False}
    if stage in ("inspect", "solve", "all"):
        _native_stage(output, plan, prepare, "inspect")
    if stage in ("sample", "analyze"):
        require(all((output / mode / "validation.json").is_file() for mode in ("inspect", "solve")),
                "Sampling/analysis requires already validated inspect and solve stages")
        _native_stage(output, plan, prepare, "inspect")
    if stage in ("solve", "sample", "analyze", "all"):
        _native_stage(output, plan, prepare, "solve")
    if stage in ("sample", "all"):
        execution.sample_branch(output, "solve", plan["analysis_plan"], entrypoint=__file__,
                                additional_sources=_sources(output))
    require(sha(Path(plan["inputs"]["checkpoint"])) == plan["inputs"]["checkpoint_sha256"], "Common checkpoint changed")
    return _analyze(output, plan) if stage in ("analyze", "all") else {"status": "PASS", "stage": stage}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/m3_local_pulse.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("HYDRO_DATA_ROOT", ROOT)))
    parser.add_argument("--stage", choices=("precheck", "inspect", "solve", "sample", "analyze", "all"), default="all")
    args = parser.parse_args(argv)
    report = run(args.config, args.output, args.data_root, args.stage)
    print(json.dumps({"status": report["status"], "stage": args.stage, "output": str(args.output)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
