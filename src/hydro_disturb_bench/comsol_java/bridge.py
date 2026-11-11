"""COMSOL Java API Bridge for HydroDisturb-Bench.

Provides programmatic access to COMSOL Multiphysics using the official
Java API and comsolbatch/comsolcompile tools, guaranteeing baseline
model immutability, parameter validation, and detailed execution logging.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

# Explicit re-exports preserve the established bridge import surface.
from .errors import ERR_API_UNSUPPORTED as ERR_API_UNSUPPORTED
from .errors import ERR_COMSOL_CONNECTION_FAILED as ERR_COMSOL_CONNECTION_FAILED
from .errors import ERR_INVALID_PARAMETER as ERR_INVALID_PARAMETER
from .errors import ERR_INVALID_UNIT as ERR_INVALID_UNIT
from .errors import ERR_MODEL_NOT_FOUND as ERR_MODEL_NOT_FOUND
from .errors import ERR_MODEL_VALIDATION_FAILED as ERR_MODEL_VALIDATION_FAILED
from .errors import ERR_NODE_NOT_FOUND as ERR_NODE_NOT_FOUND
from .errors import ERR_SOLVER_FAILED as ERR_SOLVER_FAILED
from .errors import ApiUnsupportedError as ApiUnsupportedError
from .errors import ComsolConnectionError as ComsolConnectionError
from .errors import ComsolError as ComsolError
from .errors import InvalidParameterError as InvalidParameterError
from .errors import InvalidUnitError as InvalidUnitError
from .errors import ModelNotFoundError as ModelNotFoundError
from .errors import ModelValidationError as ModelValidationError
from .errors import NodeNotFoundError as NodeNotFoundError
from .errors import SolverFailedError as SolverFailedError
from .errors import map_error as map_error
from .file_transactions import _atomic_publish_new_file as _atomic_publish_new_file
from .file_transactions import _is_file_locked as _is_file_locked
from .file_transactions import _posix_rename_noreplace as _posix_rename_noreplace
from .file_transactions import _safe_quarantine_move as _safe_quarantine_move
from .file_transactions import _safe_quarantine_move_dir as _safe_quarantine_move_dir
from .file_transactions import file_publish_lock as file_publish_lock
from .file_transactions import is_pid_alive as is_pid_alive
from .file_transactions import is_task_active as is_task_active
from .file_transactions import list_quarantine_records as list_quarantine_records
from .file_transactions import publish_staging_file as publish_staging_file
from .file_transactions import quarantine_staging_dir as quarantine_staging_dir
from .file_transactions import quarantine_staging_file as quarantine_staging_file
from .file_transactions import recover_unclaimed_staging as recover_unclaimed_staging
from .file_transactions import task_staging_session as task_staging_session

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment]


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file safely handling potential read locks."""
    path = Path(path)
    if not path.is_file():
        raise ModelNotFoundError(f"File not found: {path}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def find_comsol_root() -> Path:
    """Discover the COMSOL Multiphysics root directory.

    Checks:
    1. COMSOL_ROOT or COMSOL_DIR environment variables.
    2. Windows Registry: HKEY_LOCAL_MACHINE\\SOFTWARE\\COMSOL\\COMSOL64\\COMSOLROOT
    3. Common standard installation paths.
    """
    env_root = os.environ.get("COMSOL_ROOT") or os.environ.get("COMSOL_DIR")
    if env_root and Path(env_root).is_dir():
        return Path(env_root)

    # Windows registry check
    if sys.platform == "win32":
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\COMSOL\COMSOL64") as key:
                val, _ = winreg.QueryValueEx(key, "COMSOLROOT")
                if val and Path(val).is_dir():
                    return Path(val)
        except OSError:
            pass

    # Standard fallback paths
    candidates = [
        Path(r"D:\Program Files\COMSOL64\Multiphysics"),
        Path(r"C:\Program Files\COMSOL64\Multiphysics"),
        Path(r"D:\COMSOL64\Multiphysics"),
        Path(r"C:\COMSOL64\Multiphysics"),
    ]
    for cand in candidates:
        if cand.is_dir():
            return cand

    raise ComsolConnectionError(
        "COMSOL installation root could not be located. "
        "Please ensure COMSOL is installed and set the COMSOL_ROOT environment variable."
    )


def get_comsol_bin_dir(root: Path | None = None) -> Path:
    """Get the bin directory for the current platform."""
    root = root or find_comsol_root()
    arch = "win64" if sys.platform == "win32" else "glnxa64"
    bin_dir = root / "bin" / arch
    if not bin_dir.is_dir():
        raise ComsolConnectionError(f"COMSOL bin directory not found at {bin_dir}")
    return bin_dir


def _native_compile_dependency_failure(proc: subprocess.CompletedProcess, started: float) -> bool:
    """Recognize only the observed Windows csutil dependency startup failure."""
    details = (proc.stdout or "") + "\n" + (proc.stderr or "")
    match = re.search(r"log file\s+([^\r\n]+\.log)", details)
    if match:
        log = Path(match.group(1).strip()).resolve()
        profile = (Path.home() / ".comsol").resolve()
        try:
            if (
                log.is_relative_to(profile)
                and log.parent.name == "logs"
                and log.name.startswith("compile")
                and log.stat().st_mtime >= started - 2
            ):
                details += "\n" + log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return (
        sys.platform == "win32"
        and "UnsatisfiedLinkError" in details
        and "csutil.dll" in details
        and "Can't find dependent libraries" in details
    )


def _compile_bundled_java(root, sources, required_classes, run_process):
    """Compile unchanged sources with the installed JDK after native startup fails."""
    compiler = root / "java" / "win64" / "jre" / "bin" / "javac.exe"
    if not compiler.is_file():
        raise ComsolConnectionError("Bundled javac is absent after native compiler startup failure")
    with tempfile.TemporaryDirectory(prefix="hydrobench_compile_") as temporary:
        work = Path(temporary)
        if not str(work).isascii() or not str(root).isascii():
            raise ComsolConnectionError(
                "Bundled Java compilation requires ASCII temporary and installation paths"
            )
        for source in sources:
            shutil.copy2(source, work / source.name)
        proc = run_process(
            [
                str(compiler),
                "-encoding",
                "UTF-8",
                "-proc:none",
                "-cp",
                str(root / "plugins" / "*"),
                *(source.name for source in sources),
            ],
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise ComsolConnectionError(
                f"Bundled javac compilation failed:\n{proc.stderr}\n{proc.stdout}"
            )
        if any(not (work / target.name).is_file() for target in required_classes):
            raise ComsolConnectionError(
                "Bundled javac did not produce all required bridge/helper classes"
            )
        for target in required_classes:
            shutil.copy2(work / target.name, target)


def ensure_bridge_compiled(
    root: Path | None = None,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> Path:
    """Compile the bridge and its owned helper classes when any source changes."""
    root = root or find_comsol_root()
    bin_dir = get_comsol_bin_dir(root)
    comsolcompile = bin_dir / ("comsolcompile.exe" if sys.platform == "win32" else "comsolcompile")
    if not comsolcompile.is_file():
        raise ComsolConnectionError(f"comsolcompile executable not found at {comsolcompile}")

    java_dir = Path(__file__).resolve().parent
    java_src = java_dir / "ComsolBridge.java"
    java_class = java_dir / "ComsolBridge.class"

    if not java_src.is_file():
        raise ComsolConnectionError(f"ComsolBridge.java source file missing: {java_src}")

    sources = [java_src]
    required_classes = [java_class]
    helper_source = java_dir / "ConfigurablePulseBranch.java"
    if helper_source.is_file():
        sources.append(helper_source)
        required_classes.extend(
            java_dir / name
            for name in (
                "ConfigurablePulseBranch.class",
                "ConfigurablePulseBranch$Spec.class",
                "ConfigurablePulseBranch$StrictProperties.class",
                "ConfigurablePulseBranch$Raw.class",
            )
        )
    newest_source = max(source.stat().st_mtime for source in sources)
    needs_compile = any(
        not target.is_file() or target.stat().st_mtime < newest_source
        for target in required_classes
    )

    if needs_compile:
        cmd = [str(comsolcompile), *(str(source) for source in sources)]
        run_process = subprocess.run if process_runner is None else process_runner
        started = time.time()
        proc = run_process(
            cmd,
            cwd=str(java_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if (
            proc.returncode != 0
            or "ERROR: Compilation failed" in proc.stdout
            or "ERROR: Compilation failed" in proc.stderr
        ):
            if _native_compile_dependency_failure(proc, started):
                _compile_bundled_java(root, sources, required_classes, run_process)
            else:
                raise ComsolConnectionError(
                    f"Failed to compile ComsolBridge.java (exit code {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
                )
        if len(sources) > 1 and any(not path.is_file() for path in required_classes):
            raise ComsolConnectionError(
                "Compilation did not produce all required bridge/helper classes"
            )

    return java_class


def _graphics_preference_seed_dir(root: Path) -> Path:
    """Locate this installation's existing Windows profile without assuming a version."""
    ini = root / "bin" / "win64" / "comsolbatch.ini"
    if not ini.is_file():
        raise ComsolConnectionError(
            "Cannot locate COMSOL profile configuration for isolated graphics export"
        )
    match = re.search(
        r"@user\.home/\.comsol/(v[0-9]+)/", ini.read_text(encoding="utf-8", errors="replace")
    )
    if not match:
        raise ComsolConnectionError("COMSOL launcher does not identify its user preference profile")
    return Path.home() / ".comsol" / match.group(1)


def _run_standalone(
    root: Path,
    class_file: Path,
    args: list[str],
    *,
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> subprocess.CompletedProcess:
    """Use the official single-process Java API after a native batch startup failure.

    DLL search changes affect only this JVM. ASCII temporary class paths and
    base64 UTF-8 arguments avoid the bundled Java launcher's Windows argv loss.
    No model is copied and no installed file is changed.
    """
    run_process = subprocess.run if process_runner is None else process_runner
    java_bin = root / "java" / "win64" / "jre" / "bin"
    with tempfile.TemporaryDirectory(prefix="hydrobench_comsol_") as temp:
        work = Path(temp)
        if not str(work).isascii() or not str(root).isascii():
            raise ComsolConnectionError(
                "Standalone Java launcher requires ASCII temp and installation paths"
            )
        shutil.copy2(class_file, work / "ComsolBridge.class")
        # A fresh ASCII runtime directory must contain the complete helper class
        # family, including nested classes; copying only the dispatcher is unsafe.
        helpers = sorted(
            path
            for path in class_file.parent.glob("ConfigurablePulseBranch*.class")
            if path.name == "ConfigurablePulseBranch.class"
            or path.name.startswith("ConfigurablePulseBranch$")
        )
        if args and args[0] in ("inspect-local-pulse", "run-local-pulse"):
            required = {
                "ConfigurablePulseBranch.class",
                "ConfigurablePulseBranch$Spec.class",
                "ConfigurablePulseBranch$StrictProperties.class",
                "ConfigurablePulseBranch$Raw.class",
            }
            if not required.issubset(path.name for path in helpers):
                raise ComsolConnectionError("Configurable pulse helper bytecode is incomplete")
        for helper in helpers:
            shutil.copy2(helper, work / helper.name)
        source = work / "ComsolStandalone.java"
        shutil.copy2(Path(__file__).with_name("ComsolStandalone.java"), source)
        classpath = str(root / "plugins" / "*") + os.pathsep + str(work)
        compiled = run_process(
            [str(java_bin / "javac.exe"), "-cp", classpath, str(source)],
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if compiled.returncode != 0:
            raise ComsolConnectionError(
                f"Standalone launcher compilation failed: {compiled.stderr}"
            )
        encoded = [
            base64.b64encode(value.encode("utf-8")).decode("ascii")
            for value in [str(root.resolve()), *args]
        ]
        graphics_options = []
        isolated_preferences = None
        preference_seed_hashes = {}
        if args and args[0] == "export-s1-diagnostics":
            isolated_preferences = work / "preferences"
            isolated_preferences.mkdir()
            # A blank profile lacks resource paths needed by model loading. Copy the
            # existing local files only into this temporary JVM directory; never edit
            # their originals or include their contents in result metadata.
            seed_dir = _graphics_preference_seed_dir(root)
            for name in ("comsol.prefs", "comsolserver.prefs"):
                seed_file = seed_dir / name
                if not seed_file.is_file():
                    raise ComsolConnectionError(
                        f"Missing existing COMSOL preferences needed for isolated export: {seed_file}"
                    )
                contents = seed_file.read_bytes()
                (isolated_preferences / name).write_bytes(contents)
                preference_seed_hashes[name] = hashlib.sha256(contents).hexdigest()
            graphics_options = ["-Dcs.3drend=sw", f"-Dcs.prefsdir={isolated_preferences}"]
        process = run_process(
            [
                str(java_bin / "java.exe"),
                "-Xmx2g",
                "-Xss4m",
                *graphics_options,
                "-cp",
                classpath,
                "ComsolStandalone",
                *encoded,
            ],
            cwd=work,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if isolated_preferences is not None:
            process.graphics_preferences_dir = str(isolated_preferences)
            process.graphics_preference_seed_hashes = preference_seed_hashes
        return process


def _run_bridge(
    args: list[str],
    root: Path | None = None,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict[str, Any]:
    """Execute ComsolBridge with given arguments and parse JSON result."""
    root = root or find_comsol_root()
    bin_dir = get_comsol_bin_dir(root)
    comsolbatch = bin_dir / ("comsolbatch.exe" if sys.platform == "win32" else "comsolbatch")
    if not comsolbatch.is_file():
        raise ComsolConnectionError(f"comsolbatch executable not found at {comsolbatch}")

    # Omit the optional keyword on the default path to retain existing adapters.
    process_options = {} if process_runner is None else {"process_runner": process_runner}
    run_process = subprocess.run if process_runner is None else process_runner
    class_file = ensure_bridge_compiled(root, **process_options)

    # On Windows, image export requires headless software rendering and isolated preferences
    # via the standalone Java launcher. comsolbatch without a display skips PNG rendering.
    if sys.platform == "win32" and args and args[0] == "export-s1-diagnostics":
        proc = _run_standalone(root, class_file, args, **process_options)
        launch_mode = "standalone_java_api"
        startup_failure = None
    else:
        cmd = [str(comsolbatch), "-nosave", "-inputfile", str(class_file)] + args

        proc = run_process(
            cmd,
            cwd=str(class_file.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Retry only an identified startup failure, before any bridge command ran.
        # Never retry solver/model errors or a process that emitted bridge JSON.
        launch_mode = "comsolbatch"
        startup_failure = None
        if (
            sys.platform == "win32"
            and "<<<JSON_START>>>" not in (proc.stdout or "")
            and "Failed to start COMSOL" in (proc.stdout or "")
            and "FlNativeException" in (proc.stdout or "")
        ):
            startup_failure = {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            }
            proc = _run_standalone(root, class_file, args, **process_options)
            launch_mode = "standalone_java_api"

    # Clean up comsolbatch status file if created in the directory
    status_file = class_file.with_name(f"{class_file.name}.status")
    if status_file.is_file():
        try:
            status_file.unlink()
        except OSError:
            pass

    stdout = proc.stdout or ""
    start_tag = "<<<JSON_START>>>"
    end_tag = "<<<JSON_END>>>"

    # Attempt to extract structured JSON report
    data: Any = None
    if start_tag in stdout and end_tag in stdout:
        json_str = stdout.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as err:
            raise ComsolConnectionError(
                f"Failed to decode JSON from ComsolBridge: {err}\nRaw output: {json_str}"
            ) from err

    # Top-level JSON must be an object/dict
    if data is not None and not isinstance(data, dict):
        raise ComsolConnectionError(
            f"ComsolBridge emitted invalid JSON payload: expected JSON object, got {type(data).__name__}"
        )

    # If the bridge emitted a structured error, propagate it as a typed exception
    if data and data.get("status") != "SUCCESS":
        err_code = data.get("error_code")
        if not isinstance(err_code, str):
            err_code = ERR_COMSOL_CONNECTION_FAILED
        msg = data.get("message", "Bridge execution failed")
        details = data.get("details", "")
        raise map_error(str(err_code), str(msg), str(details))

    # If subprocess returned non-zero without a structured SUCCESS
    if proc.returncode != 0:
        raise ComsolConnectionError(
            f"ComsolBridge process failed with exit code {proc.returncode}:\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{proc.stderr}"
        )

    # If no valid structured output was emitted
    if not data or data.get("status") != "SUCCESS":
        raise ComsolConnectionError(
            f"ComsolBridge execution produced invalid output without SUCCESS status (exit code {proc.returncode}):\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{proc.stderr}"
        )

    # Strict schema validation
    op = data.get("operation")
    if not isinstance(op, str):
        raise ComsolConnectionError(
            "ComsolBridge JSON response missing valid string field 'operation'"
        )

    if op == "inspect_api":
        if "comsol_version" not in data or "api_specification" not in data:
            raise ComsolConnectionError(
                "Schema violation in inspect_api response: missing version or specification"
            )
    elif op == "inspect_model":
        if "model_tag" not in data or "parameters" not in data or "components" not in data:
            raise ComsolConnectionError(
                "Schema violation in inspect_model response: missing model_tag, parameters, or components"
            )
    elif op == "update_parameter":
        if "param_name" not in data or "new_expr" not in data or "readback_value" not in data:
            raise ComsolConnectionError(
                "Schema violation in update_parameter response: missing param_name, new_expr, or readback_value"
            )
    elif op == "run_study":
        if (
            "study_tag" not in data
            or "elapsed_ms" not in data
            or "sol_tags" not in data
            or "solution_stats" not in data
        ):
            raise ComsolConnectionError(
                "Schema violation in run_study response: missing study_tag, elapsed_ms, sol_tags, or solution_stats"
            )
    elif op == "inspect_probe_and_solver":
        if "datasets" not in data or "plotgroups" not in data or "solver_details" not in data:
            raise ComsolConnectionError(
                "Schema violation in inspect_probe_and_solver response: missing datasets, plotgroups, or solver_details"
            )

    data.setdefault("log", stdout)
    data["launch_mode"] = launch_mode
    if launch_mode == "standalone_java_api" and args and args[0] == "export-s1-diagnostics":
        renderer_values = re.findall(
            r"^COMSOL_GRAPHICS_RENDERER_READBACK=([^\r\n]+)\r?$", stdout, re.MULTILINE
        )
        if renderer_values != ["sw"]:
            raise ComsolConnectionError(
                "Standalone image export lacks a verified software-renderer preference readback"
            )
        preference_dirs = re.findall(
            r"^COMSOL_GRAPHICS_PREFSDIR_READBACK=([^\r\n]+)\r?$", stdout, re.MULTILINE
        )
        try:
            actual_directory = base64.b64decode(preference_dirs[0], validate=True).decode("utf-8")
        except (IndexError, ValueError, UnicodeDecodeError) as exc:
            raise ComsolConnectionError(
                "Standalone image export lacks isolated preference-directory readback"
            ) from exc
        if len(preference_dirs) != 1 or actual_directory != getattr(
            proc, "graphics_preferences_dir", None
        ):
            raise ComsolConnectionError(
                "Standalone image export preference directory does not match its temporary launch directory"
            )
        data["graphics_renderer"] = {
            "source": "ModelUtil.getPreference",
            "preference": "graphics.rendering.3drend",
            "value": renderer_values[0],
            "scope": "export_process_only",
            "preferences_directory": actual_directory,
            "preferences_directory_source": "pre-init cs.prefsdir system property readback matched to launcher",
            "seed_preferences_sha256": getattr(proc, "graphics_preference_seed_hashes", {}),
        }
    if startup_failure is not None:
        data["batch_startup_failure"] = startup_failure
    return data


def run_command(
    args: list[str],
    root: Path | None = None,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict[str, Any]:
    """Run one bridge command with an optional invocation-local process runner.

    The runner follows ``subprocess.run``'s argument and CompletedProcess contract,
    including requested text decoding. It handles every compilation, batch launch,
    and standalone fallback for this call without changing global subprocess state.
    Model/output identity and experiment-specific admission remain caller contracts.
    """
    if process_runner is None:
        return _run_bridge(args, root=root)
    return _run_bridge(args, root=root, process_runner=process_runner)


def inspect_api(root: Path | None = None) -> dict[str, Any]:
    """Inspect current COMSOL installation version and verified Java API specification."""
    return _run_bridge(["inspect-api"], root=root)


def inspect_model(model_path: Path | str, root: Path | None = None) -> dict[str, Any]:
    """Read full COMSOL model structure in a strictly read-only manner."""
    model_path = Path(model_path)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file does not exist: {model_path}")

    # Compute baseline hash before inspection
    hash_before = compute_file_hash(model_path)

    res = _run_bridge(["inspect-model", str(model_path.resolve())], root=root)

    # Verify baseline hash after inspection
    hash_after = compute_file_hash(model_path)
    if hash_before != hash_after:
        raise ModelValidationError(
            f"Integrity check failed: source model was modified during read-only inspection! "
            f"Before: {hash_before}, After: {hash_after}"
        )

    res["sha256"] = hash_after
    return res


def inspect_native_mesh(model_path: Path | str, root: Path | None = None) -> dict[str, Any]:
    """Read native COMSOL mesh feature settings and statistics without modifying the model."""
    model_path = Path(model_path)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file does not exist: {model_path}")
    before = compute_file_hash(model_path)
    res = _run_bridge(["inspect-native-mesh", str(model_path.resolve())], root=root)
    after = compute_file_hash(model_path)
    if before != after:
        raise ModelValidationError(
            f"Integrity check failed: source model changed during mesh inspection ({before} -> {after})"
        )
    res["sha256"] = after
    return res


def refine_native_mesh(
    model_path: Path | str,
    output_path: Path | str,
    plan: dict[str, Any],
    root: Path | None = None,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    """Apply a hash-bound M1 size plan to a new native mesh; never execute a study."""
    model_path, output_path = Path(model_path), Path(output_path)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")
    if output_path.is_symlink() or model_path.resolve() == output_path.resolve():
        raise ModelValidationError("Refusing to overwrite or alias the source model")
    if output_path.exists() and not allow_overwrite:
        raise FileExistsError(f"Mesh output already exists: {output_path}")
    before_hash = compute_file_hash(model_path)
    if str(plan.get("source_sha256", "")).upper() != before_hash.upper():
        raise ModelValidationError(
            "Mesh plan source SHA-256 does not match the actual source model"
        )
    factor = float(plan.get("refinement_factor", 0))
    if not math.isfinite(factor) or factor <= 1:
        raise InvalidParameterError("Refinement factor must be finite and greater than one")
    component, mesh = plan.get("component_tag", ""), plan.get("mesh_tag", "")
    if not all(
        isinstance(tag, str) and re.fullmatch(r"[A-Za-z0-9_]+", tag) for tag in (component, mesh)
    ):
        raise InvalidParameterError("Plan must name verified component and mesh tags")
    rows, seen, size_changes = [], set(), set()
    for change in plan.get("changes", []):
        path, prop = change.get("feature_path"), change.get("property")
        if (
            not isinstance(path, list)
            or not path
            or not all(isinstance(tag, str) and re.fullmatch(r"[A-Za-z0-9_]+", tag) for tag in path)
        ):
            raise InvalidParameterError("Each change requires a verified mesh feature path")
        key = (tuple(path), prop)
        if key in seen:
            raise InvalidParameterError("Duplicate native mesh property change")
        seen.add(key)
        old, new = change.get("before"), change.get("after")
        if prop == "custom":
            if old != "off" or new != "on":
                raise InvalidParameterError("The only custom-mode change is off to on")
            kind = "string"
        elif prop in {"hmax", "hmin"}:
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or v <= 0
                for v in (old, new)
            ):
                raise InvalidParameterError(
                    "Mesh size before/after values must be finite positive numbers"
                )
            if not math.isclose(new, old / factor, rel_tol=1e-12, abs_tol=0):
                raise InvalidParameterError(
                    "Mesh size change does not follow the declared refinement factor"
                )
            kind = "number"
            size_changes.add(prop)
        else:
            raise InvalidParameterError(f"Unapproved M1 native mesh property: {prop}")
        rows.append("\t".join(["/".join(path), prop, kind, str(old), str(new)]))
    if size_changes != {"hmax", "hmin"}:
        raise InvalidParameterError("Native M1 plan requires explicit hmax and hmin refinement")
    encoded = base64.b64encode("\n".join(rows).encode("utf-8")).decode("ascii")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"
    args = [
        "refine-native-mesh",
        str(model_path.resolve()),
        str(staging_path.resolve()),
        component,
        mesh,
        encoded,
    ]
    with task_staging_session(staging_path, stage_id):
        try:
            result = _run_bridge(args, root=root)
            if compute_file_hash(model_path) != before_hash:
                raise ModelValidationError("Source model changed during native mesh refinement")
            if result.get("stored_solution_data_empty") is not True:
                raise ModelValidationError("Refined input must not retain the source CFD solution")
            publish_staging_file(
                staging_path, output_path, stage_id, allow_overwrite=allow_overwrite
            )
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise
    result["source_sha256"] = before_hash
    result["output_model"] = str(output_path.resolve())
    result["output_sha256"] = compute_file_hash(output_path)
    result["refinement_plan"] = plan
    return result


def inspect_probe_and_solver(
    model_path: Path | str,
    probe_x: str = "0.15",
    probe_y: str = "0.12",
    root: Path | None = None,
    *,
    probe_output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Inspect a saved solution; optionally re-export probes with live node evidence.

    The optional export directory must not exist. No solver runs or model saves
    occur, and every returned export is bound to the source and file hashes.
    """
    model_path = Path(model_path)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file does not exist: {model_path}")

    hash_before = compute_file_hash(model_path)
    args = ["inspect-probe-and-solver", str(model_path.resolve()), str(probe_x), str(probe_y)]
    export_dir = None
    if probe_output_dir is not None:
        export_dir = Path(probe_output_dir).resolve()
        export_dir.mkdir(parents=True, exist_ok=False)
        args.append(str(export_dir))

    try:
        res = _run_bridge(args, root=root)
    finally:
        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            raise ModelValidationError(
                f"Integrity check failed: source model was modified during probe and solver inspection! "
                f"Before: {hash_before}, After: {hash_after}"
            )

    res["sha256"] = hash_after
    if export_dir is not None:
        exports = res.get("probe_exports")
        if not isinstance(exports, list) or len(exports) != 2:
            raise ModelValidationError("Probe re-export did not return both node evidence records")
        for entry in exports:
            exported = Path(entry["filename"]).resolve()
            if exported.parent != export_dir or not exported.is_file():
                raise ModelValidationError(f"Probe export file association invalid: {exported}")
            if Path(entry["model_path"]).resolve() != model_path.resolve():
                raise ModelValidationError("Probe export model association invalid")
            entry["file_sha256"] = compute_file_hash(exported)
            entry["model_sha256"] = hash_after
        (export_dir / "probe_evidence.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return res


def update_parameter(
    model_path: Path | str,
    output_path: Path | str,
    param_name: str,
    param_expr: str,
    descr: str = "",
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Modify a verified parameter in a model copy and read back the value."""
    model_path = Path(model_path)
    output_path = Path(output_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")

    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    if model_path.resolve() == output_path.resolve():
        raise ModelValidationError(
            "Refusing to overwrite original baseline model: source and output path are identical"
        )

    # Basic parameter expression and unit check before execution
    if not param_name or not param_name.strip():
        raise InvalidParameterError("Parameter name cannot be empty")
    if not param_expr or not param_expr.strip():
        raise InvalidParameterError("Parameter expression cannot be empty")

    # Record baseline hash before modification
    hash_before = compute_file_hash(model_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"

    args = [
        "update-param",
        str(model_path.resolve()),
        str(staging_path.resolve()),
        param_name.strip(),
        param_expr.strip(),
    ]
    if descr:
        args.append(descr.strip())

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise

        # Verify baseline model integrity BEFORE publishing to final destination!
        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise ModelValidationError(
                f"Safety violation: original model file was modified during parameter update! "
                f"Original SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifact quarantined; output file was NOT published."
            )

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    res["source_sha256"] = hash_before
    res["output_model"] = str(output_path.resolve())
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def run_study(
    model_path: Path | str,
    output_path: Path | str,
    study_tag: str,
    tlist_override: str = "",
    rtol_override: str = "",
    maxstep_override: str = "",
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Execute a study on a validated model copy and save results to output_path."""
    model_path = Path(model_path)
    output_path = Path(output_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")

    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    if model_path.resolve() == output_path.resolve():
        raise ModelValidationError(
            "Refusing to overwrite baseline model: source and output path are identical"
        )

    # Record baseline hash before execution
    hash_before = compute_file_hash(model_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"

    args = [
        "run-study",
        str(model_path.resolve()),
        str(staging_path.resolve()),
        study_tag.strip(),
    ]
    if tlist_override or rtol_override or maxstep_override:
        args.append(tlist_override.strip())
    if rtol_override or maxstep_override:
        args.append(rtol_override.strip())
    if maxstep_override:
        args.append(maxstep_override.strip())

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise

        # Verify baseline model integrity BEFORE publishing to final destination!
        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise ModelValidationError(
                f"Safety violation: source model was modified during study run! "
                f"Original SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifact quarantined; output file was NOT published."
            )

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    res["source_sha256"] = hash_before
    res["output_model"] = str(output_path.resolve())
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def build_re100_benchmark(
    baseline_path: Path | str,
    output_path: Path | str,
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build independent low-blockage Re=100 cylinder benchmark case from baseline template.

    Enforces baseline model immutability, atomic staging, and isolation.
    """
    baseline_path = Path(baseline_path)
    output_path = Path(output_path)

    if not baseline_path.is_file():
        raise ModelNotFoundError(f"Source baseline model file does not exist: {baseline_path}")

    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    if baseline_path.resolve() == output_path.resolve():
        raise ModelValidationError(
            "Refusing to overwrite baseline model: source and output path are identical"
        )

    hash_before = compute_file_hash(baseline_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"

    args = [
        "build-re100-case",
        str(baseline_path.resolve()),
        str(staging_path.resolve()),
    ]

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise

        hash_after = compute_file_hash(baseline_path)
        if hash_before != hash_after:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise ModelValidationError(
                f"Safety violation: original model file was modified during benchmark building! "
                f"Original SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifact quarantined; output file was NOT published."
            )

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    res["source_sha256"] = hash_before
    res["output_model"] = str(output_path.resolve())
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def build_c04_benchmark(
    baseline_path: Path | str,
    output_path: Path | str,
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build independent C04 3D cylinder benchmark case from baseline official model.

    Enforces baseline model immutability, atomic staging, and isolation.
    """
    baseline_path = Path(baseline_path)
    output_path = Path(output_path)

    if not baseline_path.is_file():
        raise ModelNotFoundError(f"Source baseline model file does not exist: {baseline_path}")

    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    if baseline_path.resolve() == output_path.resolve():
        raise ModelValidationError(
            "Refusing to overwrite baseline model: source and output path are identical"
        )

    hash_before = compute_file_hash(baseline_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"

    args = [
        "build-c04-case",
        str(baseline_path.resolve()),
        str(staging_path.resolve()),
    ]

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise

        hash_after = compute_file_hash(baseline_path)
        if hash_before != hash_after:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise ModelValidationError(
                f"Safety violation: original model file was modified during benchmark building! "
                f"Original SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifact quarantined; output file was NOT published."
            )

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    res["source_sha256"] = hash_before
    res["output_model"] = str(output_path.resolve())
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def build_c04_mesh2(
    model_path: Path | str,
    output_path: Path | str,
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build tuned 1.55M element mesh2 on C04 model with staging isolation."""
    model_path = Path(model_path)
    output_path = Path(output_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")
    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    hash_before = compute_file_hash(model_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_path.name}"

    args = [
        "build-c04-mesh2",
        str(model_path.resolve()),
        str(staging_path.resolve()),
    ]

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise

        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            quarantine_staging_file(staging_path, output_path, stage_id, reason="failed")
            raise ModelValidationError(
                f"Safety violation: original model file was modified during mesh2 building! "
                f"Original SHA-256 changed from {hash_before} to {hash_after}."
            )

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    res["source_sha256"] = hash_before
    res["output_model"] = str(output_path.resolve())
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def export_c04_slice_dataset(
    model_path: Path | str,
    output_npz: Path | str,
    time_s: float = 0.0,
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> Path:
    """Export 2D mid-span slice (192 x 96) from solved C04 model to NPZ dataset."""
    from ..sampling_3d import create_c04_2d_slice_grid, save_c04_dataset

    model_path = Path(model_path)
    output_npz = Path(output_npz)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file not found: {model_path}")
    if output_npz.is_file() and not allow_overwrite:
        raise ModelValidationError(f"Refusing to overwrite existing dataset: {output_npz}")

    temp_txt = output_npz.parent / f".tmp_{uuid.uuid4().hex[:8]}_slice.txt"
    try:
        temp_txt.parent.mkdir(parents=True, exist_ok=True)
        args = ["export-c04-slice", str(model_path.resolve()), str(temp_txt.resolve())]
        _run_bridge(args, root=root)

        lines = temp_txt.read_text(encoding="utf-8").strip().splitlines()
        data_rows = []
        for line in lines:
            if line.startswith("%"):
                continue
            parts = line.split()
            if len(parts) >= 6:
                data_rows.append([float(x) for x in parts])

        arr = np.array(data_rows, dtype=np.float32)
        grid = create_c04_2d_slice_grid(D=0.30)
        fields = np.zeros((1, 4, 96, 192), dtype=np.float32)

        # Map to channels [u, v_transverse, w_spanwise, p]
        fields[0, 0] = arr[:, 2].reshape((96, 192))
        fields[0, 1] = arr[:, 4].reshape((96, 192))
        fields[0, 2] = arr[:, 3].reshape((96, 192))
        fields[0, 3] = arr[:, 5].reshape((96, 192))

        # Enforce cylinder solid mask & clean NaNs
        fields[0, :, grid.solid_mask] = 0.0
        fields = np.nan_to_num(fields, nan=0.0)

        times = np.array([time_s], dtype=np.float32)
        save_c04_dataset(output_npz, fields, grid, times)
        return output_npz
    finally:
        temp_txt.unlink(missing_ok=True)


def export_c04_volume_dataset(
    model_path: Path | str,
    output_npz: Path | str,
    time_s: float = 0.0,
    allow_overwrite: bool = False,
    root: Path | None = None,
) -> Path:
    """Export 3D regular volume (192 x 96 x 64) from solved C04 model to NPZ dataset."""
    from ..sampling_3d import create_c04_sampling_grid, save_c04_dataset

    model_path = Path(model_path)
    output_npz = Path(output_npz)
    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file not found: {model_path}")
    if output_npz.is_file() and not allow_overwrite:
        raise ModelValidationError(f"Refusing to overwrite existing dataset: {output_npz}")

    temp_txt = output_npz.parent / f".tmp_{uuid.uuid4().hex[:8]}_vol.txt"
    try:
        temp_txt.parent.mkdir(parents=True, exist_ok=True)
        args = ["export-c04-volume", str(model_path.resolve()), str(temp_txt.resolve())]
        _run_bridge(args, root=root)

        data = np.loadtxt(temp_txt, comments="%", dtype=np.float32)
        grid = create_c04_sampling_grid(D=0.30)
        fields = np.zeros((1, 4, 64, 96, 192), dtype=np.float32)

        # data cols: 0=x, 1=y(span), 2=z(transverse), 3=u, 4=v(span), 5=w(transverse), 6=p
        fields[0, 0] = data[:, 3].reshape((64, 96, 192))
        fields[0, 1] = data[:, 5].reshape((64, 96, 192))
        fields[0, 2] = data[:, 4].reshape((64, 96, 192))
        fields[0, 3] = data[:, 6].reshape((64, 96, 192))

        fields[0, :, grid.solid_mask] = 0.0
        fields = np.nan_to_num(fields, nan=0.0)

        times = np.array([time_s], dtype=np.float32)
        save_c04_dataset(output_npz, fields, grid, times)
        return output_npz
    finally:
        temp_txt.unlink(missing_ok=True)



def export_solution_data(
    model_path: Path | str,
    output_txt_path: Path | str,
    allow_overwrite: bool = False,
    root: Path | None = None,
    expected_times: Any = None,
) -> dict[str, Any]:
    """Export solution field data (u, v, p, vx-uy, spf.U) to text file with staging transaction protocol.

    Publication gate (all must hold, otherwise the staging artifact is
    quarantined and the final path is left untouched):
    1. COMSOL bridge reports SUCCESS.
    2. Source model SHA-256 unchanged.
    3. Staging file passes :func:`hydro_disturb_bench.io.validate_comsol_export`
       (header, x/y, per-time completeness, unique expr-time columns, units,
       column counts, non-zero rows, finite samples; optional ``expected_times``).
    4. Atomic no-overwrite publish (unless ``allow_overwrite``).

    The returned dict additionally carries ``validation`` (contract summary).
    """
    from ..io import ComsolExportValidationError, validate_comsol_export

    model_path = Path(model_path)
    output_txt_path = Path(output_txt_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file does not exist: {model_path}")

    if output_txt_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_txt_path}")

    # Compute source model hash before execution
    hash_before = compute_file_hash(model_path)

    output_txt_path.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_txt_path.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_path = staging_dir / f"stage_{stage_id}_{output_txt_path.name}"

    args = [
        "export-solution-data",
        str(model_path.resolve()),
        str(staging_path.resolve()),
    ]

    with task_staging_session(staging_path, stage_id):
        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_file(staging_path, output_txt_path, stage_id, reason="failed")
            raise

        # Verify source model hash integrity before publishing
        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            quarantine_staging_file(
                staging_path, output_txt_path, stage_id, reason="source_mutated"
            )
            raise ModelValidationError(
                f"Safety violation: source model file was modified during data export! "
                f"Source SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifact quarantined; output file was NOT published."
            )

        # Pre-publish validation: staging file must exist, be non-empty, and contain valid text data
        if not staging_path.is_file() or staging_path.stat().st_size == 0:
            if staging_path.is_file():
                quarantine_staging_file(staging_path, output_txt_path, stage_id, reason="empty")
            raise ModelValidationError(
                f"Staging export file is missing or empty before publish: {staging_path}"
            )

        # Full data-contract admission check on the staging artifact (never on final)
        try:
            validation = validate_comsol_export(staging_path, expected_times=expected_times)
        except ComsolExportValidationError as err:
            quarantine_staging_file(staging_path, output_txt_path, stage_id, reason="invalid_data")
            raise ModelValidationError(
                f"Staging export failed data contract validation; output file was NOT published: {err}"
            ) from err

        publish_staging_file(
            staging_path=staging_path,
            output_path=output_txt_path,
            stage_id=stage_id,
            allow_overwrite=allow_overwrite,
        )

    validation["path"] = str(output_txt_path.resolve())
    res["validation"] = validation
    res["source_sha256"] = hash_before
    res["output_txt"] = str(output_txt_path.resolve())
    res["output_sha256"] = compute_file_hash(output_txt_path)
    return res


def export_full_field_sample(
    model_path: Path | str,
    output_dir: Path | str,
    requested_times=(180.0, 185.0, 190.0, 195.0, 200.0),
    grid_shapes=((81, 49), (161, 97)),
    bounds=(0.0, 0.4, 0.0, 0.24),
    allow_overwrite: bool = False,
    root: Path | None = None,
    *,
    process_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict[str, Any]:
    """Read an existing solution into two validated Cartesian sample archives."""
    from ..field_sampling import export_full_field_sample as sample

    process_options = {} if process_runner is None else {"process_runner": process_runner}
    return sample(
        model_path,
        output_dir,
        requested_times,
        grid_shapes,
        bounds,
        allow_overwrite=allow_overwrite,
        root=root,
        **process_options,
    )


def _archive_existing_diagnostic_set(output_dir: Path) -> str | None:
    """If output_dir contains an existing complete diagnostic result set, copy it into versions/ archive.

    Returns the version tag string (e.g. 'run_20261103_142832_7f7cb4'), or None if no prior results exist.
    """
    from ..io import S1_REQUIRED_SERIES, S1_REQUIRED_SNAPSHOTS

    all_keys = list(S1_REQUIRED_SERIES) + list(S1_REQUIRED_SNAPSHOTS) + ["manifest.json"]
    existing_files = [f for f in all_keys if (output_dir / f).is_file()]
    if not existing_files:
        return None

    # Determine old version tag from old manifest.json or timestamp
    old_manifest_file = output_dir / "manifest.json"
    old_stage_id = "legacy"
    old_timestamp = time.strftime("%Y%m%d_%H%M%S")
    if old_manifest_file.is_file():
        try:
            with open(old_manifest_file, encoding="utf-8") as mf:
                data = json.load(mf)
                old_stage_id = str(data.get("stage_id", "legacy"))[:8]
                exp_at = data.get("exported_at", "")
                if exp_at:
                    digits = re.sub(r"[^0-9]", "", exp_at)
                    if len(digits) >= 14:
                        old_timestamp = digits[:14]
        except Exception:
            pass

    versions_parent = output_dir / "versions"
    versions_parent.mkdir(parents=True, exist_ok=True)
    version_tag = f"run_{old_timestamp}_{old_stage_id}"
    version_dir = versions_parent / version_tag

    counter = 0
    while version_dir.exists():
        counter += 1
        version_dir = versions_parent / f"{version_tag}_{counter}"

    version_dir.mkdir(parents=True, exist_ok=True)
    for fname in existing_files:
        src = output_dir / fname
        dst = version_dir / fname
        shutil.copy2(str(src), str(dst))

    return version_dir.name


def list_s1_diagnostic_versions(output_dir: Path | str) -> list[dict[str, Any]]:
    """List all archived diagnostic versions under output_dir/versions."""
    out_dir = Path(output_dir)
    versions_dir = out_dir / "versions"
    if not versions_dir.is_dir():
        return []

    records = []
    for d in sorted(versions_dir.iterdir()):
        if d.is_dir() and d.name.startswith("run_"):
            manifest_file = d / "manifest.json"
            meta: dict[str, Any] = {}
            if manifest_file.is_file():
                try:
                    with open(manifest_file, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
            source_info = meta.get("source_model", {})
            src_sha = source_info.get("sha256", "") if isinstance(source_info, dict) else ""
            records.append(
                {
                    "version_tag": d.name,
                    "path": str(d.resolve()),
                    "has_manifest": manifest_file.is_file(),
                    "status": meta.get("status", "UNKNOWN"),
                    "exported_at": meta.get("exported_at", ""),
                    "source_sha256": src_sha,
                }
            )
    return records


def recover_s1_diagnostic_version(
    output_dir: Path | str,
    version_tag: str,
    target_dir: Path | str | None = None,
) -> Path:
    """Recover an archived diagnostic version to target_dir (or restore it to output_dir)."""
    out_dir = Path(output_dir)
    version_dir = out_dir / "versions" / version_tag
    if not version_dir.is_dir():
        raise FileNotFoundError(f"Version {version_tag} not found in {out_dir / 'versions'}")

    dest = Path(target_dir) if target_dir is not None else out_dir
    dest.mkdir(parents=True, exist_ok=True)

    for item in version_dir.iterdir():
        if item.is_file():
            shutil.copy2(str(item), str(dest / item.name))
    return dest


def export_s1_diagnostics(
    model_path: Path | str,
    output_dir: Path | str,
    allow_overwrite: bool = False,
    root: Path | None = None,
    expected_steps: int | None = None,
    expected_dt: float = 0.20,
    expected_t_final: float = 100.0,
    versioned: bool = True,
    snapshot_times: list[float] | None = None,
    required_snapshots: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Export Stage S1 diagnostic time-series (lift, drag, wake, inlet, stag) and vorticity snapshots.

    Publication gate (atomic, fail closed):
    1. COMSOL bridge reports SUCCESS into staging directory.
    2. Source model SHA-256 unchanged.
    3. Staging directory passes validate_s1_diagnostics (aligned steps, no NaN/Inf, snapshots valid).
    4. Completion manifest.json generated in staging directory.
    5. Under cross-process file_publish_lock:
       - If destination already contains results and allow_overwrite is False: quarantine as conflict and raise FileExistsError.
       - If destination contains results and allow_overwrite is True: archive existing results to versions/ before replacing.
       - Stage each file as .tmp_<stage_id>_<fname> in output_dir. If any file fails, clean up .tmp_ files and quarantine staging!
       - Atomically promote .tmp_ files to final names (diagnostic files first, manifest.json last as the commit token).
    """
    from ..io import (
        S1_A2_REQUIRED_SNAPSHOTS,
        S1_REQUIRED_SERIES,
        S1_REQUIRED_SNAPSHOTS,
        ComsolExportValidationError,
        build_s1_manifest,
        validate_s1_diagnostics,
    )

    model_path = Path(model_path)
    output_dir = Path(output_dir)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Model file does not exist: {model_path}")

    if expected_steps is None:
        expected_steps = int(round(expected_t_final / expected_dt)) + 1

    if required_snapshots is None:
        if snapshot_times is not None:
            required_snapshots = tuple(
                f"vorticity_t{int(round(t))}s_comsol.png" for t in snapshot_times
            )
        elif expected_t_final > 105.0:
            required_snapshots = S1_A2_REQUIRED_SNAPSHOTS
        else:
            required_snapshots = S1_REQUIRED_SNAPSHOTS

    output_dir.mkdir(parents=True, exist_ok=True)
    hash_before = compute_file_hash(model_path)

    staging_parent = output_dir.parent / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    stage_id = uuid.uuid4().hex[:12]
    staging_dir = staging_parent / f"stage_{stage_id}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    lease_file = staging_parent / f".lease_{stage_id}.lock"

    with task_staging_session(lease_file, stage_id):
        args = [
            "export-s1-diagnostics",
            str(model_path.resolve()),
            str(staging_dir.resolve()),
        ]
        if snapshot_times is not None:
            args.append(",".join(str(t) for t in snapshot_times))

        try:
            res = _run_bridge(args, root=root)
        except Exception:
            quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="failed")
            raise

        hash_after = compute_file_hash(model_path)
        if hash_before != hash_after:
            quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="source_mutated")
            raise ModelValidationError(
                f"Safety violation: source model file was modified during diagnostic export! "
                f"Source SHA-256 changed from {hash_before} to {hash_after}. "
                f"Staging artifacts quarantined; outputs were NOT published."
            )

        # Validate all staging files before publishing
        try:
            val_summary = validate_s1_diagnostics(
                staging_dir,
                expected_steps=expected_steps,
                expected_dt=expected_dt,
                expected_t_final=expected_t_final,
                require_manifest=False,
                required_snapshots=required_snapshots,
            )
        except ComsolExportValidationError as err:
            quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="invalid_data")
            raise ModelValidationError(
                f"Staging diagnostics failed data contract validation; outputs were NOT published: {err}"
            ) from err

        # Build completion manifest inside staging directory
        manifest_data = build_s1_manifest(
            staging_dir=staging_dir,
            source_model_path=model_path,
            source_sha256=hash_before,
            stage_id=stage_id,
            validation_summary=val_summary,
            required_snapshots=required_snapshots,
        )
        manifest_file = staging_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Acquire cross-process publish lock
        lock_file = output_dir.parent / f".publish_{output_dir.name}.lock"
        with file_publish_lock(lock_file):
            all_files_to_check = (
                list(S1_REQUIRED_SERIES) + list(required_snapshots) + ["manifest.json"]
            )
            existing_present = [f for f in all_files_to_check if (output_dir / f).is_file()]

            if existing_present and not allow_overwrite:
                quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="conflict")
                raise FileExistsError(
                    f"Refusing to overwrite existing diagnostic result set without allow_overwrite=True in: {output_dir}"
                )

            superseded_version = None
            if existing_present and allow_overwrite and versioned:
                superseded_version = _archive_existing_diagnostic_set(output_dir)
                if superseded_version:
                    manifest_data["superseded_version"] = superseded_version
                    manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

            # Transactional staged transfer: stage files as .tmp_<stage_id>_<fname> in output_dir
            all_publish_items = (
                list(S1_REQUIRED_SERIES) + list(required_snapshots) + ["manifest.json"]
            )
            transferred_tmp_files: list[Path] = []
            try:
                for fname in all_publish_items:
                    src_f = staging_dir / fname
                    if not src_f.is_file():
                        raise ComsolExportValidationError(
                            f"Missing staging artifact for publish: {fname}"
                        )
                    tmp_dest = output_dir / f".tmp_{stage_id}_{fname}"
                    shutil.copy2(str(src_f), str(tmp_dest))
                    transferred_tmp_files.append(tmp_dest)
            except Exception:
                for tf in transferred_tmp_files:
                    try:
                        tf.unlink(missing_ok=True)
                    except OSError:
                        pass
                quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="failed_publish")
                raise

            # Promote .tmp_ files to final names atomically
            promoted_files: list[str] = []
            try:
                # Diagnostic artifacts first
                for fname in list(S1_REQUIRED_SERIES) + list(required_snapshots):
                    tmp_dest = output_dir / f".tmp_{stage_id}_{fname}"
                    final_dest = output_dir / fname
                    os.replace(str(tmp_dest), str(final_dest))
                    promoted_files.append(fname)

                # manifest.json LAST as commit token
                tmp_manifest = output_dir / f".tmp_{stage_id}_manifest.json"
                final_manifest = output_dir / "manifest.json"
                os.replace(str(tmp_manifest), str(final_manifest))
                promoted_files.append("manifest.json")
            except Exception:
                for tf in transferred_tmp_files:
                    try:
                        tf.unlink(missing_ok=True)
                    except OSError:
                        pass
                quarantine_staging_dir(staging_dir, output_dir, stage_id, reason="failed_promote")
                raise

        # Clean up empty staging dir
        shutil.rmtree(staging_dir, ignore_errors=True)

    res["status"] = "SUCCESS"
    res["stage_id"] = stage_id
    res["validation"] = val_summary
    res["source_sha256"] = hash_before
    res["output_dir"] = str(output_dir.resolve())
    res["manifest_file"] = str((output_dir / "manifest.json").resolve())
    res["published_files"] = promoted_files
    if superseded_version:
        res["superseded_version"] = superseded_version
    return res
