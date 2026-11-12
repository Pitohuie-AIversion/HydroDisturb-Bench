"""Audit lightweight files from disk or Git blobs, without CFD data/dependencies.

Verifies provenance links, actual parameter/source tables, sampled-time metadata,
accepted-step logs and test records. It does not recompute native state or fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

FOLDER = "docs/evidence/m3_pulse_direction_duration_20261109"
REPORT = "docs/reports/M3_PULSE_DIRECTION_DURATION_20261109.md"
STUDY = "m3-pulse-direction-duration-20261109"
CASES = ("reverse", "short", "long")
LIMIT = 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value, "Unsafe delivery path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == value, "Unsafe delivery path")
    return value


def close(left, right):
    return isinstance(left, (int, float)) and not isinstance(left, bool) and math.isfinite(left) and math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def same_array(actual, expected):
    return isinstance(actual, list) and len(actual) == len(expected) and all(close(a, b) for a, b in zip(actual, expected))


def parse_steps(text, start=180., stop=200., cap=.05):
    """Dependency-free recheck of accepted rows; export also uses project parser."""
    headers, output_rows, initial, rows = 0, 0, False, []
    for line in text.splitlines():
        if re.match(r"^\s*Step\s+Time\s+Stepsize(?:\s|$)", line):
            headers += 1
            continue
        match = re.match(r"^\s*(\d+|-)\s+(\S+)\s+(\S+)(?:\s|$)", line) if headers else None
        if not match or (match[1] == "-" and not re.search(r"\sout(?:\s|$)", line)):
            continue
        t = float(match[2])
        require(math.isfinite(t), "Nonfinite step time")
        if match[3] == "-":
            require(re.search(r"\sout(?:\s|$)", line), "Malformed output row")
            output_rows += 1
            initial |= abs(t - start) <= 1e-8
            continue
        require(match[1] != "-", "Accepted step lacks ID")
        dt = float(match[3])
        require(math.isfinite(dt) and 0 < dt <= cap * 1.0001, "Actual step violates cap")
        rows.append((int(match[1]), t, dt))
    require(headers and rows, "Missing accepted-step table")
    require(rows[0][0] in (0, 1) and [row[0] for row in rows] == list(range(rows[0][0], rows[0][0] + len(rows))), "Missing/duplicate step IDs")
    require((initial or abs(rows[0][1] - start) <= 1e-8) and rows[0][1] >= start - 1e-8
            and rows[-1][1] >= stop - 1e-8, "Incomplete solver time coverage")
    require(all(a[1] <= b[1] for a, b in zip(rows, rows[1:])) and len(rows) * cap * 1.0001 >= stop - start - 1e-8,
            "Invalid accepted-step coverage")
    return {"accepted_step_count": len(rows), "first_step_number": rows[0][0], "last_step_number": rows[-1][0],
            "final_internal_time_s": rows[-1][1], "dt_min_s": min(r[2] for r in rows), "dt_max_s": max(r[2] for r in rows),
            "output_only_rows_excluded": output_rows, "headers_seen": headers}


def required_paths(recovered=()):
    names = {"export_evidence.py", "audit_delivery.py", "export_manifest.json", "preregistered_plan.json",
             "freeze_record.json", "protected_before.json", "protected_after.json", "comparison.json",
             "reference_registry.json", "checkpoint_inspection/readback.json", "checkpoint_inspection/source_identity.json"}
    for case in CASES:
        prefix = "cases/" + case + "/"
        names.update(prefix + name for name in ("plan.json", "config.input.json", "event.properties", "sample_execution.json",
                                               "sequence_manifest.json", "sequence_report.json", "analysis.json"))
        for mode in ("inspect", "solve"):
            names.update(prefix + mode + "/" + name for name in ("readback_summary.json", "execution.json", "validation.json"))
        names.update(prefix + "solve/" + name for name in ("solver_stdout.txt", "solver_log_extraction.json", "process_execution.json"))
        names.update("logs/" + case + "_all/" + name for name in ("execution.json", "stdout.txt", "stderr.txt"))
        if case in recovered:
            names.add("recovery_source.py")
            names.update(prefix + "recovery/" + name for name in ("publication_recovery.json", "publication_retry_admission.json", "failure.json", "stage_failure.json"))
            names.update("logs/" + case + "_analyze/" + name for name in ("execution.json", "stdout.txt", "stderr.txt"))
    for suite in ("targeted", "regression"):
        names.update("tests/" + suite + "_" + suffix for suffix in ("stdout.txt", "execution.json", "junit.xml"))
    return {FOLDER + "/" + name for name in names} | {REPORT}


class Reader:
    def __init__(self, root, ref=None):
        self.root, self.ref = root, ref
        if ref and ref != "INDEX":
            self.ref = self.git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args])

    def read(self, name):
        safe(name)
        if self.ref:
            record = self.git("ls-files", "--stage", "--", name) if self.ref == "INDEX" else self.git("ls-tree", self.ref, "--", name)
            require(record.split(maxsplit=1)[0] in (b"100644", b"100755"), "Expected a regular Git file")
            spec = ":" + name if self.ref == "INDEX" else self.ref + ":" + name
            size = int(self.git("cat-file", "-s", spec))
            require(size <= LIMIT, "Oversized Git blob: " + name)
            raw = self.git("cat-file", "blob", spec)
        else:
            path = self.root / name
            require(path.resolve().is_relative_to(self.root.resolve()) and not path.is_symlink(), "Unsafe local file")
            require(path.stat().st_size <= LIMIT, "Oversized local file: " + name)
            raw = path.read_bytes()
        return raw

    def evidence(self, name):
        return self.read(FOLDER + "/" + name)

    def json(self, name):
        return json.loads(self.evidence(name))

    def original_evidence(self, name):
        """Return original hash-bound bytes, verifying a declared LF view if any."""
        normalized = self.evidence(name)
        transforms = [row for row in self.json("export_manifest.json").get("text_transforms", [])
                      if row["output"] == name and "raw_companion" in row]
        require(len(transforms) <= 1, "Ambiguous original evidence transformation")
        if not transforms:
            return normalized
        row = transforms[0]
        require(name.endswith(".json") and row["raw_companion"] == name + ".raw", "Invalid JSON raw companion")
        original = self.evidence(safe(row["raw_companion"]))
        require(sha(original) == row["source"]["sha256"] and len(original) == row["source"]["bytes"]
                and sha(normalized) == row["output_sha256"] and len(normalized) == row["output_bytes"]
                and b"\r\n" in original and original.replace(b"\r\n", b"\n") == normalized,
                "Original/LF evidence transformation differs: " + name)
        return original


def summary(value):
    require(value.get("evidence_kind") == "summary_not_full_original", "Summary disclosure absent")
    source = value["source"]
    require(source.get("storage") == "local_only" and isinstance(source.get("bytes"), int)
            and re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")), "Raw source identity absent")
    found = []
    def walk(item):
        if isinstance(item, dict):
            if item.get("omitted") is True:
                require(item.get("storage") == "local_only" and re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
                        and isinstance(item.get("bytes"), int), "Omission identity absent")
                found.append(item)
            else:
                require(not isinstance(item.get("base64"), str), "Native payload in lightweight summary")
                for child in item.values():
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value["content"])
    require(found == value["omitted"] and len(found) == value["omitted_fields_count"], "Omission inventory differs")
    return value["content"]


def check_parameters(response, config):
    pulse, actual = config["pulse"], response["actual_pulse"]
    expected = {"amplitude_m_s2": pulse["acceleration_m_s2"], "xc_m": pulse["center_m"][0],
                "yc_m": pulse["center_m"][1], "sigma_m": pulse["sigma_m"], "direction_deg": pulse["direction_deg"],
                "on_s": pulse["on_s"], "off_s": pulse["off_s"]}
    require(all(close(actual.get(key), val) for key, val in expected.items()), "Actual pulse differs from frozen configuration")
    angle = math.radians(pulse["direction_deg"])
    direction = [0. if abs(v) < 1e-15 else v for v in (math.cos(angle), math.sin(angle))]
    require(same_array(actual["direction"], direction), "Direction components differ")
    specifications = [("A", pulse["acceleration_m_s2"], "m/s^2"), ("xc", pulse["center_m"][0], "m"),
                      ("yc", pulse["center_m"][1], "m"), ("sigma", pulse["sigma_m"], "m"),
                      ("theta", pulse["direction_deg"], "deg"), ("dx", direction[0], "1"), ("dy", direction[1], "1"),
                      ("on", pulse["on_s"], "s"), ("off", pulse["off_s"], "s")]
    for name, val, unit in specifications:
        item = actual["parameters"]["local_" + name]
        require(item.get("expression") and item.get("declared_unit") == unit and close(item["value_in_declared_unit"], val), "Getter record differs: " + name)
        expected_unit = "rad" if unit == "deg" else unit
        require("unit" in item and (item["unit"] in (None, "", "1") if unit == "1" else item["unit"] == expected_unit), "Actual SI unit differs")
        require(close(item["value"], angle if unit == "deg" else val), "Actual SI parameter value differs")
    for phase in ("configured", "after"):
        snapshot = response[phase]
        for name, item in actual["parameters"].items():
            require(snapshot["parameters"][name] == {"expr": item["expression"], "value": item["value"], "unit": item["unit"]},
                    "Actual getter not bound to node snapshot")
        nodes = [node for node in snapshot["solver_features"] if node.get("type") == "Time"]
        require(len(nodes) == 1, "Time solver snapshot ambiguous")
        settings = nodes[0]["properties"]
        require(close(float(settings["rtol"]), 1e-4) and settings["maxstepconstraintbdf"] == "const"
                and close(float(settings["maxstepbdf"]), .05) and settings["consistent"] == "off", "Actual solver settings differ")


def check_source(response, spec, validation):
    times = [180 + .2 * i for i in range(101)]
    require(same_array(response["actual_times"], times) and same_array(response["actual_force_evaluation"]["actual_times"], times), "Actual solved times differ")
    force, pulse = response["actual_force_evaluation"], spec["config"]["pulse"]
    integral, reference = force["integral"], spec["source_reference"]
    require(force["dataset_tag"] == response["dataset_tag"] and force["solution_tag"] == response["solution_tag"]
            and integral["dataset_tag"] == force["dataset_tag"] and integral["units"] == ["N/m"] * 3,
            "Actual source Dataset or units differ")
    rows = integral["values_by_expression"]
    require(rows == integral["force_per_depth_by_component"] and len(rows) == 3
            and all(len(row) == 101 and all(math.isfinite(v) for v in row) for row in rows), "Actual source time series omitted or malformed")
    gate = [math.sin(math.pi * (t - pulse["on_s"]) / (pulse["off_s"] - pulse["on_s"])) ** 2
            if pulse["on_s"] < t < pulse["off_s"] else 0. for t in times]
    directions = [*reference["direction"], 0.]
    peak = reference["peak_force_per_depth_N_m"]
    for row, direction in zip(rows, directions):
        require(all(abs(v - peak * direction * g) <= max(1e-14, .01 * peak) for v, g in zip(row, gate)), "Actual source waveform differs")
        require(all(v == 0 for v, g in zip(row, gate) if not g) and (direction != 0 or all(v == 0 for v in row)), "Source is nonzero outside support/component")
    impulses = [sum((b - a) * (u + v) / 2 for a, b, u, v in zip(times, times[1:], row, row[1:])) for row in rows]
    require(same_array(impulses[:2], validation["actual_saved_time_source"]["impulse_vector_Ns_per_m"]), "Published source impulse differs from actual table")
    require(validation["actual_saved_time_source"]["passed"] is True and validation["prescribed_source_diagnostics"]["passed"] is True,
            "Source validation failed")


def check_recovery(reader, prefix, sampled, manifest):
    """Verify recovery records; full data-byte rechecks remain local evidence."""
    stage = prefix + "recovery/"
    recovery = reader.json(stage + "publication_recovery.json")
    admission = reader.json(stage + "publication_retry_admission.json")
    failure = reader.json(stage + "failure.json")
    stage_failure = reader.json(stage + "stage_failure.json")
    require(sha(reader.original_evidence(stage + "publication_recovery.json")) == sampled["publication_recovery_sha256"], "Publication recovery hash differs")
    require(recovery["status"] == "PASS" and admission["status"] == "ADMITTED"
            and recovery["time_solver_executed"] is False and recovery["export_repeated"] is False
            and recovery["source_model_unchanged"] is True and recovery["all_file_bytes_unchanged"] is True,
            "Recovery is incomplete or claims new computation")
    require(recovery["array_fields_rechecked"] == ["values", "x", "y", "times", "mask", "invalid_mask", "cylinder_mask"], "Recovery array checks incomplete")
    require(recovery["admission_sha256"] == sha(reader.original_evidence(stage + "publication_retry_admission.json"))
            and recovery["original_failure_sha256"] == admission["original_failure_sha256"] == sha(reader.original_evidence(stage + "failure.json"))
            and recovery["original_stage_failure_sha256"] == sha(reader.original_evidence(stage + "stage_failure.json")), "Original failure/admission identity differs")
    require(recovery["script_sha256"] == admission["script_sha256"] == sha(reader.original_evidence("recovery_source.py")), "Recovery source identity differs")
    require(failure["status"] == stage_failure["status"] == "FAIL" and "[WinError 5]" in failure["error"]
            and "os.rename(stage, output_dir)" in failure["traceback"], "Original publication failure absent")
    model_sha = sampled["source_sha256"].lower()
    require(admission["source_model_sha256"].lower() == failure["source_sha256_after"].lower()
            == stage_failure["source_sha256_before"].lower() == stage_failure["source_sha256_after"].lower() == model_sha
            and stage_failure["source_unchanged"] is True, "Recovery source model differs")
    require(recovery["staged_files"] == admission["staged_files"], "Recovery file inventory changed")
    rows = {safe(row["path"]): row for row in recovery["staged_files"]}
    require(len(rows) == len(recovery["staged_files"]) and rows["sequence_manifest.json"]["sha256"]
            == sha(reader.original_evidence(prefix + "sequence_manifest.json"))
            and rows["failure.json"]["sha256"] == recovery["original_stage_failure_sha256"], "Recovered inventory does not bind archived manifest/failure")
    for batch in manifest["batches"]:
        require(rows[batch["path"] + "/field_sample.npz"]["sha256"].lower() == batch["npz_sha256"].lower(), "Recovered tensor identity differs")


def check_comparison_binding(reader, comparison, study, registry):
    """Bind the small comparison to the archived input identities, not fields."""
    require(comparison.get("study_id") == study["study_id"] and comparison.get("plan_sha256")
            == sha(reader.original_evidence("preregistered_plan.json")), "Comparison frozen-plan binding differs")
    details, pins = comparison["case_evidence"], comparison["input_file_sha256"]
    require(set(details) == {"default", "zero", *CASES}, "Comparison evidence case set differs")

    def pinned(relative, expected):
        # The absolute data root may differ on a reviewer machine. Match the
        # complete, unique frozen relative path, never just the filename.
        suffix = "/" + safe(relative)
        matches = [value for path, value in pins.items() if path.replace("\\", "/").endswith(suffix)]
        require(len(matches) == 1 and matches[0].lower() == expected.lower(), "Comparison input identity differs: " + relative)

    for row in registry["reference_files"]:
        pinned(row["path"], row["sha256"])
    for name, label in (("default", "default_reference"), ("zero", "zero_reference")):
        stage = study[label]["stage_dir"]
        models = [row for row in registry["reference_files"] if row["path"].startswith(stage + "/") and row["path"].endswith(".mph")]
        readbacks = [row for row in registry["reference_files"] if row["path"] == stage + "/readback.json"]
        require(len(models) == len(readbacks) == 1 and details[name]["reused"] is True
                and details[name]["model_sha256"].lower() == models[0]["sha256"].lower()
                and details[name]["readback_sha256"].lower() == readbacks[0]["sha256"].lower(), "Comparison accepted reference differs: " + name)
    for name in CASES:
        prefix, spec = "cases/" + name + "/", study["cases"][name]
        native = reader.json(prefix + "solve/readback_summary.json")["content"]
        execution = reader.json(prefix + "solve/execution.json")
        validation = reader.json(prefix + "solve/validation.json")
        case = details[name]
        require(case["reused"] is False and case["config"] == spec["config"]
                and case["actual_pulse"] == native["actual_pulse"]
                and case["model_sha256"].lower() == execution["output_sha256"].lower()
                and case["individual_status"] == reader.json(prefix + "analysis.json")["status"] == "PASS",
                "Comparison case configuration/model differs: " + name)
        for key in ("prescribed_source_diagnostics", "actual_saved_time_source", "steps"):
            require(case[key] == validation[key], "Comparison case validation differs: " + name + "/" + key)
        pinned(spec["config_path"], spec["config_sha256"])
        for relative in ("plan.json", "config.input.json", "event.properties", "analysis.json"):
            pinned(spec["run_dir"] + "/" + relative, sha(reader.original_evidence(prefix + relative)))
        for mode in ("inspect", "solve"):
            for filename in ("execution.json", "validation.json"):
                relative = mode + "/" + filename
                pinned(spec["run_dir"] + "/" + relative, sha(reader.original_evidence(prefix + relative)))
            pinned(spec["run_dir"] + "/" + mode + "/readback.json",
                   reader.json(prefix + mode + "/readback_summary.json")["source"]["sha256"])
        for original, archived in (("sample_solve/execution.json", "sample_execution.json"),
                                   ("sample_solve/fields/sequence_manifest.json", "sequence_manifest.json")):
            pinned(spec["run_dir"] + "/" + original, sha(reader.original_evidence(prefix + archived)))


def check_case(reader, name, study):
    prefix = "cases/" + name + "/"
    spec = study["cases"][name]
    plan = reader.json(prefix + "plan.json")
    require(sha(reader.original_evidence(prefix + "plan.json")) == spec["run_plan_sha256"], "Frozen run plan hash differs")
    require(sha(reader.original_evidence(prefix + "config.input.json")) == spec["config_sha256"] == plan["config_sha256"]
            and reader.json(prefix + "config.input.json") == spec["config"] == plan["config"], "Frozen config binding differs")
    require(sha(reader.original_evidence(prefix + "event.properties")) == plan["properties_sha256"], "Executable properties hash differs")
    for mode in ("inspect", "solve"):
        stage = prefix + mode + "/"
        wrapped = reader.json(stage + "readback_summary.json")
        response = summary(wrapped)
        execution, validation = reader.json(stage + "execution.json"), reader.json(stage + "validation.json")
        require(execution["status"] == "COMPLETE" and validation["status"] == "PASS" and response["status"] == "SUCCESS", "Incomplete native evidence")
        require(execution["readback_sha256"] == wrapped["source"]["sha256"], "Execution/raw readback hash differs")
        require(execution["source_sha256_before"] == execution["source_sha256_after"] == study["checkpoint"]["sha256"]
                == plan["inputs"]["checkpoint_sha256"], "Checkpoint binding differs")
        require(response["source_model"] == execution["source_model"] and response["output_model"] == execution["output_model"], "Native model paths differ")
        require(response["event_id"] == spec["config"]["event_id"] and response["properties_sha256"] == plan["properties_sha256"]
                and response["time_solver_executed"] is (mode == "solve"), "Native event/time-solver association differs")
        require(all(key in response for key in ("before", "configured", "after", "force_feature", "initialization_readback")), "Actual node snapshots missing")
        check_parameters(response, spec["config"])
        require(execution["implementation"]["all_source_files_unchanged"] is True, "Execution source files changed")
        if mode == "solve":
            times = [180 + .2 * i for i in range(101)]
            check_source(response, spec, validation)
            log = reader.evidence(stage + "solver_stdout.txt")
            extraction = reader.json(stage + "solver_log_extraction.json")
            require(sha(log) == extraction["output_sha256"] and len(log) == extraction["output_bytes"]
                    and extraction["source"]["sha256"] == validation["steps"]["source_bytes_sha256"], "Solver log source/extraction binding differs")
            require("TIME_START" in log.decode() and "TIME_FINISHED" in log.decode() and b"<<<JSON_START>>>" not in log, "Incomplete solver log prefix")
            parsed = parse_steps(log.decode())
            require(all(validation["steps"].get(k) == v == extraction["parsed_steps"].get(k) for k, v in parsed.items()), "Reparsed steps differ")
            process = reader.json(stage + "process_execution.json")
            require(process["returncode"] == 0 and process["stdout"] == validation["steps"]["source"], "Solver process proof differs")
    sampled = reader.json(prefix + "sample_execution.json")
    manifest = reader.json(prefix + "sequence_manifest.json")
    require(sampled["status"] == "COMPLETE" and manifest["status"] == "PASS" and sampled["source_sha256"].lower()
            == manifest["source_sha256"].lower() == execution["output_sha256"].lower(), "Fields not bound to solved model")
    require(sampled["manifest_sha256"].lower() == sha(reader.original_evidence(prefix + "sequence_manifest.json")), "Sample manifest hash differs")
    require(manifest["shape"] == [101, 3, 97, 161] and same_array(manifest["requested_times"], times)
            and same_array(manifest["actual_times"], times), "Sampling contract differs")
    position = 0
    for batch in manifest["batches"]:
        require(batch["start_index"] == position and batch["stop_index"] - position == batch["count"] > 0, "Batch gap/overlap")
        position = batch["stop_index"]
    require(position == 101 and reader.json(prefix + "analysis.json")["status"] == "PASS", "Incomplete fields or response failure")
    all_run = reader.json("logs/" + name + "_all/execution.json")
    require(all_run["plan_sha256"] == sha(reader.original_evidence("preregistered_plan.json"))
            and all_run["config_sha256"] == spec["config_sha256"], "Run log frozen inputs differ")
    if "publication_recovery_sha256" in sampled:
        check_recovery(reader, prefix, sampled, manifest)
        require(all_run["returncode"] != 0, "Original failed all-stage run was not retained")
        analyze = reader.json("logs/" + name + "_analyze/execution.json")
        require(analyze["returncode"] == 0 and analyze["plan_sha256"] == all_run["plan_sha256"]
                and analyze["config_sha256"] == all_run["config_sha256"], "Recovery final analysis did not pass")
    else:
        require(all_run["returncode"] == 0, "Failed all-stage run has no recorded recovery")
    return parsed


def audit(reader):
    manifest = reader.json("delivery_manifest.json")
    require(manifest.get("schema_version") == 1 and manifest.get("study_id") == STUDY, "Wrong delivery schema")
    paths = set()
    for row in manifest["files"]:
        path = safe(row["path"])
        require(path not in paths and path != FOLDER + "/delivery_manifest.json" and row["storage"] == "git", "Duplicate/self manifest entry")
        paths.add(path)
        raw = reader.read(path)
        require(sha(raw) == row["sha256"] and len(raw) == row["bytes"], "File identity differs: " + path)
        require(PurePosixPath(path).suffix.lower() not in {".mph", ".npz", ".bin", ".zip"}, "Large data artifact in delivery")
    require(required_paths() <= paths, "Missing mandatory evidence: " + str(sorted(required_paths() - paths)))
    recovered = [case for case in CASES if "publication_recovery_sha256" in reader.json("cases/" + case + "/sample_execution.json")]
    require(required_paths(recovered) <= paths, "Missing mandatory publication-recovery evidence")
    exported = reader.json("export_manifest.json")
    source_rows = {row["path"]: row for row in exported["sources"]}
    for transformation in exported.get("text_transforms", []):
        raw = reader.evidence(safe(transformation["output"]))
        require(b"\r" not in raw and sha(raw) == transformation["output_sha256"] and len(raw) == transformation["output_bytes"]
                and transformation["source"] == source_rows[transformation["source"]["path"]], "Wrapper-log transformation identity differs")
        if "raw_companion" in transformation:
            require(FOLDER + "/" + safe(transformation["raw_companion"]) in paths, "Raw JSON companion missing from delivery manifest")
            reader.original_evidence(transformation["output"])
    study = reader.json("preregistered_plan.json")
    freeze = reader.json("freeze_record.json")
    require(study["study_id"] == STUDY and freeze["time_solver_executed"] is False
            and freeze["plan_sha256"] == sha(reader.original_evidence("preregistered_plan.json")), "Freeze binding differs")
    before, after = reader.json("protected_before.json"), reader.json("protected_after.json")
    require(sha(reader.original_evidence("protected_before.json")) == study["historical_protection_sha256"]
            and after["status"] == "PASS" and before["files"] == after["files"], "Historical protection differs")
    identity = reader.json("checkpoint_inspection/source_identity.json")
    require(identity["before"] == identity["after"] == study["checkpoint"]["sha256"] and identity["unchanged"] is True, "Checkpoint inspection changed source")
    registry_raw = reader.original_evidence("reference_registry.json")
    registry = json.loads(registry_raw)
    require(registry["checkpoint"]["sha256"] == study["checkpoint"]["sha256"], "Common registry checkpoint differs")
    indexed = {item["path"]: item for item in registry["reference_files"]}
    for label in ("default_reference", "zero_reference"):
        row = study[label]
        require(row["stage_dir"] + "/execution.json" in indexed and row["fields_dir"] + "/sequence_manifest.json" in indexed,
                "Control path is absent from pinned accepted registry")
    for case in CASES:
        require(reader.json("cases/" + case + "/plan.json")["inputs"]["series_sha256"] == sha(registry_raw), "Accepted reference registry differs")
    cases = {name: check_case(reader, name, study) for name in CASES}
    comparison = reader.json("comparison.json")
    require(comparison["status"] == "PASS" and comparison["common_roi_m"] == [.12, .18, .09, .15]
            and comparison["common_response_window_s"] == [181., 188.] and comparison["response_window_left_open"] is True,
            "Common comparison protocol differs")
    require(set(comparison["cases"]) == {"default", *CASES}, "Comparison missing reference/case")
    check_comparison_binding(reader, comparison, study, registry)
    for suite in ("targeted", "regression"):
        execution = reader.json("tests/" + suite + "_execution.json")
        require(execution.get("returncode", execution.get("exit_code")) == 0, "Test process failed")
        junit = ET.fromstring(reader.evidence("tests/" + suite + "_junit.xml"))
        require(not junit.findall(".//failure") and not junit.findall(".//error") and junit.findall(".//testcase"), "JUnit has failures or no tests")
    return {"status": "PASS", "git_ref": reader.ref or "working_tree", "file_count": len(paths), "cases": cases,
            "scope": "Lightweight file/provenance/parameter/time-source/step-log/test evidence checks; no full native-vector or field-value recomputation",
            "comsol_started": False, "local_large_files_required": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", help="Fixed commit/ref or INDEX; omit for working files")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    require(not output.exists() and not output.is_symlink() and not output.is_relative_to(root / "docs/evidence"), "Audit output must be a new file outside evidence")
    result = audit(Reader(root, args.git_ref))
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
