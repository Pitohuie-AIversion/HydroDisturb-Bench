"""Java parameter admission and helper-aware process/build contracts; no CFD."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from hydro_disturb_bench.comsol_java import bridge

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "src/hydro_disturb_bench/comsol_java"
CLASSES = ("ComsolBridge.class", "ConfigurablePulseBranch.class", "ConfigurablePulseBranch$Spec.class",
           "ConfigurablePulseBranch$StrictProperties.class", "ConfigurablePulseBranch$Raw.class")
NATIVE_FAILURE = "java.lang.UnsatisfiedLinkError: csutil.dll: Can't find dependent libraries"
VALID = {"event_id": "combined", "amplitude_m_s2": "0.0075", "xc_m": "0.17", "yc_m": "0.13",
         "sigma_m": "0.008", "direction_deg": "45", "on_s": "182", "off_s": "184"}


def payload(values=None, extra=""):
    content = "".join(f"{key}={value}\n" for key, value in (values or VALID).items()) + extra
    return "base64:" + base64.b64encode(content.encode()).decode()


@pytest.fixture(scope="module")
def java_spec(tmp_path_factory):
    """Compile the actual pure Spec source without any COMSOL libraries/process."""
    compiler = shutil.which("javac")
    if compiler is None:
        candidate = Path("D:/Program Files/COMSOL64/Multiphysics/java/win64/jre/bin/javac.exe")
        if candidate.is_file():
            compiler = str(candidate)
    if compiler is None:
        pytest.skip("No Java compiler available for the pure parameter parser")
    work = tmp_path_factory.mktemp("pure_local_pulse_spec")
    source = (JAVA / "ConfigurablePulseBranch.java").read_text(encoding="utf-8")
    declarations = source[source.index("    private static final String[] KEYS"):
                          source.index("    private static Map<String,Object> object")]
    # Copy the actual KEYS/StrictProperties/Spec/require bodies, not a reimplementation.
    harness = """import java.util.*; import java.io.StringReader; import java.nio.charset.StandardCharsets;
public class PulseSpecHarness {
""" + declarations + """
 public static void main(String[] args) throws Exception {
   Spec s = new Spec(args[0]);
   System.out.println(s.eventId + "|" + s.dx + "|" + s.dy + "|" + s.propertiesHash);
 }
}
class ComsolBridge {
 static String branchHash(byte[] b) throws Exception {
   return HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(b));
 }
}
"""
    (work / "PulseSpecHarness.java").write_text(harness, encoding="utf-8")
    result = subprocess.run([compiler, "-encoding", "UTF-8", "PulseSpecHarness.java"], cwd=work,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    runtime = str(Path(compiler).with_name("java.exe" if os.name == "nt" else "java"))

    def invoke(value):
        return subprocess.run([runtime, "-cp", str(work), "PulseSpecHarness", value], cwd=work,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    return invoke


@pytest.mark.parametrize("degrees,expected", [(0, (1., 0.)), (90, (0., 1.)), (-90, (0., -1.)),
                                               (180, (-1., 0.)), (-180, (-1., 0.))])
def test_actual_java_parser_direction_snap_and_decoded_byte_hash(java_spec, degrees, expected):
    encoded = payload({**VALID, "direction_deg": str(degrees)})
    result = java_spec(encoded)
    assert result.returncode == 0, result.stderr
    event, dx, dy, digest = result.stdout.strip().split("|")
    assert event == "combined" and (float(dx), float(dy)) == expected
    assert digest == hashlib.sha256(base64.b64decode(encoded[7:])).hexdigest()


@pytest.mark.parametrize("key,value", [
    ("amplitude_m_s2", "0.0101"), ("amplitude_m_s2", "-0.1"), ("sigma_m", "0.0049"),
    ("sigma_m", "0.0201"), ("direction_deg", "181"), ("on_s", "180"), ("off_s", "200"),
    ("on_s", "182.00000000001"), ("off_s", "184.2"), ("xc_m", "0.01"), ("xc_m", "0.1"),
    ("yc_m", "0.23"), ("amplitude_m_s2", "NaN"), ("sigma_m", "Infinity"),
    ("event_id", "../escape"), ("event_id", "has space"), ("event_id", "event.with.dot"),
])
def test_actual_java_parser_rejects_invalid_parameters(java_spec, key, value):
    result = java_spec(payload({**VALID, key: value}))
    assert result.returncode != 0


@pytest.mark.parametrize("extra", ["unknown=1\n", "amplitude_m_s2=0.005\n"])
def test_actual_java_parser_rejects_unknown_and_duplicate_properties(java_spec, extra):
    assert java_spec(payload(extra=extra)).returncode != 0


def test_actual_java_parser_rejects_missing_and_non_payload_inputs(java_spec):
    values = dict(VALID)
    values.pop("sigma_m")
    assert java_spec(payload(values)).returncode != 0
    assert java_spec("config.properties").returncode != 0
    assert java_spec("base64:!!!").returncode != 0
    assert java_spec(payload({**VALID, "amplitude_m_s2": "7.5e-3"})).returncode == 0


@pytest.mark.parametrize("sigma,center,accepted", [
    (.0051, [.25, .2247], True), (.0051, [.0153, .20], True),
    (.0051, [.3847, .20], True), (.0051, [.25, .0153], True),
    (.02, [.06, .18], True), (.0051, [.25, .224700000000001], False),
    (.01, [.135, .12], False), (.01, [.135000000000001, .12], True),
])
def test_java_and_python_decimal_geometry_admission_agree(java_spec, sigma, center, accepted):
    from hydro_disturb_bench.pulse_parameters import validate_pulse_config

    config = {"schema_version": 1, "event_id": "boundary", "mechanism": "gaussian_body_acceleration",
              "pulse": {"center_m": center, "sigma_m": sigma, "direction_deg": 45,
                        "acceleration_m_s2": .0075, "on_s": 182., "off_s": 184.}}
    if accepted:
        validate_pulse_config(config)
    else:
        with pytest.raises(bridge.InvalidParameterError):
            validate_pulse_config(config)
    values = {**VALID, "sigma_m": repr(sigma), "xc_m": repr(center[0]), "yc_m": repr(center[1])}
    assert (java_spec(payload(values)).returncode == 0) is accepted


@pytest.fixture
def compiler_installation(tmp_path, monkeypatch):
    root, java_dir = tmp_path / "COMSOL", tmp_path / "bridge"
    (root / "bin/win64").mkdir(parents=True)
    (root / "bin/win64/comsolcompile.exe").write_bytes(b"fake")
    (root / "java/win64/jre/bin").mkdir(parents=True)
    (root / "java/win64/jre/bin/javac.exe").write_bytes(b"fake")
    java_dir.mkdir()
    for name in ("ComsolBridge.java", "ConfigurablePulseBranch.java", "ComsolStandalone.java"):
        (java_dir / name).write_text("synthetic " + name, encoding="utf-8")
    monkeypatch.setattr(bridge, "__file__", str(java_dir / "bridge.py"))
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    return root, java_dir


def write_classes(directory):
    for name in CLASSES:
        (directory / name).write_bytes(name.encode())


def test_helper_source_change_and_missing_inner_bytecode_force_recompile(compiler_installation):
    root, directory = compiler_installation
    calls = []

    def process(command, **kwargs):
        calls.append(command)
        assert [Path(p).name for p in command[1:]] == ["ComsolBridge.java", "ConfigurablePulseBranch.java"]
        write_classes(directory)
        return subprocess.CompletedProcess(command, 0, "", "")

    assert bridge.ensure_bridge_compiled(root, process_runner=process) == directory / "ComsolBridge.class"
    assert len(calls) == 1
    bridge.ensure_bridge_compiled(root, process_runner=process)
    assert len(calls) == 1
    older = time.time() - 10
    for name in CLASSES:
        os.utime(directory / name, (older, older))
    bridge.ensure_bridge_compiled(root, process_runner=process)
    assert len(calls) == 2
    (directory / "ConfigurablePulseBranch$Spec.class").unlink()
    bridge.ensure_bridge_compiled(root, process_runner=process)
    assert len(calls) == 3


@pytest.mark.parametrize("failure", [None, "syntax", "missing_class"])
def test_narrow_native_compile_failure_uses_injected_bundled_compiler(compiler_installation, failure):
    root, directory = compiler_installation
    calls = []
    ordinary_run = subprocess.run

    def process(command, **kwargs):
        calls.append((command, kwargs))
        assert subprocess.run is ordinary_run
        if Path(command[0]).name == "comsolcompile.exe":
            return subprocess.CompletedProcess(command, 0, "ERROR: Compilation failed", NATIVE_FAILURE)
        assert Path(command[0]).name == "javac.exe"
        assert "-proc:none" in command
        work = Path(kwargs["cwd"])
        assert (work / "ConfigurablePulseBranch.java").read_bytes() == (directory / "ConfigurablePulseBranch.java").read_bytes()
        if failure == "syntax":
            return subprocess.CompletedProcess(command, 1, "", "injected syntax error")
        if failure != "missing_class":
            write_classes(work)
        return subprocess.CompletedProcess(command, 0, "", "")

    if failure:
        with pytest.raises(bridge.ComsolConnectionError, match="javac"):
            bridge.ensure_bridge_compiled(root, process_runner=process)
        assert not (directory / "ComsolBridge.class").exists()
    else:
        bridge.ensure_bridge_compiled(root, process_runner=process)
        assert all((directory / name).read_bytes() == name.encode() for name in CLASSES)
    assert len(calls) == 2 and not Path(calls[-1][1]["cwd"]).exists()


def test_ordinary_compile_error_does_not_trigger_native_fallback(compiler_installation):
    root, _ = compiler_installation
    calls = []

    def process(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "Java syntax error")

    with pytest.raises(bridge.ComsolConnectionError, match="syntax error"):
        bridge.ensure_bridge_compiled(root, process_runner=process)
    assert len(calls) == 1


def test_native_compile_log_must_be_current_profile_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge.sys, "platform", "win32")
    monkeypatch.setattr(bridge.Path, "home", lambda: tmp_path)
    log = tmp_path / ".comsol/v64/logs/compile1.log"
    log.parent.mkdir(parents=True)
    log.write_text(NATIVE_FAILURE, encoding="utf-8")
    proc = subprocess.CompletedProcess([], 0, f"ERROR: Compilation failed. For details, see the log file {log}", "")
    assert bridge._native_compile_dependency_failure(proc, time.time())
    os.utime(log, (1, 1))
    assert not bridge._native_compile_dependency_failure(proc, time.time())
    external = tmp_path / "compile_external.log"
    external.write_text(NATIVE_FAILURE, encoding="utf-8")
    proc.stdout = f"log file {external}"
    assert not bridge._native_compile_dependency_failure(proc, time.time())


def test_standalone_copies_all_helper_classes_and_retains_invocation_runner(compiler_installation):
    root, directory = compiler_installation
    write_classes(directory)
    calls = []

    def process(command, **kwargs):
        calls.append(command)
        work = Path(kwargs["cwd"])
        assert all((work / name).read_bytes() == name.encode() for name in CLASSES)
        return subprocess.CompletedProcess(command, 0, "runtime output", "")

    result = bridge._run_standalone(root, directory / "ComsolBridge.class",
                                   ["inspect-local-pulse", "source", "output", payload()], process_runner=process)
    assert result.stdout == "runtime output"
    assert [Path(c[0]).name for c in calls] == ["javac.exe", "java.exe"]
    (directory / "ConfigurablePulseBranch$Raw.class").unlink()
    with pytest.raises(bridge.ComsolConnectionError, match="incomplete"):
        bridge._run_standalone(root, directory / "ComsolBridge.class", ["run-local-pulse"], process_runner=process)
    assert len(calls) == 2


def test_frozen_entrypoint_contracts_and_time_guard_remain_visible():
    dispatcher = (JAVA / "ComsolBridge.java").read_text(encoding="utf-8")
    configurable = (JAVA / "ConfigurablePulseBranch.java").read_text(encoding="utf-8")
    assert 'amplitude != 0.0 && amplitude != 0.005' in dispatcher
    assert 'amplitude != 0.0025 && amplitude != 0.010' in dispatcher
    assert '"m3-pulse-amplitude-series-v1".equals(seriesId)' in dispatcher
    assert configurable.count("sol.runAll(false)") == 1
    assert configurable.index("if (solve)") < configurable.index("sol.runAll(false)")
    assert "FileInputStream" not in configurable and "nearest" not in configurable


def test_multisource_save_failure_in_log_triggers_bundled_javac_fallback(
    compiler_installation, tmp_path, monkeypatch
):
    root, directory = compiler_installation
    monkeypatch.setattr(bridge.Path, "home", lambda: tmp_path)
    log = tmp_path / ".comsol/v64/logs/compile9.log"
    log.parent.mkdir(parents=True)
    calls = []

    def process(command, **kwargs):
        calls.append(command)
        if Path(command[0]).name == "comsolcompile.exe":
            log.write_text("2026-11-12 ERROR 无法保存编译结果。 [com.comsol.util]\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                command, 0, f"ERROR: Compilation failed. For details, see the log file {log}", ""
            )
        assert Path(command[0]).name == "javac.exe"
        write_classes(Path(kwargs["cwd"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    out = bridge.ensure_bridge_compiled(root, process_runner=process)
    assert out == directory / "ComsolBridge.class"
    assert [Path(c[0]).name for c in calls] == ["comsolcompile.exe", "javac.exe"]
    assert all((directory / name).is_file() for name in CLASSES)
