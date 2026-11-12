"""Run only the two additional frozen M3 pulse amplitudes; reuse accepted controls."""
from __future__ import annotations

import argparse
import copy
import os
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA_ROOT = Path(os.environ.get("HYDRO_DATA_ROOT", str(ROOT))).resolve()
SERIES_ID = "m3-pulse-amplitude-series-v1"
SERIES_PLAN_PATH = ROOT / "docs/evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json"
SERIES_PLAN_SHA = "75b08df846652372f7c01a1e8699f67cf57912c44b45b719135d701b7f8c4f8d"
BASE_PLAN_SHA = "72343d29335f761b82a5c3aa5b269b027fca82217c6cb69adbd537e8787125ad"
RESTART_PROTOCOL_SHA = "586a7065a7e9da4560501a368ab68037b0bbdaebf4957e6bab9541bb16e8acb8"
PROTOCOL_ID = "native-restart-consistent-off-v2"
from hydro_disturb_bench.branch_pulse import compare_branch_fields, validate_native_branch_evidence
from hydro_disturb_bench.experiments import branch_execution as execution
from hydro_disturb_bench.experiments.evidence_io import (
    file_sha256 as sha,
)
from hydro_disturb_bench.experiments.evidence_io import (
    read_json as read,
)
from hydro_disturb_bench.experiments.evidence_io import (
    require,
)
from hydro_disturb_bench.experiments.evidence_io import (
    write_json as write,
)
from hydro_disturb_bench.experiments.solver_steps import validate_branch_steps
from hydro_disturb_bench.field_sequence import load_full_field_sequence

SCALED_REFERENCES = ("peak_force_density_N_m3", "peak_force_per_depth_N_m", "impulse_per_depth_N_s_m")
ALLOWED_CHANGES = ["pulse.acceleration_amplitude_m_s2"] + [
    "source_integral_reference." + key for key in SCALED_REFERENCES
] + ["source_model resolved to the same pinned file under HYDRO_DATA_ROOT",
     "restart_protocol attached from its immutable file"]


def _path(name, root=None):
    root = DATA_ROOT if root is None else root
    relative = Path(name)
    require(not relative.is_absolute() and ".." not in relative.parts, "Frozen paths must be relative and contained")
    target = (root / relative).resolve()
    require(target.is_relative_to(root.resolve()), "Frozen path escapes its root")
    return target


def _contracts(series):
    require(series.get("series_id") == SERIES_ID, "Unexpected amplitude series ID")
    require(series.get("new_amplitudes_m_s2") == [.0025, .01], "Only the two frozen new amplitudes are allowed")
    require(series.get("cases") == [
        {"name": "a0025", "amplitude_m_s2": .0025, "mode": "new"},
        {"name": "a0050", "amplitude_m_s2": .005, "mode": "reuse"},
        {"name": "a0100", "amplitude_m_s2": .01, "mode": "new"}], "Case identities or reuse policy differ")
    require(series.get("derived_plan_allowed_changes") == ALLOWED_CHANGES, "Derived-plan change authority differs")
    base_ref, protocol_ref = series["base_plan"], series["restart_protocol"]
    require(base_ref["sha256"] == BASE_PLAN_SHA and protocol_ref["sha256"] == RESTART_PROTOCOL_SHA,
            "Old physical plan/protocol hashes are not the fixed accepted identities")
    base_path, protocol_path = _path(base_ref["path"], ROOT), _path(protocol_ref["path"], ROOT)
    require(sha(base_path) == BASE_PLAN_SHA and sha(protocol_path) == RESTART_PROTOCOL_SHA,
            "Frozen physical plan or restart protocol bytes changed")
    base, protocol = read(base_path), read(protocol_path)
    require(protocol["physical_plan_sha256"] == BASE_PLAN_SHA
            and protocol_ref["id"] == protocol["id"] == PROTOCOL_ID, "Restart protocol binding differs")
    require(series["source_model"]["path"] == base["source_model"]
            and series["source_model"]["sha256"] == base["source_sha256"], "Frozen source model differs")
    pulse = base["pulse"]
    contract = {key: base[key] for key in ("source_solnum", "branch_time_s", "stop_s", "output_step_s", "frames", "grid_shape_nx_ny")}
    contract.update({"pulse_center_m": pulse["center_m"], "pulse_sigma_m": pulse["sigma_m"],
                     "direction": pulse["direction"], "pulse_on_s": pulse["on_s"], "pulse_off_s": pulse["off_s"],
                     **{key: base["solver"][key] for key in ("rtol", "maxstepconstraintbdf", "maxstepbdf_s")},
                     "consistent": protocol["solver_change"]["both_branches"]})
    require(all(series["unchanged_contract"].get(key) == value for key, value in contract.items()),
            "Series changes an unchanged physical, time, grid or solver contract")
    return base, protocol


def load_frozen_plan(path=None, expected_sha256=None):
    path = (SERIES_PLAN_PATH if path is None else Path(path)).resolve()
    expected = SERIES_PLAN_SHA if expected_sha256 is None else expected_sha256
    require(sha(path) == expected, "Frozen amplitude-series plan bytes changed")
    series = read(path)
    _contracts(series)
    require(not any(key.startswith("_frozen_") for key in series), "Reserved plan metadata appears in file")
    series["_frozen_plan_path"], series["_frozen_plan_sha256"] = str(path), expected
    return series


def derive_plan(series_plan, amplitude):
    base, protocol = _contracts(series_plan)
    require(not isinstance(amplitude, bool) and amplitude in (.0025, .005, .01), "Unsupported derived amplitude")
    derived = copy.deepcopy(base)
    derived["pulse"]["acceleration_amplitude_m_s2"] = amplitude
    for key in SCALED_REFERENCES:
        derived["source_integral_reference"][key] = base["source_integral_reference"][key] * (amplitude / .005)
    derived["source_model"] = str(_path(base["source_model"]))
    derived["restart_protocol"] = copy.deepcopy(protocol)
    return derived


def _same_sha(*values):
    return bool(values) and all(isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{64}", value)
                               for value in values) and len({value.lower() for value in values}) == 1


def validate_sample(fields, model, execution):
    metadata = fields["metadata"]
    require(_same_sha(metadata.get("source_sha256"), execution["output_sha256"])
            and Path(metadata.get("source_model", "")).resolve() == Path(model).resolve(),
            "Sampled fields are not bound to the corresponding solved model")


def validate_reference(series_plan):
    base = derive_plan(series_plan, .005)
    inventory = {}
    for item in series_plan["reference_files"]:
        path = _path(item["path"])
        require(path not in inventory, "Duplicate frozen reference file")
        require(path.is_file() and path.stat().st_size == item["bytes"] and _same_sha(sha(path), item["sha256"]),
                "Frozen reference file differs: " + item["path"])
        inventory[path] = item
    def pinned(path):
        require(path.resolve() in inventory, "Required reference is absent from frozen inventory: " + str(path))
        return inventory[path.resolve()]
    source, checkpoint = _path(series_plan["source_model"]["path"]), _path(series_plan["checkpoint"]["path"])
    require(pinned(source)["sha256"] == base["source_sha256"]
            and pinned(checkpoint)["sha256"] == series_plan["checkpoint"]["sha256"], "Pinned model identities disagree")
    def stage(directory, operation, source_path, source_hash):
        for name in ("execution.json", "readback.json"):
            pinned(directory / name)
        record, response = read(directory / "execution.json"), read(directory / "readback.json")
        model = Path(record["output_model"]).resolve()
        require(record.get("status") == "COMPLETE" and response.get("status") == "SUCCESS"
                and record.get("operation") == response.get("operation") == operation, "Reference operation/status differs")
        require(_same_sha(record["output_sha256"], pinned(model)["sha256"])
                and _same_sha(record["readback_sha256"], pinned(directory / "readback.json")["sha256"]), "Reference output/readback binding differs")
        require(Path(record["source_model"]).resolve() == Path(response["source_model"]).resolve() == source_path
                and record["source_sha256_before"] == record["source_sha256_after"] == source_hash
                and Path(response["output_model"]).resolve() == model, "Reference source/checkpoint binding differs")
        return response, model, record
    reference_run = _path(series_plan["reference_run"])
    prepare, prepared_model, prepare_record = stage(reference_run / "prepare", "prepare_branch_state", source, base["source_sha256"])
    require(prepared_model == checkpoint, "Reference preparation uses another checkpoint")
    actual_identity = {
        "native_state_sha256": prepare["native_vectors"]["source"]["sha256"],
        "native_derivative_sha256": prepare["native_derivatives"]["source"]["sha256"],
        "native_dof_mapping_sha256": prepare["source_dof_mapping"]["sha256"],
        "mesh_fingerprint": prepare["mesh_before"],
        "native_dof_count": prepare["native_vectors"]["source"]["count"],
    }
    require(series_plan["native_identity"] == actual_identity, "Actual native state/derivative/mapping/mesh differs from frozen series identity")
    refs = {"prepare": prepare, "checkpoint": checkpoint, "prepare_record": prepare_record,
            "reference_file_count": len(inventory)}
    for name, amplitude in (("zero", 0.), ("a0050", .005)):
        ref = series_plan["references"][name]
        require(ref["amplitude_m_s2"] == amplitude, "Frozen reused amplitude differs")
        response, model, record = stage(_path(ref["stage_dir"]), "run_pulse_branch", checkpoint, series_plan["checkpoint"]["sha256"])
        require(response.get("amplitude_m_s2") == amplitude and response.get("restart_protocol_id") == PROTOCOL_ID,
                "Actual reused amplitude or restart protocol differs")
        require(record.get("command") == ["run-pulse-branch", str(checkpoint), str(model), str(amplitude), PROTOCOL_ID],
                "Reused branch command/checkpoint/amplitude/restart protocol differs")
        fields_path = _path(ref["fields_dir"])
        pinned(fields_path / "sequence_manifest.json")
        pinned(fields_path.parent / "execution.json")
        sample_record = read(fields_path.parent / "execution.json")
        require(_same_sha(sample_record["source_sha256"], record["output_sha256"])
                and Path(sample_record["source_model"]).resolve() == model
                and _same_sha(sample_record["manifest_sha256"], pinned(fields_path / "sequence_manifest.json")["sha256"]),
                "Reused sample execution/manifest differs")
        fields = load_full_field_sequence(fields_path)
        validate_sample(fields, model, record)
        refs.update({name: response, name + "_model": model, name + "_record": record, name + "_fields": fields})
    baseline_path = _path(series_plan["baseline_fields"])
    pinned(baseline_path / "sequence_manifest.json")
    refs["baseline"] = load_full_field_sequence(baseline_path)
    validate_sample(refs["baseline"], source, {"output_sha256": base["source_sha256"]})
    for name in ("report.json", "native_validation.json", "step_validation.json", "field_comparison.json"):
        path = reference_run / "analysis" / name
        pinned(path)
        require(read(path).get("status") == "PASS", "Accepted reference analysis is not PASS")
    refs["a0050_comparison"] = read(reference_run / "analysis/field_comparison.json")
    native = validate_native_branch_evidence(prepare, refs["zero"], refs["a0050"], base)
    require(native["status"] == "PASS", "Reused native evidence fails validation")
    refs["native_reference_validation"] = native
    return refs


def _validate_new(response, target, record, checkpoint, amplitude, series):
    require(response.get("series_id") == SERIES_ID and response.get("restart_protocol_id") == PROTOCOL_ID
            and response.get("amplitude_m_s2") == amplitude, "Actual new series ID/protocol/amplitude differs")
    require(Path(response["source_model"]).resolve() == Path(record["source_model"]).resolve() == checkpoint.resolve()
            and record["source_sha256_before"] == record["source_sha256_after"] == series["checkpoint"]["sha256"]
            and Path(response["output_model"]).resolve() == target.resolve(), "New branch is not bound to the pinned checkpoint/model")
    require(record["command"] == ["run-pulse-series-branch", str(checkpoint), str(target), str(amplitude), PROTOCOL_ID, SERIES_ID],
            "New branch command differs from the explicit frozen series")
    for name in ("configured", "after"):
        value = response[name]["parameters"]["branch_A"]
        require(value.get("value") == amplitude and value.get("unit") == "m/s^2", "Actual branch_A differs")


def _completed_new(output, branch, amplitude, series, checkpoint):
    directory = output / branch
    metadata = read(directory / "series_execution.json")
    require(metadata.get("status") == "COMPLETE" and metadata.get("series_id") == SERIES_ID
            and metadata.get("series_plan_sha256") == series["_frozen_plan_sha256"]
            and metadata.get("amplitude_m_s2") == amplitude,
            "Existing branch lacks completed matching series execution metadata")
    require(metadata["readback_sha256"] == sha(directory / "readback.json")
            and metadata["execution_sha256"] == sha(directory / "execution.json"), "Series execution/readback binding differs")
    implementations = metadata["implementation"]
    require(set(implementations) == {str(Path(__file__).resolve()), str(Path(series["_frozen_plan_path"]))}
            and all(item["sha256_before"] == item["sha256_after"] for item in implementations.values())
            and implementations[str(Path(series["_frozen_plan_path"]))]["sha256_before"] == series["_frozen_plan_sha256"],
            "Series implementation/plan version binding differs")
    result = execution.completed_stage(directory)
    require(result[1].resolve() == (directory / f"{branch}.mph").resolve(), "Completed branch model is at another path")
    _validate_new(*result, checkpoint, amplitude, series)
    return result


def _solve_new(output, checkpoint, branch, amplitude, series_plan, derived_plan):
    require({"name": branch, "amplitude_m_s2": amplitude, "mode": "new"} in series_plan["cases"], "This case is not a frozen new solve")
    require(derived_plan == derive_plan(series_plan, amplitude), "Derived plan changes more than the authorized amplitude references")
    directory = output / branch
    if directory.exists():
        return _completed_new(output, branch, amplitude, series_plan, checkpoint)
    source_files = [Path(__file__).resolve(), Path(series_plan["_frozen_plan_path"])]
    before = {str(path): sha(path) for path in source_files}
    target = directory / f"{branch}.mph"
    status, error = "FAIL", None
    started = time.time()
    try:
        result = execution.execute_branch(directory, ["run-pulse-series-branch", str(checkpoint), str(target), str(amplitude), PROTOCOL_ID, SERIES_ID],
                                 checkpoint, target, "run_pulse_branch", entrypoint=Path(__file__),
                                 additional_sources=(Path(series_plan["_frozen_plan_path"]),))
        _validate_new(*result, checkpoint, amplitude, series_plan)
        require(all(sha(Path(path)) == value for path, value in before.items()), "New runner or series plan changed during solve")
        status = "COMPLETE"
        return result
    except Exception as exc:
        error = {"error": str(exc), "traceback": traceback.format_exc()}
        raise
    finally:
        if directory.exists():
            write(directory / "series_execution.json", {"status": status, "series_id": SERIES_ID,
                  "series_plan_sha256": series_plan["_frozen_plan_sha256"], "amplitude_m_s2": amplitude,
                  "readback_sha256": sha(directory / "readback.json") if (directory / "readback.json").is_file() else None,
                  "execution_sha256": sha(directory / "execution.json") if (directory / "execution.json").is_file() else None,
                  "implementation": {name: {"sha256_before": digest, "sha256_after": sha(Path(name))}
                                     for name, digest in before.items()}, "error": error, "elapsed_seconds": time.time() - started})


def _analyze_case(output, case, series, refs):
    name, amplitude = case["name"], case["amplitude_m_s2"]
    directory = output / "analysis" / name
    directory.mkdir(parents=True, exist_ok=False)
    try:
        derived = derive_plan(series, amplitude)
        response, model, execution = _completed_new(output, name, amplitude, series, refs["checkpoint"])
        fields = load_full_field_sequence(output / f"sample_{name}/fields")
        validate_sample(fields, model, execution)
        native = validate_native_branch_evidence(refs["prepare"], refs["zero"], response, derived)
        steps = validate_branch_steps(output / name, response, derived)
        comparison = compare_branch_fields(refs["baseline"], refs["zero_fields"], fields, derived)
        for filename, data in (("native_validation.json", native), ("step_validation.json", steps), ("field_comparison.json", comparison)):
            write(directory / filename, data)
        report = {"status": "PASS" if native["status"] == steps["status"] == comparison["status"] == "PASS" else "FAIL",
                  "series_id": SERIES_ID, "series_plan_sha256": series["_frozen_plan_sha256"],
                  "amplitude_m_s2": amplitude, "output_model_sha256": execution["output_sha256"],
                  "peak_roi_over_U": comparison["response"]["peak_roi_over_U"],
                  "peak_time_s": comparison["response"]["peak_time_s"], "native_status": native["status"],
                  "step_status": steps["status"], "field_status": comparison["status"]}
        write(directory / "report.json", report)
        return report
    except Exception as exc:
        write(directory / "failure.json", {"status": "FAIL", "error": str(exc), "traceback": traceback.format_exc()})
        raise


def _reference_integrity_after(series):
    checks = []
    for item in series["reference_files"]:
        path = _path(item["path"])
        size = path.stat().st_size if path.is_file() else None
        digest = sha(path) if path.is_file() else "MISSING"
        checks.append({"path": item["path"], "expected_bytes": item["bytes"], "actual_bytes": size,
                       "expected_sha256": item["sha256"], "actual_sha256": digest,
                       "unchanged": size == item["bytes"] and _same_sha(digest, item["sha256"])})
    return {"status": "PASS" if all(item["unchanged"] for item in checks) else "FAIL",
            "checked_files": len(checks), "files": checks}


def run_series(series_plan, output, stage="all"):
    require(stage in ("precheck", "solve", "sample", "analyze", "all"), "Unknown series stage")
    require(sha(Path(series_plan["_frozen_plan_path"])) == series_plan["_frozen_plan_sha256"], "Series plan changed before entry")
    output = Path(output).resolve()
    protected = [_path(series_plan[key]) for key in ("reference_run", "baseline_fields")]
    protected.extend(_path(series_plan[key]["path"]).parent for key in ("source_model", "checkpoint"))
    require(not any(output.is_relative_to(path) for path in protected), "New output cannot reuse a historical run")
    refs = validate_reference(series_plan)  # Every invocation validates before any external solve or sampling.
    marker = {"series_id": SERIES_ID, "series_plan_sha256": series_plan["_frozen_plan_sha256"],
              "source_sha256": series_plan["source_model"]["sha256"], "checkpoint_sha256": series_plan["checkpoint"]["sha256"]}
    if output.exists():
        require(read(output / "run_manifest.json") == marker, "Output is not this frozen series run")
        require(sha(output / "preregistered_plan.json") == marker["series_plan_sha256"], "Output plan copy changed")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "preregistered_plan.json").write_bytes(Path(series_plan["_frozen_plan_path"]).read_bytes())
        write(output / "run_manifest.json", marker)
    precheck = output / "reference_precheck.json"
    if not precheck.exists():
        write(precheck, {"status": "PASS", **marker, "checked_reference_files": refs["reference_file_count"],
                         "reused_amplitudes_m_s2": [0., .005], "new_time_solves_started_by_precheck": 0})
    reports = []
    for case in series_plan["cases"]:
        if case["mode"] != "new":
            continue
        name, amplitude = case["name"], case["amplitude_m_s2"]
        derived = derive_plan(series_plan, amplitude)
        if stage in ("all", "solve"):
            # Recheck the shared immutable inputs immediately before each new Time solve.
            refs = validate_reference(series_plan)
            _solve_new(output, refs["checkpoint"], name, amplitude, series_plan, derived)
        if stage in ("all", "sample"):
            _completed_new(output, name, amplitude, series_plan, refs["checkpoint"])
            execution.sample_branch(output, name, derived, entrypoint=Path(__file__),
                                    additional_sources=(Path(series_plan["_frozen_plan_path"]),))
            _, model, record = execution.completed_stage(output / name)
            validate_sample(load_full_field_sequence(output / f"sample_{name}/fields"), model, record)
        if stage in ("all", "analyze"):
            item = _analyze_case(output, case, series_plan, refs)
            reports.append(item)
            if item["status"] != "PASS":
                break
    if stage in ("all", "analyze"):
        integrity = _reference_integrity_after(series_plan)
        write(output / "reference_integrity_after.json", integrity)
        require(integrity["status"] == "PASS", "Frozen historical reference files changed during this series")
        original = refs["a0050_comparison"]
        reports.append({"status": original["status"], "amplitude_m_s2": .005, "reused": True,
                        "peak_roi_over_U": original["response"]["peak_roi_over_U"], "peak_time_s": original["response"]["peak_time_s"]})
        reports.sort(key=lambda item: item["amplitude_m_s2"])
        report = {"status": "PASS" if len(reports) == 3 and all(item["status"] == "PASS" for item in reports) else "FAIL",
                  **marker, "cases": reports, "trend_acceptance": "Descriptive only; no linearity or monotonicity pass gate",
                  "reference_integrity_after": "../reference_integrity_after.json",
                  "response_per_amplitude": [{"amplitude_m_s2": item["amplitude_m_s2"],
                                              "peak_roi_over_U_per_m_s2": item["peak_roi_over_U"] / item["amplitude_m_s2"]} for item in reports]}
        (output / "analysis").mkdir(exist_ok=True)
        write(output / "analysis/report.json", report)
        return report
    return {"status": "STAGE_COMPLETE", "stage": stage, **marker, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("precheck", "solve", "sample", "analyze", "all"), default="all")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else DATA_ROOT / args.output
    report = run_series(load_frozen_plan(), output, args.stage)
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] != "FAIL" else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
