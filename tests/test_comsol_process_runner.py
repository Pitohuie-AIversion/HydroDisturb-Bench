"""Invocation-local process execution through the real bridge launch paths."""

from __future__ import annotations

import base64
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from hydro_disturb_bench.comsol_java import bridge, run_command


def _json_output(payload):
    return "<<<JSON_START>>>\n" + json.dumps(payload) + "\n<<<JSON_END>>>"


def _success(marker):
    return {"status": "SUCCESS", "operation": "inspect_api", "comsol_version": "6.4",
            "api_specification": {}, "runner_marker": marker}


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Synthetic files only: no installed COMSOL executable is used."""
    root = tmp_path / "COMSOL"
    bin_dir = root / "bin" / "win64"
    bin_dir.mkdir(parents=True)
    for name in ("comsolcompile.exe", "comsolbatch.exe"):
        (bin_dir / name).write_bytes(b"not an executable")
    java_dir = tmp_path / "bridge"
    java_dir.mkdir()
    (java_dir / "ComsolBridge.java").write_text("synthetic bridge source", encoding="utf-8")
    (java_dir / "ComsolStandalone.java").write_text("synthetic launcher", encoding="utf-8")
    monkeypatch.setattr(bridge, "__file__", str(java_dir / "bridge.py"))
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    return root, java_dir


class ProcessDouble:
    def __init__(self, marker, *, startup_failure=False, failure=None):
        self.marker = marker
        self.startup_failure = startup_failure
        self.failure = failure
        self.calls = []

    def __call__(self, command, **kwargs):
        name = Path(command[0]).name
        self.calls.append((name, command, kwargs))
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True and kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        if name == "comsolcompile.exe":
            if self.failure == "compile":
                return subprocess.CompletedProcess(command, 1, "", "synthetic compilation failure")
            Path(command[1]).with_suffix(".class").write_bytes(b"synthetic bytecode")
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "javac.exe":
            return subprocess.CompletedProcess(command, 0, "", "")
        assert name in {"comsolbatch.exe", "java.exe"}
        if name == "comsolbatch.exe" and self.startup_failure:
            return subprocess.CompletedProcess(command, 1,
                "Failed to start COMSOL\nFlNativeException\n", "synthetic startup detail")
        payload = _success(self.marker)
        if self.failure == "solver":
            payload = {"status": "ERROR", "error_code": "SOLVER_FAILED",
                       "message": "synthetic solver error", "details": "original details"}
        elif self.failure == "schema":
            payload.pop("comsol_version")
        prefix = ""
        preference_args = [item for item in command if item.startswith("-Dcs.prefsdir=")]
        if preference_args:
            preference_dir = preference_args[0].split("=", 1)[1]
            prefix = "COMSOL_GRAPHICS_RENDERER_READBACK=sw\nCOMSOL_GRAPHICS_PREFSDIR_READBACK="
            prefix += base64.b64encode(preference_dir.encode("utf-8")).decode("ascii") + "\n"
            payload["operation"] = "export_s1_diagnostics"
        return subprocess.CompletedProcess(command, 0, prefix + _json_output(payload), "")


@pytest.mark.parametrize("fallback", [False, True])
def test_explicit_runner_handles_compile_batch_and_native_startup_fallback(installation, fallback):
    root, _ = installation
    original = subprocess.run
    runner = ProcessDouble("one", startup_failure=fallback)
    result = run_command(["inspect-api"], root, process_runner=runner)
    assert result["runner_marker"] == "one"
    assert result["launch_mode"] == ("standalone_java_api" if fallback else "comsolbatch")
    expected = ["comsolcompile.exe", "comsolbatch.exe"]
    if fallback:
        expected += ["javac.exe", "java.exe"]
        assert result["batch_startup_failure"]["stderr"] == "synthetic startup detail"
        assert not Path(runner.calls[-1][2]["cwd"]).exists()
    assert [item[0] for item in runner.calls] == expected
    assert subprocess.run is original


def test_explicit_runner_handles_direct_graphics_launch(installation, tmp_path, monkeypatch):
    root, _ = installation
    seed_dir = tmp_path / "preferences"
    seed_dir.mkdir()
    for name in ("comsol.prefs", "comsolserver.prefs"):
        (seed_dir / name).write_bytes(b"synthetic resource paths")
    monkeypatch.setattr(bridge, "_graphics_preference_seed_dir", lambda root: seed_dir)
    runner = ProcessDouble("graphics")
    result = run_command(["export-s1-diagnostics", "model.mph", "output"], root,
                         process_runner=runner)
    assert [item[0] for item in runner.calls] == ["comsolcompile.exe", "javac.exe", "java.exe"]
    assert result["graphics_renderer"]["value"] == "sw"
    assert result["runner_marker"] == "graphics"


def test_overlapping_commands_keep_runners_local_and_subprocess_unchanged(installation):
    root, java_dir = installation
    (java_dir / "ComsolBridge.class").write_bytes(b"existing synthetic bytecode")
    barrier = Barrier(2)
    original = subprocess.run
    calls = {"left": [], "right": []}

    def invoke(marker):
        def process(command, **kwargs):
            assert subprocess.run is original
            assert Path(command[0]).name == "comsolbatch.exe"
            calls[marker].append(command)
            barrier.wait(timeout=10)
            return subprocess.CompletedProcess(command, 0, _json_output(_success(marker)), "")
        return run_command(["inspect-api", marker], root, process_runner=process)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(invoke, "left")
        right = pool.submit(invoke, "right")
        assert left.result(timeout=15)["runner_marker"] == "left"
        assert right.result(timeout=15)["runner_marker"] == "right"
    assert {key: [command[-1] for command in value] for key, value in calls.items()} == {
        "left": ["left"], "right": ["right"]}
    assert subprocess.run is original


@pytest.mark.parametrize("failure,error,match", [
    ("compile", bridge.ComsolConnectionError, "Failed to compile"),
    ("solver", bridge.SolverFailedError, "synthetic solver error"),
    ("schema", bridge.ComsolConnectionError, "Schema violation"),
])
def test_explicit_runner_preserves_error_and_schema_contracts(installation, failure, error, match):
    root, _ = installation
    runner = ProcessDouble("failure", failure=failure)
    with pytest.raises(error, match=match) as captured:
        run_command(["inspect-api"], root, process_runner=runner)
    assert not any(call[0] == "java.exe" for call in runner.calls)
    if failure == "solver":
        assert captured.value.details == "original details"


def test_default_public_entry_preserves_existing_bridge_adapter_signature(monkeypatch):
    calls = []

    def established_adapter(args, root=None):
        calls.append((args, root))
        return _success("default")

    monkeypatch.setattr(bridge, "_run_bridge", established_adapter)
    assert run_command(["inspect-api"])["runner_marker"] == "default"
    assert calls == [(["inspect-api"], None)]
