"""Fault-injection checks for the lightweight delivery; no COMSOL calls."""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import math
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/m3_pulse_direction_duration_20261109"


def _module(name):
    spec = importlib.util.spec_from_file_location("pulse_delivery_" + name, EVIDENCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = _module("export_evidence")
audit = _module("audit_delivery")


def test_run_log_export_ignores_test_raw_directories_but_keeps_failed_run(tmp_path):
    logs = tmp_path / "logs"
    names = ("reverse_all", "reverse_analyze", "short_all", "long_all")
    for name in names:
        directory = logs / name
        directory.mkdir(parents=True)
        code = 1 if name == "reverse_all" else 0
        (directory / "execution.json").write_bytes(export.encode({"returncode": code}))
        (directory / "stdout.txt").write_bytes(b"run output\r\n")
        (directory / "stderr.txt").write_bytes(b"")
    unrelated = logs / "regression"
    unrelated.mkdir()
    (unrelated / "stdout.raw").write_bytes(b"pytest output\r\n")
    exporter = export.Exporter(tmp_path)
    export.copy_run_logs(exporter, logs)
    assert len(exporter.outputs) == 12
    assert {Path(name).parts[1] for name in exporter.outputs} == set(names)
    assert json.loads(exporter.outputs["logs/reverse_all/execution.json"])["returncode"] == 1
    assert exporter.outputs["logs/reverse_analyze/stdout.txt"] == b"run output\n"


def _step_log(count=400):
    return "LOCAL_PULSE_TIME_START\nStep Time Stepsize\n- 180 - out\n" + "".join(
        f"{i} {180 + .05 * i:.8f} 0.05\n" for i in range(1, count + 1)) + "LOCAL_PULSE_TIME_FINISHED\n"


def test_compaction_keeps_actual_force_times_and_labels_every_omission(tmp_path):
    payload = {"actual_force_evaluation": {"actual_times": list(range(101)), "values": [[.5] * 101] * 3},
               "native_vectors": {"seed": {"base64": base64.b64encode(b"native state").decode()}},
               "dof_mapping": list(range(150)), "time_solver_log": "native solver text"}
    path = tmp_path / "readback.json"
    path.write_bytes(export.encode(payload))
    exporter = export.Exporter(tmp_path)
    exporter.summary(path, "summary.json")
    wrapped = json.loads(exporter.outputs["summary.json"])
    result = audit.summary(wrapped)
    assert result["actual_force_evaluation"] == payload["actual_force_evaluation"]
    assert wrapped["source"]["sha256"] == export.sha(path.read_bytes())
    assert wrapped["omitted_fields_count"] == 3
    wrapped["omitted_fields_count"] = 2
    with pytest.raises(ValueError, match="Omission inventory"):
        audit.summary(wrapped)


def test_portable_step_parser_matches_formal_parser():
    parsed = audit.parse_steps(_step_log())
    formal = export.parse_internal_steps(_step_log())
    assert all(formal[key] == value for key, value in parsed.items())
    assert parsed["accepted_step_count"] == 400


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("1 180.05000000", "2 180.05000000"),
    lambda text: text.replace("0.05\n", "0.06\n", 1),
    lambda text: text.replace("1 180.05000000", "1 nan"),
    lambda text: text.replace("400 200.00000000 0.05\n", ""),
    lambda text: text.replace("Step Time Stepsize", "solver getter truncated"),
])
def test_portable_step_parser_rejects_incomplete_or_invalid_records(mutation):
    with pytest.raises(ValueError):
        audit.parse_steps(mutation(_step_log()))


def test_log_extraction_binds_raw_stdout_and_removes_only_final_payload(tmp_path):
    stage = tmp_path / "solve"
    stage.mkdir()
    raw = (_step_log().replace("\n", "\r\n") + '<<<JSON_START>>>\n{"large":"omitted"}\n<<<JSON_END>>>').encode()
    path = stage / "process_001_stdout.log"
    path.write_bytes(raw)
    (stage / "process_001_execution.json").write_bytes(export.encode({"returncode": 0, "stdout": path.name}))
    validation = {"steps": {**export.parse_internal_steps(_step_log()), "source": path.name, "source_bytes_sha256": export.sha(raw)}}
    plan = {"analysis_plan": {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}}
    exporter = export.Exporter(tmp_path)
    exporter.log(stage, "solve", validation, plan)
    text = exporter.outputs["solve/solver_stdout.txt"]
    assert b"\r" not in text and b"<<<JSON_START>>>" not in text
    extraction = json.loads(exporter.outputs["solve/solver_log_extraction.json"])
    assert extraction["source"]["sha256"] == export.sha(raw)
    assert extraction["output_sha256"] == export.sha(text)
    path.write_bytes(raw + b"changed")
    with pytest.raises(ValueError, match="log source changed"):
        export.Exporter(tmp_path).log(stage, "solve", validation, plan)


def _source(duration=2., direction=-1.):
    times = [180 + .2 * i for i in range(101)]
    pulse = {"on_s": 181., "off_s": 181. + duration}
    gate = [math.sin(math.pi * (t - 181.) / duration) ** 2 if 181 < t < 181 + duration else 0. for t in times]
    rows = [[0.] * 101, [.003 * direction * value for value in gate], [0.] * 101]
    integral = {"dataset_tag": "dset1", "units": ["N/m"] * 3,
                "values_by_expression": rows, "force_per_depth_by_component": copy.deepcopy(rows)}
    response = {"dataset_tag": "dset1", "solution_tag": "sol1", "actual_times": times,
                "actual_force_evaluation": {"dataset_tag": "dset1", "solution_tag": "sol1", "actual_times": times, "integral": integral}}
    spec = {"config": {"pulse": pulse}, "source_reference": {"direction": [0., direction], "peak_force_per_depth_N_m": .003}}
    validation = {"actual_saved_time_source": {"passed": True, "impulse_vector_Ns_per_m": [0., .003 * direction * duration / 2]},
                  "prescribed_source_diagnostics": {"passed": True}}
    return response, spec, validation


@pytest.mark.parametrize("duration,direction", [(2., -1.), (1.2, 1.), (2., 1.), (3.2, 1.)])
def test_actual_source_table_checks_reverse_and_duration_impulses(duration, direction):
    audit.check_source(*_source(duration, direction))


@pytest.mark.parametrize("fault", ["unit", "time", "source_sign", "reported_impulse", "dataset"])
def test_source_audit_rejects_wrong_actual_evidence(fault):
    response, spec, validation = _source()
    actual = response["actual_force_evaluation"]
    if fault == "unit":
        actual["integral"]["units"] = ["N"] * 3
    elif fault == "time":
        actual["actual_times"] = actual["actual_times"][:-1]
    elif fault == "source_sign":
        actual["integral"]["values_by_expression"][1][10] *= -1
        actual["integral"]["force_per_depth_by_component"][1][10] *= -1
    elif fault == "reported_impulse":
        validation["actual_saved_time_source"]["impulse_vector_Ns_per_m"][1] = .003
    else:
        actual["dataset_tag"] = "other"
    with pytest.raises(ValueError):
        audit.check_source(response, spec, validation)


def test_publication_preflight_never_overwrites_existing_evidence(tmp_path):
    target = tmp_path / "output"
    target.mkdir()
    (target / "existing.json").write_bytes(b"history")
    exporter = export.Exporter(tmp_path)
    exporter.add("new.json", b"{}")
    exporter.add("existing.json", b"{}")
    with pytest.raises(ValueError, match="replace existing"):
        exporter.publish(target)
    assert (target / "existing.json").read_bytes() == b"history"
    assert not (target / "new.json").exists()


def test_wrapper_log_lf_conversion_retains_raw_source_identity(tmp_path):
    path = tmp_path / "stdout.txt"
    raw = "已完成\r\nsecond\rthird\n".encode()
    path.write_bytes(raw)
    exporter = export.Exporter(tmp_path)
    exporter.text_copy(path, "logs/stdout.txt")
    assert exporter.outputs["logs/stdout.txt"] == "已完成\nsecond\nthird\n".encode()
    transform = exporter.transforms[0]
    assert transform["source"]["sha256"] == export.sha(raw)
    assert transform["source"]["bytes"] == len(raw)
    assert transform["output_sha256"] == export.sha(exporter.outputs["logs/stdout.txt"])


def _git_crlf_manifest(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.autocrlf", "true"], check=True)
    (tmp_path / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    original = b'{\r\n  "status": "PASS",\r\n  "shape": [101, 3, 97, 161]\r\n}\r\n'
    source = tmp_path / "native" / "sequence_manifest.json"
    source.parent.mkdir()
    source.write_bytes(original)
    exporter = export.Exporter(tmp_path)
    name = "cases/reverse/sequence_manifest.json"
    exporter.copy(source, name)
    destination = tmp_path / audit.FOLDER
    exporter.publish(destination)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitattributes", audit.FOLDER], check=True)
    return name, original, destination


def test_crlf_manifest_original_hash_survives_real_git_staging(tmp_path):
    name, original, _ = _git_crlf_manifest(tmp_path)
    expected_execution_hash = audit.sha(original)
    reader = audit.Reader(tmp_path, "INDEX")
    assert reader.evidence(name) == original.replace(b"\r\n", b"\n")
    assert reader.evidence(name + ".raw") == original
    assert audit.sha(reader.evidence(name)) != expected_execution_hash
    assert audit.sha(reader.original_evidence(name)) == expected_execution_hash
    assert reader.json(name)["shape"] == [101, 3, 97, 161]
    assert audit.Reader(tmp_path).original_evidence(name) == original


@pytest.mark.parametrize("fault", ["lf_view", "raw_companion", "self_consistent_false_view"])
def test_staged_crlf_manifest_rejects_tampered_view_or_original(tmp_path, fault):
    name, original, destination = _git_crlf_manifest(tmp_path)
    if fault == "raw_companion":
        (destination / (name + ".raw")).write_bytes(original.replace(b"161", b"162"))
    else:
        altered = original.replace(b"\r\n", b"\n").replace(b"161", b"162")
        (destination / name).write_bytes(altered)
        if fault == "self_consistent_false_view":
            path = destination / "export_manifest.json"
            manifest = json.loads(path.read_bytes())
            manifest["text_transforms"][0]["output_sha256"] = audit.sha(altered)
            manifest["text_transforms"][0]["output_bytes"] = len(altered)
            path.write_bytes(export.encode(manifest))
    subprocess.run(["git", "-C", str(tmp_path), "add", audit.FOLDER], check=True)
    with pytest.raises(ValueError, match="Original/LF"):
        audit.Reader(tmp_path, "INDEX").original_evidence(name)


def test_lf_frozen_json_is_not_rewritten_or_given_a_raw_companion(tmp_path):
    original = b'{"freeze":true}\n'
    path = tmp_path / "preregistered_plan.json"
    path.write_bytes(original)
    exporter = export.Exporter(tmp_path)
    exporter.copy(path, path.name)
    assert exporter.outputs == {path.name: original}
    assert exporter.original_output(path.name) == original
    assert exporter.transforms == []


def test_reader_uses_actual_staged_git_bytes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "evidence.txt"
    path.write_bytes(b"recorded\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "evidence.txt"], check=True)
    path.write_bytes(b"changed working file\n")
    assert audit.Reader(tmp_path, "INDEX").read("evidence.txt") == b"recorded\n"
    assert audit.Reader(tmp_path).read("evidence.txt") == b"changed working file\n"


@pytest.mark.parametrize("path", ["../secret", "/absolute", "D:/absolute", "dir\\file", "a/../b"])
def test_delivery_rejects_path_escape(path):
    with pytest.raises(ValueError, match="Unsafe"):
        audit.safe(path)


def test_empty_self_consistent_manifest_cannot_pass(tmp_path):
    directory = tmp_path / audit.FOLDER
    directory.mkdir(parents=True)
    (directory / "delivery_manifest.json").write_text(json.dumps({"schema_version": 1, "study_id": audit.STUDY, "files": []}))
    with pytest.raises(ValueError, match="Missing mandatory evidence"):
        audit.audit(audit.Reader(tmp_path))


def _recovery_fixture():
    data, prefix, model = {}, "cases/reverse/", "a" * 64
    def put(name, value):
        data[name] = export.encode(value)
        return audit.sha(data[name])
    class Reader:
        def evidence(self, name):
            return data[name]
        def original_evidence(self, name):
            return self.evidence(name)
        def json(self, name):
            return json.loads(data[name])
    script = b"# Original one-off publication recovery source\n"
    data["recovery_source.py"] = script
    stage = prefix + "recovery/"
    failure = {"status": "FAIL", "error": "[WinError 5] denied", "traceback": "os.rename(stage, output_dir)", "source_sha256_after": model}
    failure_sha = put(stage + "failure.json", failure)
    stage_failure = {"status": "FAIL", "source_unchanged": True, "source_sha256_before": model.upper(), "source_sha256_after": model.upper()}
    stage_sha = put(stage + "stage_failure.json", stage_failure)
    manifest = {"batches": [{"path": "batch_0000", "npz_sha256": "b" * 64}]}
    manifest_sha = put(prefix + "sequence_manifest.json", manifest)
    files = [{"path": "sequence_manifest.json", "sha256": manifest_sha}, {"path": "failure.json", "sha256": stage_sha},
             {"path": "batch_0000/field_sample.npz", "sha256": "b" * 64}]
    admission = {"status": "ADMITTED", "source_model_sha256": model, "original_failure_sha256": failure_sha,
                 "script_sha256": audit.sha(script), "staged_files": files}
    admission_sha = put(stage + "publication_retry_admission.json", admission)
    recovery = {"status": "PASS", "time_solver_executed": False, "export_repeated": False, "source_model_unchanged": True,
                "all_file_bytes_unchanged": True, "array_fields_rechecked": ["values", "x", "y", "times", "mask", "invalid_mask", "cylinder_mask"],
                "admission_sha256": admission_sha, "original_failure_sha256": failure_sha, "original_stage_failure_sha256": stage_sha,
                "script_sha256": audit.sha(script), "staged_files": files}
    recovery_sha = put(stage + "publication_recovery.json", recovery)
    sampled = {"source_sha256": model, "publication_recovery_sha256": recovery_sha}
    return Reader(), data, prefix, sampled, manifest, recovery


def test_publication_recovery_retains_failure_and_native_source_identity():
    reader, _, prefix, sampled, manifest, _ = _recovery_fixture()
    audit.check_recovery(reader, prefix, sampled, manifest)


@pytest.mark.parametrize("fault", ["new_solver", "files_changed", "lost_array_check", "script_changed", "failure_changed", "wrong_model"])
def test_publication_recovery_rejects_false_success(fault):
    reader, data, prefix, sampled, manifest, recovery = _recovery_fixture()
    if fault == "new_solver":
        recovery["time_solver_executed"] = True
    elif fault == "files_changed":
        recovery["all_file_bytes_unchanged"] = False
    elif fault == "lost_array_check":
        recovery["array_fields_rechecked"] = ["values"]
    elif fault == "script_changed":
        data["recovery_source.py"] = b"changed recovery code"
    elif fault == "failure_changed":
        data[prefix + "recovery/failure.json"] = export.encode({"status": "PASS"})
    else:
        sampled["source_sha256"] = "c" * 64
    path = prefix + "recovery/publication_recovery.json"
    data[path] = export.encode(recovery)
    sampled["publication_recovery_sha256"] = audit.sha(data[path])
    with pytest.raises((ValueError, KeyError)):
        audit.check_recovery(reader, prefix, sampled, manifest)


def _comparison_fixture():
    data, pins, details, cases, references = {}, {}, {}, {}, []
    def put(name, value):
        data[name] = export.encode(value)
        return audit.sha(data[name])
    def pin(relative, digest):
        pins["D:\\data\\" + relative.replace("/", "\\")] = digest
    class Reader:
        def evidence(self, name):
            return data[name]
        def original_evidence(self, name):
            return self.evidence(name)
        def json(self, name):
            return json.loads(data[name])
    study = {"study_id": audit.STUDY, "cases": cases,
             "default_reference": {"stage_dir": "runs/accepted/pulse"}, "zero_reference": {"stage_dir": "runs/accepted/zero"}}
    for name, stage in (("default", "pulse"), ("zero", "zero")):
        model, readback = audit.sha(name.encode()), audit.sha((name + "raw").encode())
        details[name] = {"reused": True, "model_sha256": model, "readback_sha256": readback}
        for filename, digest in ((stage + ".mph", model), ("readback.json", readback)):
            path = "runs/accepted/" + stage + "/" + filename
            references.append({"path": path, "sha256": digest})
            pin(path, digest)
    for index, name in enumerate(audit.CASES):
        prefix, run = "cases/" + name + "/", "runs/" + name
        config = {"event_id": name}
        config_sha = put(prefix + "config.input.json", config)
        cases[name] = {"config": config, "config_path": "configs/" + name + ".json", "config_sha256": config_sha, "run_dir": run}
        pin(cases[name]["config_path"], config_sha)
        model = audit.sha((name + "model").encode())
        validation = {"steps": {"accepted_step_count": 450 + index}, "actual_saved_time_source": {"impulse": index + 1.},
                      "prescribed_source_diagnostics": {"passed": True}}
        actual = {"direction_deg": -90 if name == "reverse" else 90, "duration": index + 1.}
        for mode in ("inspect", "solve"):
            for filename, content in (("execution.json", {"output_sha256": model}), ("validation.json", validation)):
                digest = put(prefix + mode + "/" + filename, content)
                pin(run + "/" + mode + "/" + filename, digest)
            raw_sha = audit.sha((name + mode + "raw").encode())
            put(prefix + mode + "/readback_summary.json", {"source": {"sha256": raw_sha}, "content": {"actual_pulse": actual}})
            pin(run + "/" + mode + "/readback.json", raw_sha)
        for filename in ("plan.json", "event.properties", "analysis.json", "sample_execution.json", "sequence_manifest.json"):
            put(prefix + filename, {"status": "PASS"})
        for relative in ("plan.json", "config.input.json", "event.properties", "analysis.json"):
            pin(run + "/" + relative, audit.sha(data[prefix + relative]))
        pin(run + "/sample_solve/execution.json", audit.sha(data[prefix + "sample_execution.json"]))
        pin(run + "/sample_solve/fields/sequence_manifest.json", audit.sha(data[prefix + "sequence_manifest.json"]))
        details[name] = {"reused": False, "config": config, "actual_pulse": actual, "model_sha256": model,
                         "individual_status": "PASS", **copy.deepcopy(validation)}
    plan_sha = put("preregistered_plan.json", study)
    comparison = {"study_id": audit.STUDY, "plan_sha256": plan_sha, "case_evidence": details, "input_file_sha256": pins}
    return Reader(), comparison, study, {"reference_files": references}


def test_comparison_binds_actual_cases_and_pinned_old_references_portably():
    audit.check_comparison_binding(*_comparison_fixture())


@pytest.mark.parametrize("fault", ["plan", "swapped_case", "actual_pulse", "steps", "source_impulse", "model", "default_model", "zero_readback", "input_hash", "missing_input", "ambiguous_input"])
def test_comparison_cannot_mix_evidence_from_another_run(fault):
    reader, comparison, study, registry = _comparison_fixture()
    detail = comparison["case_evidence"]["reverse"]
    pins = comparison["input_file_sha256"]
    target = next(path for path in pins if path.endswith("reverse\\solve\\readback.json"))
    if fault == "plan":
        comparison["plan_sha256"] = "0" * 64
    elif fault == "swapped_case":
        comparison["case_evidence"]["reverse"] = copy.deepcopy(comparison["case_evidence"]["short"])
    elif fault == "actual_pulse":
        detail["actual_pulse"] = {"direction_deg": 90}
    elif fault == "steps":
        detail["steps"]["accepted_step_count"] += 1
    elif fault == "source_impulse":
        detail["actual_saved_time_source"]["impulse"] += 1
    elif fault == "model":
        detail["model_sha256"] = "0" * 64
    elif fault == "default_model":
        comparison["case_evidence"]["default"]["model_sha256"] = "0" * 64
    elif fault == "zero_readback":
        comparison["case_evidence"]["zero"]["readback_sha256"] = "0" * 64
    elif fault == "input_hash":
        pins[target] = "0" * 64
    elif fault == "missing_input":
        del pins[target]
    else:
        pins[target.replace("D:", "X:")] = pins[target]
    with pytest.raises(ValueError, match="Comparison"):
        audit.check_comparison_binding(reader, comparison, study, registry)
