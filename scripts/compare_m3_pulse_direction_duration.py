"""Recompute the frozen M3 direction/duration comparison without running COMSOL."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench import branch_pulse, pulse_parameters, pulse_validation
from hydro_disturb_bench.experiments.branch_execution import completed_stage
from hydro_disturb_bench.experiments.evidence_io import file_sha256 as sha
from hydro_disturb_bench.experiments.evidence_io import read_json as read
from hydro_disturb_bench.experiments.evidence_io import require, write_json
from hydro_disturb_bench.experiments.solver_steps import validate_branch_steps
from hydro_disturb_bench.field_sequence import load_full_field_sequence
from hydro_disturb_bench.pulse_variant_comparison import compare_pulse_variants

SERIES_PATH = ROOT / "docs/evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json"
SERIES_SHA = "75b08df846652372f7c01a1e8699f67cf57912c44b45b719135d701b7f8c4f8d"
NEW_CASES = ("reverse", "short", "long")


def _inside(root, relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Expected a relative data path")
    result = (root / path).resolve()
    require(result.is_relative_to(root), "Data path escapes its root")
    return result


def _model_association(response, model, record, checkpoint, checkpoint_sha):
    require(Path(record["source_model"]).resolve() == checkpoint
            and Path(response["source_model"]).resolve() == checkpoint
            and record["source_sha256_before"] == record["source_sha256_after"] == checkpoint_sha,
            "Stage does not bind the frozen checkpoint")
    require(Path(record["output_model"]).resolve() == model.resolve()
            and Path(response["output_model"]).resolve() == model.resolve(),
            "Stage output model association differs")


def _sample(directory, model, model_sha):
    record = read(directory.parent / "execution.json")
    require(record["status"] == "COMPLETE"
            and Path(record["source_model"]).resolve() == model.resolve()
            and record["source_sha256"].lower() == model_sha.lower()
            and record["manifest_sha256"] == sha(directory / "sequence_manifest.json"),
            "Sample execution does not bind the solved model and manifest")
    fields = load_full_field_sequence(directory)
    require(Path(fields["metadata"]["source_model"]).resolve() == model.resolve()
            and fields["metadata"]["source_sha256"].lower() == model_sha.lower(),
            "Field sequence came from another solved model")
    return fields


def _new_stage(directory, mode, plan, prepare, checkpoint):
    require(not (directory / "validation_failure.json").exists(), "Stage has a validation failure")
    response, model, record = completed_stage(directory)
    _model_association(response, model, record, checkpoint, plan["inputs"]["checkpoint_sha256"])
    properties = (directory.parent / "event.properties").read_bytes()
    operation = "inspect_local_pulse" if mode == "inspect" else "run_local_pulse"
    command = [operation.replace("_", "-"), str(checkpoint), str(directory / "model.mph"),
               "base64:" + base64.b64encode(properties).decode("ascii")]
    require(record["operation"] == operation and record["command"] == command
            and model.resolve() == (directory / "model.mph").resolve()
            and response["properties_sha256"] == plan["properties_sha256"],
            "Stage operation or executable parameters differ from the frozen plan")
    validation = pulse_validation.validate_pulse_readback(
        response, plan["config"], mode="inspect" if mode == "inspect" else "run", prepare=prepare)
    require(validation["status"] == "PASS", "Actual pulse readback validation failed")
    if mode == "solve":
        validation["steps"] = validate_branch_steps(directory, response, plan["analysis_plan"])
        require(validation["steps"]["status"] == "PASS", "Actual internal step log failed")
    require(read(directory / "validation.json") == validation, "Saved stage validation differs from recomputation")
    return response, model, record, validation


def collect_comparison(plan_path, expected_sha, data_root):
    """Verify immutable inputs, load formal sequences and return a compact report."""
    plan_path, data_root = Path(plan_path).resolve(), Path(data_root).resolve()
    require(sha(plan_path) == expected_sha, "Frozen study plan SHA-256 differs")
    study, freeze = read(plan_path), read(plan_path.with_name("freeze_record.json"))
    require(freeze["plan_sha256"] == expected_sha and freeze["time_solver_executed"] is False
            and freeze["frozen_at_utc"] == study["frozen_at_utc"], "Study freeze record differs")
    require(set(study["cases"]) == set(NEW_CASES), "Expected exactly the three frozen new cases")
    require(sha(SERIES_PATH) == SERIES_SHA, "Accepted reference catalogue changed")
    series = read(SERIES_PATH)
    require(study["checkpoint"] == series["checkpoint"]
            and study["default_reference"] == series["references"]["a0050"]
            and study["zero_reference"] == series["references"]["zero"]
            and study["baseline_fields"] == series["baseline_fields"], "Study references differ from accepted catalogue")
    identities = {str(plan_path): expected_sha, str(SERIES_PATH): SERIES_SHA}

    def pin(path, expected=None):
        digest = sha(path)
        require(expected is None or digest == expected, f"Input identity changed: {path}")
        identities[str(path)] = digest
        return digest

    # Includes original solved models, checkpoint, execution/readback, manifests,
    # accepted native/field/step evidence, and primary solver stdout logs.
    for row in series["reference_files"]:
        pin(_inside(data_root, row["path"]), row["sha256"])
    checkpoint = _inside(data_root, study["checkpoint"]["path"])
    pin(checkpoint, study["checkpoint"]["sha256"])
    source = _inside(data_root, series["source_model"]["path"])
    pin(source, series["source_model"]["sha256"])
    reference_run = _inside(data_root, series["reference_run"])
    accepted = read(reference_run / "analysis/report.json")
    require(all(accepted[key] == "PASS" for key in ("status", "native_status", "field_status", "step_status")),
            "Original reference acceptance did not pass")
    prepare_path = reference_run / "prepare/readback.json"
    prepare = read(prepare_path)
    require(Path(prepare["output_model"]).resolve() == checkpoint
            and prepare["output_sha256"] == study["checkpoint"]["sha256"], "Prepare does not bind the checkpoint")
    controls, variants, details = {}, {}, {}
    for name, key in (("default", "default_reference"), ("zero", "zero_reference")):
        row = study[key]
        directory = _inside(data_root, row["stage_dir"])
        response, model, record = completed_stage(directory)
        _model_association(response, model, record, checkpoint, study["checkpoint"]["sha256"])
        fields = _sample(_inside(data_root, row["fields_dir"]), model, record["output_sha256"])
        (variants if name == "default" else controls)[name] = fields
        details[name] = {"reused": True, "model_sha256": record["output_sha256"],
                         "readback_sha256": record["readback_sha256"]}

    frozen_controls = None
    for name in NEW_CASES:
        case = study["cases"][name]
        output = _inside(data_root, case["run_dir"])
        run_path = output / "plan.json"
        pin(run_path, case["run_plan_sha256"])
        require((output / "plan.sha256").read_text().strip() == case["run_plan_sha256"], "Run plan freeze differs")
        plan = read(run_path)
        config_path = _inside(ROOT, case["config_path"])
        pin(config_path, case["config_sha256"])
        pin(output / "config.input.json", case["config_sha256"])
        require(read(config_path) == case["config"] == plan["config"]
                and plan["config_sha256"] == case["config_sha256"], "Frozen case configuration differs")
        properties = pulse_parameters.properties_payload(plan["config"]).encode("ascii")
        require((output / "event.properties").read_bytes() == properties
                and hashlib.sha256(properties).hexdigest() == plan["properties_sha256"], "Executable properties differ")
        pin(output / "event.properties", plan["properties_sha256"])
        inputs = plan["inputs"]
        require(Path(inputs["checkpoint"]).resolve() == checkpoint
                and inputs["checkpoint_sha256"] == study["checkpoint"]["sha256"]
                and Path(inputs["prepare_readback"]).resolve() == prepare_path
                and inputs["prepare_sha256"] == sha(prepare_path)
                and inputs["series_sha256"] == SERIES_SHA, "Frozen native inputs differ")
        if frozen_controls is None:
            frozen_controls = inputs["controls"]
            require(set(frozen_controls) == {"baseline", "zero"}, "Missing frozen control sequences")
            for control, relative in (("baseline", study["baseline_fields"]),
                                      ("zero", study["zero_reference"]["fields_dir"])):
                row = frozen_controls[control]
                directory = _inside(data_root, relative)
                require(Path(row["sequence_dir"]).resolve() == directory, "Frozen control path differs")
                pin(directory / "sequence_manifest.json", row["manifest_sha256"])
                fields = controls.get(control)
                if fields is None:
                    fields = load_full_field_sequence(directory)
                require(fields["metadata"]["source_sha256"].lower() == row["source_sha256"].lower(),
                        "Control source model SHA differs")
                if control == "baseline":
                    require(Path(fields["metadata"]["source_model"]).resolve() == source
                            and row["source_sha256"].lower() == series["source_model"]["sha256"],
                            "Baseline is not the accepted M3 model")
                controls[control] = fields
        require(inputs["controls"] == frozen_controls, "Cases use different control identities")
        _new_stage(output / "inspect", "inspect", plan, prepare, checkpoint)
        response, model, record, validation = _new_stage(output / "solve", "solve", plan, prepare, checkpoint)
        fields = _sample(output / "sample_solve/fields", model, record["output_sha256"])
        individual = branch_pulse.compare_branch_fields(controls["baseline"], controls["zero"], fields, plan["analysis_plan"])
        individual.update(config_sha256=plan["config_sha256"], source_validation=validation)
        require(individual["status"] == "PASS" and individual == read(output / "analysis.json"),
                "Individual field acceptance differs or failed")
        variants[name] = fields
        for relative in ("inspect/execution.json", "inspect/readback.json", "inspect/validation.json",
                         "solve/execution.json", "solve/readback.json", "solve/validation.json",
                         "sample_solve/execution.json", "sample_solve/fields/sequence_manifest.json", "analysis.json"):
            pin(output / relative)
        details[name] = {"reused": False, "config": plan["config"], "actual_pulse": response["actual_pulse"],
                         "model_sha256": record["output_sha256"], "individual_status": individual["status"],
                         "prescribed_source_diagnostics": validation["prescribed_source_diagnostics"],
                         "actual_saved_time_source": validation["actual_saved_time_source"],
                         "steps": validation["steps"]}
    result = compare_pulse_variants(controls["baseline"], controls["zero"], variants, study["common_analysis_plan"])
    return {"study_id": study["study_id"], "plan_sha256": expected_sha,
            **result, "case_evidence": details, "input_file_sha256": identities,
            "execution_scope": "Read-only recomputation of saved evidence and sampled fields; no COMSOL invocation"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("HYDRO_DATA_ROOT", ROOT)))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    require(not args.output.exists(), "Comparison output already exists")
    report = collect_comparison(args.plan, args.expected_sha256, args.data_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps({"status": report["status"], "output": str(args.output)}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
