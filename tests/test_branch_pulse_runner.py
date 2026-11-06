"""Offline execution-wrapper checks; these tests never launch COMSOL."""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/run_m3_branch_pulse.py"
    spec = importlib.util.spec_from_file_location("branch_pulse_runner_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.mph"
    path.write_bytes(b"offline fake model; not COMSOL")
    return path


def _vector(values=(1.0, 2.0)):
    raw = np.array(values, dtype="<f8").tobytes()
    return {
        "dtype": "float64", "byte_order": "little", "count": 2, "time": 180.0,
        "solnum": 1, "solution_tag": "test_seed", "solution_size": [2, 1],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode("ascii"),
    }


def _success(source):
    def respond(args, **kwargs):
        target = Path(args[2])
        target.write_bytes(b"offline fake saved branch")
        return {
            "status": "SUCCESS", "operation": "run_pulse_branch",
            "source_model": str(source), "output_model": str(target),
            "native_vectors": {"seed": _vector(), "input": _vector()},
            "native_derivatives": {"seed": _vector((.25, -.5)), "input": _vector((.25, -.5))},
            "time_solver_log": "offline fixture",
        }
    return respond


def _run(runner, stage, source):
    target = stage / "zero.mph"
    return runner._execute(
        stage, ["run-pulse-branch", str(source), str(target), "0"],
        source, target, "run_pulse_branch",
    )


def _reusable(runner, tmp_path, source, wrong_hash=False):
    checkpoint = tmp_path / "checkpoint.mph"
    checkpoint.write_bytes(b"offline checkpoint")
    readback = tmp_path / "prepare_response.json"
    readback.write_text(json.dumps({
        "status": "SUCCESS", "operation": "prepare_branch_state",
        "source_model": str(source), "output_model": str(checkpoint),
        "source_sha256_before": runner._hash(source), "source_sha256_after": runner._hash(source),
        "output_sha256": "0" * 64 if wrong_hash else runner._hash(checkpoint),
        "native_vectors": {"source": _vector(), "seed": _vector(), "initialized": _vector()},
        "native_derivatives": {"source": _vector((.25, -.5)), "seed": _vector((.25, -.5))},
    }), encoding="utf-8")
    output = tmp_path / "run"
    output.mkdir()
    return output, argparse.Namespace(checkpoint=checkpoint, readback=readback), {
        "source_sha256": runner._hash(source),
    }


def test_runner_persists_vectors_readback_and_model_hashes(runner, tmp_path, source):
    stage = tmp_path / "zero"
    with patch.object(runner.bridge, "run_command", side_effect=_success(source)) as fake:
        response, target, record = _run(runner, stage, source)
    assert fake.call_count == 1
    assert record["source_sha256_before"] == record["source_sha256_after"] == runner._hash(source)
    assert record["output_sha256"] == runner._hash(target)
    assert record["implementation"]["all_source_files_unchanged"] is True
    assert {Path(path).name for path in record["implementation"]["source_files"]} == {
        "run_m3_branch_pulse.py", "bridge.py", "ComsolBridge.java", "ComsolStandalone.java",
        "errors.py", "file_transactions.py",
        "__init__.py", "branch_execution.py", "process_recording.py", "evidence_io.py", "solver_steps.py",
    }
    assert runner._hash(stage / "native_seed.bin") == response["native_vectors"]["seed"]["sha256"]
    assert runner._hash(stage / "native_derivative_seed.bin") == response["native_derivatives"]["seed"]["sha256"]
    assert (stage / "native_derivative_vectors.json").is_file()
    assert runner._completed(stage)[1] == target


def test_runner_rejects_changed_completed_model(runner, tmp_path, source):
    stage = tmp_path / "zero"
    with patch.object(runner.bridge, "run_command", side_effect=_success(source)):
        _run(runner, stage, source)
    (stage / "zero.mph").write_bytes(b"corrupted offline fixture")
    with pytest.raises(runner.bridge.ModelValidationError, match="Completed model bytes changed"):
        runner._completed(stage)


def test_runner_preserves_failure_and_refuses_overwrite(runner, tmp_path, source):
    stage = tmp_path / "zero"
    with patch.object(runner.bridge, "run_command", side_effect=runner.bridge.ModelValidationError("injected")):
        with pytest.raises(runner.bridge.ModelValidationError, match="injected"):
            _run(runner, stage, source)
    failure = (stage / "failure.json").read_bytes()
    failed = json.loads(failure)
    assert failed["implementation"]["all_source_files_unchanged"] is True
    assert len(failed["implementation"]["source_files"]) == 11
    with patch.object(runner.bridge, "run_command") as fake:
        with pytest.raises(FileExistsError):
            _run(runner, stage, source)
    fake.assert_not_called()
    assert (stage / "failure.json").read_bytes() == failure
    assert (stage / "processes.json").exists()


def test_runner_reuses_verified_checkpoint_without_bridge(runner, tmp_path, source):
    output, args, plan = _reusable(runner, tmp_path, source)
    with patch.object(runner.bridge, "run_command") as fake:
        result = runner._prepare(output, source, args, plan)
    fake.assert_not_called()
    assert result[2]["reused"] is True
    assert (output / "prepare/readback.json").read_bytes() == args.readback.read_bytes()
    expected = json.loads(args.readback.read_bytes())["native_derivatives"]["source"]["sha256"]
    assert runner._hash(output / "prepare/native_derivative_source.bin") == expected


def test_runner_rejects_derivative_hash_mismatch_and_retains_readback(runner, tmp_path, source):
    stage = tmp_path / "zero"
    def corrupt(args, **kwargs):
        result = _success(source)(args)
        result["native_derivatives"]["seed"]["sha256"] = "0" * 64
        return result
    with patch.object(runner.bridge, "run_command", side_effect=corrupt):
        with pytest.raises(runner.bridge.ModelValidationError, match="vector bytes"):
            _run(runner, stage, source)
    assert (stage / "failure.json").is_file()
    assert json.loads((stage / "readback.json").read_bytes())["native_derivatives"]["seed"]["sha256"] == "0" * 64
    assert not (stage / "execution.json").exists()
    assert not (stage / "native_derivative_seed.bin").exists()


def test_runner_rejects_checkpoint_hash_before_stage_creation(runner, tmp_path, source):
    output, args, plan = _reusable(runner, tmp_path, source, wrong_hash=True)
    with patch.object(runner.bridge, "run_command") as fake:
        with pytest.raises(runner.bridge.ModelValidationError, match="output_sha256"):
            runner._prepare(output, source, args, plan)
    fake.assert_not_called()
    assert not (output / "prepare").exists()


@pytest.mark.parametrize("name", ["zero", "pulse"])
def test_runner_passes_explicit_frozen_protocol_to_each_branch(runner, tmp_path, source, name):
    protocol = runner._read(runner.RESTART_PROTOCOL_PATH)
    plan = {"restart_protocol": protocol,
            "pulse": {"zero_branch_amplitude_m_s2": 0., "acceleration_amplitude_m_s2": .005}}
    with patch.object(runner.bridge, "run_command", side_effect=_success(source)) as fake:
        runner._solve(tmp_path, source, name, plan)
    assert fake.call_args.args[0][4:] == ["native-restart-consistent-off-v2"]
    with patch.object(runner.bridge, "run_command") as unused:
        runner._solve(tmp_path, source, name, plan)
    unused.assert_not_called()


def test_runner_rejects_reusing_old_solve_without_explicit_protocol(runner, tmp_path, source):
    with patch.object(runner.bridge, "run_command", side_effect=_success(source)):
        _run(runner, tmp_path / "zero", source)
    plan = {"restart_protocol": runner._read(runner.RESTART_PROTOCOL_PATH)}
    with pytest.raises(runner.bridge.ModelValidationError, match="explicit restart protocol"):
        runner._solve(tmp_path, source, "zero", plan)


def _entrypoint_inputs(runner, tmp_path, source, monkeypatch):
    plan_path = tmp_path / "physical_plan.json"
    runner._write(plan_path, {"source_model": str(source), "source_sha256": runner._hash(source)})
    protocol_path = tmp_path / "restart_protocol_v2.json"
    runner._write(protocol_path, {"id": "native-restart-consistent-off-v2",
                                 "physical_plan_sha256": runner._hash(plan_path)})
    for name, value in (("PLAN_PATH", plan_path), ("PLAN_SHA", runner._hash(plan_path)),
                        ("RESTART_PROTOCOL_PATH", protocol_path),
                        ("RESTART_PROTOCOL_SHA", runner._hash(protocol_path))):
        monkeypatch.setattr(runner, name, value)
    output = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", ["runner", "--output", str(output), "--stage", "prepare"])
    return output, plan_path, protocol_path


def test_runner_entrypoint_freezes_both_files_and_attaches_protocol(runner, tmp_path, source, monkeypatch):
    output, plan_path, protocol_path = _entrypoint_inputs(runner, tmp_path, source, monkeypatch)
    with patch.object(runner, "_prepare", return_value=({}, source, {})) as prepare:
        assert runner.main() == 0
    attached_plan = prepare.call_args.args[3]
    assert attached_plan["restart_protocol"] == runner._read(protocol_path)
    assert "restart_protocol" not in runner._read(plan_path)
    assert (output / "preregistered_plan.json").read_bytes() == plan_path.read_bytes()
    assert (output / protocol_path.name).read_bytes() == protocol_path.read_bytes()
    marker = runner._read(output / "run_manifest.json")
    assert marker["restart_protocol_sha256"] == runner._hash(protocol_path)
    assert marker["restart_protocol_id"] == "native-restart-consistent-off-v2"


@pytest.mark.parametrize("fault", ["missing_marker", "changed_run_copy", "changed_protocol", "wrong_physical_binding"])
def test_runner_entrypoint_rejects_unbound_protocol_before_external_work(runner, tmp_path, source, monkeypatch, fault):
    output, _, protocol_path = _entrypoint_inputs(runner, tmp_path, source, monkeypatch)
    with patch.object(runner, "_prepare", return_value=({}, source, {})):
        assert runner.main() == 0
    if fault == "missing_marker":
        marker_path = output / "run_manifest.json"
        marker = runner._read(marker_path)
        del marker["restart_protocol_sha256"]
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    elif fault == "changed_run_copy":
        (output / protocol_path.name).write_bytes(b"modified protocol copy")
    elif fault == "changed_protocol":
        protocol_path.write_bytes(b"modified frozen protocol")
    else:
        protocol = runner._read(protocol_path)
        protocol["physical_plan_sha256"] = "0" * 64
        protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
        monkeypatch.setattr(runner, "RESTART_PROTOCOL_SHA", runner._hash(protocol_path))
    with patch.object(runner, "_prepare") as prepare:
        with pytest.raises(runner.bridge.ModelValidationError, match="[Pp]rotocol"):
            runner.main()
    prepare.assert_not_called()


def test_runner_rejects_vector_bytes_hash_mismatch(runner, tmp_path):
    bad = _vector()
    bad["sha256"] = "0" * 64
    with pytest.raises(runner.bridge.ModelValidationError, match="vector bytes"):
        runner._vectors(tmp_path, {"seed": bad})
    assert not (tmp_path / "native_seed.bin").exists()


def test_runner_preserves_raw_process_bytes_before_decoding(runner, tmp_path):
    with runner._capture_processes(tmp_path) as record_process:
        result = record_process(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'\\xff\\n');sys.stderr.buffer.write(b'\\xfe')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    assert (tmp_path / "process_000_stdout.log").read_bytes() == b"\xff\n"
    assert (tmp_path / "process_000_stderr.log").read_bytes() == b"\xfe"
    assert result.stdout == "\ufffd\n"
    assert result.returncode == 0


def test_analysis_rejects_valid_sequence_from_wrong_branch_model(runner, tmp_path, source):
    """A valid NPZ/hash chain cannot replace the expected branch's provenance."""
    from hydro_disturb_bench import branch_pulse

    fixture_path = Path(__file__).with_name("test_field_sequence.py")
    spec = importlib.util.spec_from_file_location("sequence_fixtures_for_runner", fixture_path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    output, args, plan = _reusable(runner, tmp_path, source)
    _, checkpoint, _ = runner._prepare(output, source, args, plan)
    plan["source_model"] = str(source)
    plan["restart_protocol"] = runner._read(runner.RESTART_PROTOCOL_PATH)
    models = {}
    for name in ("zero", "pulse"):
        stage = output / name
        target = stage / f"{name}.mph"
        with patch.object(runner.bridge, "run_command", side_effect=_success(checkpoint)):
            _, models[name], _ = runner._execute(
                stage, ["run-pulse-branch", str(checkpoint), str(target), "0", plan["restart_protocol"]["id"]],
                checkpoint, target, "run_pulse_branch",
            )
    foreign_model = tmp_path / "unrelated.mph"
    foreign_model.write_bytes(b"different valid sampled model")
    baseline_path = tmp_path / "baseline_fields"
    for model, directory in (
        (source, baseline_path),
        (foreign_model, output / "sample_zero/fields"),
        (models["pulse"], output / "sample_pulse/fields"),
    ):
        external = fixtures.AnalyticComsol(model)
        with patch.object(runner.bridge, "run_command", side_effect=external):
            runner.export_full_field_sequence(
                model, directory, start=180.0, stop=180.4, step=0.2,
                grid_shape=fixtures.SHAPE, batch_size=32,
            )
        # The normal production loader accepts this complete sample independently.
        assert runner.load_full_field_sequence(directory)["values"].shape[0] == 3
    with (
        patch.object(runner, "BASELINE", baseline_path),
        patch.object(branch_pulse, "validate_native_branch_evidence", create=True) as native,
        patch.object(branch_pulse, "compare_branch_fields") as compare,
    ):
        with pytest.raises(runner.bridge.ModelValidationError, match="corresponding solved branch"):
            runner._analyze(output, plan)
    native.assert_not_called()
    compare.assert_not_called()
    assert (output / "analysis/failure.json").is_file()
    assert not (output / "analysis/report.json").exists()


def _step_log(first=1):
    lines = ["Step Time Stepsize Res Jac Sol", "0 180 - out 1 1 1"]
    lines.extend(f"{i + first} {180 + (i + 1) * .05:.8f} .05 2 1 1" for i in range(401))
    lines.insert(30, "- 181.2 - out")
    return "\n".join(lines)


@pytest.mark.parametrize("first", [0, 1])
def test_step_gate_accepts_complete_zero_or_one_based_internal_rows(runner, first):
    result = runner._parse_internal_steps(_step_log(first))
    assert result["status"] == "PASS"
    assert result["accepted_step_count"] == 401
    assert result["output_only_rows_excluded"] == 2
    assert result["final_internal_time_s"] == pytest.approx(200.05)


def _with_progress_lines(text):
    lines = text.splitlines()
    for index, progress in ((200, "- 当前进度: 10 % - 矩阵分解"),
                            (100, "- Current Progress: 25 % - Assembly"),
                            (40, "----- Current Progress: 50 % - Solver")):
        lines.insert(index, progress)
    return "\n".join(lines)


def test_step_gate_ignores_real_progress_lines_without_counting_them(runner):
    result = runner._parse_internal_steps(_with_progress_lines(_step_log()))
    assert result["status"] == "PASS"
    assert result["accepted_step_count"] == 401
    assert result["output_only_rows_excluded"] == 2
    assert result["dt_max_s"] == .05


@pytest.mark.parametrize("fault", ["cap", "gap", "incomplete"])
def test_step_gate_progress_lines_do_not_hide_invalid_internal_steps(runner, fault):
    text = _with_progress_lines(_step_log())
    if fault == "cap":
        text = text.replace("2 180.10000000 .05", "2 180.10000000 .1")
    elif fault == "gap":
        text = text.replace("2 180.10000000 .05 2 1 1\n", "")
    else:
        text = "\n".join(text.splitlines()[:-2]) + "\n- Current Progress: 100 % - Complete"
    with pytest.raises(runner.bridge.ModelValidationError):
        runner._parse_internal_steps(text)


@pytest.mark.parametrize("fault", ["missing_header", "missing_start", "gap", "duplicate", "negative_dt", "over_cap", "nan", "truncated", "output_only_end"])
def test_step_gate_rejects_missing_corrupt_or_excessive_internal_steps(runner, fault):
    text = _step_log()
    if fault == "missing_header":
        text = text.replace("Step Time Stepsize Res Jac Sol\n", "")
    elif fault == "missing_start":
        text = text.replace("0 180 - out 1 1 1\n", "")
    elif fault == "gap":
        text = text.replace("2 180.10000000 .05 2 1 1\n", "")
    elif fault == "duplicate":
        text = text.replace("2 180.10000000", "1 180.10000000")
    elif fault == "negative_dt":
        text = text.replace("2 180.10000000 .05", "2 180.10000000 -.01")
    elif fault == "over_cap":
        text = text.replace("2 180.10000000 .05", "2 180.10000000 .0501")
    elif fault == "nan":
        text = text.replace("2 180.10000000 .05", "2 180.10000000 NaN")
    else:
        text = "\n".join(text.splitlines()[:-2])
        if fault == "output_only_end":
            text += "\n- 200 - out"
    with pytest.raises(runner.bridge.ModelValidationError):
        runner._parse_internal_steps(text)


def test_step_gate_uses_real_stdout_when_getter_is_empty(runner, tmp_path):
    path = tmp_path / "process_002_stdout.log"
    path.write_bytes(_step_log().encode("utf-8"))
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": ""}, plan)
    assert result["status"] == "PASS"
    assert result["source"] == path.name
    assert result["source_bytes_sha256"] == runner._hash(path)


def test_step_gate_prefers_complete_stdout_over_truncated_nonempty_getter(runner, tmp_path):
    path = tmp_path / "process_002_stdout.log"
    path.write_bytes(_step_log().encode("utf-8"))
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    tail = "3769 184.14 .05 2 1 1\n3770 184.19 .05 2 1 1"
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": tail}, plan)
    assert result["status"] == "PASS"
    assert result["source"] == path.name
    assert result["source_bytes_sha256"] == runner._hash(path)
    assert result["getter_diagnostic"]["character_count"] == len(tail)
    assert result["getter_diagnostic"]["has_step_table_header"] is False
    assert result["getter_diagnostic"]["truncated_or_invalid"] is True


def test_step_gate_accepts_complete_getter_only_when_stdout_table_is_absent(runner, tmp_path):
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    text = _step_log()
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": text}, plan)
    assert result["status"] == "PASS"
    assert result["source"] == "readback.json:time_solver_log"
    assert result["source_text_utf8_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert result["getter_diagnostic"]["complete_step_table"] is True


@pytest.mark.parametrize("text", [
    "3769 184.14 .05 2 1 1",
    "Step Time Stepsize Res Jac Sol\n3769 184.14 .05 2 1 1",
    "Step Time Stepsize Res Jac Sol\n0 180 - out\n1 200 .05 2 1 1",
    "Step Time Stepsize Res Jac Sol\n0 180 - out\n- 200 - out",
])
def test_step_gate_rejects_truncated_or_pseudo_getter_fallback(runner, tmp_path, text):
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": text}, plan)
    assert result["status"] == "FAIL"
    assert result["source"] == "readback.json:time_solver_log"
    assert result["getter_diagnostic"]["truncated_or_invalid"] is True


def test_step_gate_does_not_hide_incomplete_stdout_with_complete_getter(runner, tmp_path):
    path = tmp_path / "process_002_stdout.log"
    path.write_text("\n".join(_step_log().splitlines()[:-2]), encoding="utf-8")
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": _step_log()}, plan)
    assert result["status"] == "FAIL"
    assert result["source"] == path.name
    assert result["getter_diagnostic"]["complete_step_table"] is True


def test_step_gate_rejects_ambiguous_stdout_even_with_complete_getter(runner, tmp_path):
    for index in (1, 2):
        (tmp_path / f"process_{index:03d}_stdout.log").write_text(_step_log(), encoding="utf-8")
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    result = runner._branch_step_validation(tmp_path, {"time_solver_log": _step_log()}, plan)
    assert result["status"] == "FAIL"
    assert result["error"].endswith("Ambiguous actual solver stdout tables")


def test_step_gate_missing_both_real_sources_fails(runner, tmp_path):
    plan = {"branch_time_s": 180., "stop_s": 200., "solver": {"maxstepbdf_s": .05}}
    result = runner._branch_step_validation(tmp_path, {}, plan)
    assert result["status"] == "FAIL"
    assert result["source"] is None


def test_concurrent_executions_keep_process_logs_isolated(runner, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(3)
    original_run = subprocess.run

    def external(args, *, process_runner):
        source = Path(args[1])
        response = _success(source)(args)
        barrier.wait(timeout=10)
        assert subprocess.run is original_run
        label = source.stem
        process_runner(
            [sys.executable, "-c", "import sys;print(sys.argv[1]);print(sys.argv[1], file=sys.stderr)", label],
            capture_output=True, text=True, encoding="utf-8",
        )
        return response

    monkeypatch.setattr(runner.bridge, "run_command", external)
    sources = [tmp_path / f"source_{index}.mph" for index in range(2)]
    for index, source in enumerate(sources):
        source.write_bytes(f"offline source {index}".encode())
    stages = [tmp_path / f"stage_{index}" for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(_run, runner, stage, source) for stage, source in zip(stages, sources)]
        barrier.wait(timeout=10)
        assert subprocess.run is original_run
        unrelated = subprocess.run([sys.executable, "-c", "print('unrelated process')"], capture_output=True)
        assert unrelated.stdout.strip() == b"unrelated process"
        for future in pending:
            assert future.result(timeout=15)[2]["status"] == "COMPLETE"
    for stage, source in zip(stages, sources):
        for channel in ("stdout", "stderr"):
            assert (stage / f"process_000_{channel}.log").read_bytes().strip() == source.stem.encode()
        assert len(runner._read(stage / "processes.json")) == 1
        assert not (stage / "process_001_stdout.log").exists()
    assert subprocess.run is original_run


def test_typed_bridge_error_retains_raw_streams_and_partial_native_payload(runner, tmp_path, source, monkeypatch):
    binary_dir = tmp_path / "fake_bin"
    binary_dir.mkdir()
    (binary_dir / ("comsolbatch.exe" if sys.platform == "win32" else "comsolbatch")).write_bytes(b"not executed")
    class_file = binary_dir / "ComsolBridge.class"
    class_file.write_bytes(b"not a JVM class; process is injected")
    monkeypatch.setattr(runner.bridge, "find_comsol_root", lambda: binary_dir)
    monkeypatch.setattr(runner.bridge, "get_comsol_bin_dir", lambda root: binary_dir)
    monkeypatch.setattr(runner.bridge, "ensure_bridge_compiled", lambda root, **kwargs: class_file)
    payload = {
        "status": "ERROR", "error_code": "SOLVER_FAILED", "message": "native Time failed",
        "details": "original COMSOL detail", "partial_native_vectors": {"input": _vector()},
        "partial_native_derivatives": {"input": _vector((.25, -.5))},
    }
    stdout = b"\xffnative prefix\r\n<<<JSON_START>>>" + json.dumps(payload).encode() + b"<<<JSON_END>>>\r\n"
    stderr = b"\xfefatal native stderr\r\n"

    def binary_run(command, **kwargs):
        assert not kwargs.get("text") and "encoding" not in kwargs
        return subprocess.CompletedProcess(command, 1, stdout, stderr)

    stage = tmp_path / "failed_native"
    target = stage / "failed.mph"
    with pytest.raises(runner.bridge.SolverFailedError, match="native Time failed") as error:
        runner.execution.execute_branch(
            stage, ["run-pulse-branch", str(source), str(target), "0"], source, target,
            "run_pulse_branch", entrypoint=Path(runner.__file__), process_runner=binary_run,
        )
    assert error.value.details == "original COMSOL detail"
    assert (stage / "process_000_stdout.log").read_bytes() == stdout
    assert (stage / "process_000_stderr.log").read_bytes() == stderr
    assert runner._read(stage / "process_000_payload.json") == payload
    for field, suffix in (("partial_native_vectors", "partial"),
                          ("partial_native_derivatives", "partial_derivative")):
        assert runner._hash(stage / f"process_000_{suffix}_input.bin") == payload[field]["input"]["sha256"]
    assert runner._read(stage / "failure.json")["details"] == "original COMSOL detail"
    assert runner._read(stage / "processes.json")[0]["returncode"] == 1
    assert not (stage / "execution.json").exists()


def test_process_timeout_retains_partial_streams_and_exception(runner, tmp_path):
    error = subprocess.TimeoutExpired(["offline"], 1, output=b"partial\xff", stderr=b"timeout\xfe")

    def expired(command, **kwargs):
        raise error

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        with runner._capture_processes(tmp_path, expired) as record:
            record(["offline"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert caught.value is error
    assert (tmp_path / "process_000_stdout.log").read_bytes() == b"partial\xff"
    assert (tmp_path / "process_000_stderr.log").read_bytes() == b"timeout\xfe"
    assert runner._read(tmp_path / "processes.json")[0]["error"]["type"] == "TimeoutExpired"


def test_invalid_process_payload_is_retained_without_masking_bridge_handling(runner, tmp_path):
    raw = b"<<<JSON_START>>>{invalid JSON<<<JSON_END>>>"
    with runner._capture_processes(
        tmp_path, lambda command, **kwargs: subprocess.CompletedProcess(command, 1, raw, b"original error")
    ) as record:
        result = record(["offline"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert (tmp_path / "process_000_stdout.log").read_bytes() == raw
    assert (tmp_path / "process_000_payload_decode_failure.json").is_file()


def test_sample_service_records_explicit_process_and_binds_sampling_sources(runner, tmp_path, source, monkeypatch):
    fixture_path = Path(__file__).with_name("test_field_sequence.py")
    spec = importlib.util.spec_from_file_location("sequence_fixture_for_recorded_sample", fixture_path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    output = tmp_path / "run"
    output.mkdir()
    with patch.object(runner.bridge, "run_command", side_effect=_success(source)):
        _, model, _ = _run(runner, output / "zero", source)
    binary_dir = tmp_path / "fake_bin"
    binary_dir.mkdir()
    (binary_dir / ("comsolbatch.exe" if sys.platform == "win32" else "comsolbatch")).write_bytes(b"not executed")
    class_file = binary_dir / "ComsolBridge.class"
    class_file.write_bytes(b"not executed")
    monkeypatch.setattr(runner.bridge, "find_comsol_root", lambda: binary_dir)
    monkeypatch.setattr(runner.bridge, "get_comsol_bin_dir", lambda root: binary_dir)
    monkeypatch.setattr(runner.bridge, "ensure_bridge_compiled", lambda root, **kwargs: class_file)
    external = fixtures.AnalyticComsol(model)
    original_streams = []

    def binary_run(command, **kwargs):
        assert command[4] == "export-full-field-sequence-batch"
        assert not kwargs.get("text") and "encoding" not in kwargs
        payload = external(command[4:])
        raw = b"actual sampling prefix\r\n<<<JSON_START>>>" + json.dumps(payload).encode() + b"<<<JSON_END>>>"
        original_streams.append(raw)
        return subprocess.CompletedProcess(command, 0, raw, b"sampling stderr\r\n")

    plan = {"branch_time_s": 180., "stop_s": 180.4, "output_step_s": .2,
            "grid_shape_nx_ny": fixtures.SHAPE}
    runner.execution.sample_branch(output, "zero", plan, entrypoint=Path(runner.__file__), process_runner=binary_run)
    directory = output / "sample_zero"
    assert (directory / "process_000_stdout.log").read_bytes() == original_streams[0]
    assert (directory / "process_000_stderr.log").read_bytes() == b"sampling stderr\r\n"
    record = runner._read(directory / "execution.json")
    names = {Path(path).name for path in record["implementation"]["source_files"]}
    assert {"branch_execution.py", "process_recording.py", "field_sequence.py", "field_sampling.py",
            "run_m3_branch_pulse.py", "bridge.py", "ComsolBridge.java",
            "errors.py", "file_transactions.py"} <= names
    assert record["implementation"]["all_source_files_unchanged"] is True
    assert runner.load_full_field_sequence(directory / "fields")["values"].shape == (3, 3, 9, 17)
    original_log = (directory / "processes.json").read_bytes()
    runner.execution.sample_branch(output, "zero", plan, entrypoint=Path(runner.__file__),
                                   process_runner=lambda *args, **kwargs: pytest.fail("Completed sample must not run again"))
    assert (directory / "processes.json").read_bytes() == original_log
