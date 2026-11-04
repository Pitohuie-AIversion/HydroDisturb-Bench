"""COMSOL Java API Bridge for HydroDisturb-Bench.

Provides programmatic access to COMSOL Multiphysics using the official
Java API and comsolbatch/comsolcompile tools, guaranteeing baseline
model immutability, parameter validation, and detailed execution logging.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import winreg
else:
    winreg = None  # type: ignore[assignment]


# Error code constants matching project contract
ERR_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
ERR_API_UNSUPPORTED = "API_UNSUPPORTED"
ERR_NODE_NOT_FOUND = "NODE_NOT_FOUND"
ERR_INVALID_PARAMETER = "INVALID_PARAMETER"
ERR_INVALID_UNIT = "INVALID_UNIT"
ERR_COMSOL_CONNECTION_FAILED = "COMSOL_CONNECTION_FAILED"
ERR_MODEL_VALIDATION_FAILED = "MODEL_VALIDATION_FAILED"
ERR_SOLVER_FAILED = "SOLVER_FAILED"


class ComsolError(Exception):
    """Base exception for COMSOL bridge operations."""

    def __init__(self, error_code: str, message: str, details: str = "") -> None:
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message
        self.details = details


class ModelNotFoundError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_MODEL_NOT_FOUND, message, details)


class ApiUnsupportedError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_API_UNSUPPORTED, message, details)


class NodeNotFoundError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_NODE_NOT_FOUND, message, details)


class InvalidParameterError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_INVALID_PARAMETER, message, details)


class InvalidUnitError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_INVALID_UNIT, message, details)


class ComsolConnectionError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_COMSOL_CONNECTION_FAILED, message, details)


class ModelValidationError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_MODEL_VALIDATION_FAILED, message, details)


class SolverFailedError(ComsolError):
    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(ERR_SOLVER_FAILED, message, details)


def map_error(error_code: str, message: str, details: str = "") -> ComsolError:
    """Map error code string to typed exception."""
    mapping = {
        ERR_MODEL_NOT_FOUND: ModelNotFoundError,
        ERR_API_UNSUPPORTED: ApiUnsupportedError,
        ERR_NODE_NOT_FOUND: NodeNotFoundError,
        ERR_INVALID_PARAMETER: InvalidParameterError,
        ERR_INVALID_UNIT: InvalidUnitError,
        ERR_COMSOL_CONNECTION_FAILED: ComsolConnectionError,
        ERR_MODEL_VALIDATION_FAILED: ModelValidationError,
        ERR_SOLVER_FAILED: SolverFailedError,
    }
    cls = mapping.get(error_code, ComsolError)
    if cls is ComsolError:
        return ComsolError(error_code, message, details)
    return cls(message, details)


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


def ensure_bridge_compiled(root: Path | None = None) -> Path:
    """Ensure ComsolBridge.class is compiled and up-to-date."""
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

    needs_compile = False
    if not java_class.is_file():
        needs_compile = True
    elif java_src.stat().st_mtime > java_class.stat().st_mtime:
        needs_compile = True

    if needs_compile:
        cmd = [str(comsolcompile), str(java_src)]
        proc = subprocess.run(
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
            raise ComsolConnectionError(
                f"Failed to compile ComsolBridge.java (exit code {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
            )

    return java_class


def _run_standalone(root: Path, class_file: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Use the official single-process Java API after a native batch startup failure.

    DLL search changes affect only this JVM. ASCII temporary class paths and
    base64 UTF-8 arguments avoid the bundled Java launcher's Windows argv loss.
    No model is copied and no installed file is changed.
    """
    java_bin = root / "java" / "win64" / "jre" / "bin"
    with tempfile.TemporaryDirectory(prefix="hydrobench_comsol_") as temp:
        work = Path(temp)
        if not str(work).isascii() or not str(root).isascii():
            raise ComsolConnectionError("Standalone Java launcher requires ASCII temp and installation paths")
        shutil.copy2(class_file, work / "ComsolBridge.class")
        source = work / "ComsolStandalone.java"
        shutil.copy2(Path(__file__).with_name("ComsolStandalone.java"), source)
        classpath = str(root / "plugins" / "*") + os.pathsep + str(work)
        compiled = subprocess.run(
            [str(java_bin / "javac.exe"), "-cp", classpath, str(source)],
            cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if compiled.returncode != 0:
            raise ComsolConnectionError(f"Standalone launcher compilation failed: {compiled.stderr}")
        encoded = [base64.b64encode(value.encode("utf-8")).decode("ascii")
                   for value in [str(root.resolve()), *args]]
        return subprocess.run(
            [str(java_bin / "java.exe"), "-Xmx2g", "-Xss4m", "-cp", classpath,
             "ComsolStandalone", *encoded],
            cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )


def _run_bridge(args: list[str], root: Path | None = None) -> dict[str, Any]:
    """Execute ComsolBridge with given arguments and parse JSON result."""
    root = root or find_comsol_root()
    bin_dir = get_comsol_bin_dir(root)
    comsolbatch = bin_dir / ("comsolbatch.exe" if sys.platform == "win32" else "comsolbatch")
    if not comsolbatch.is_file():
        raise ComsolConnectionError(f"comsolbatch executable not found at {comsolbatch}")

    class_file = ensure_bridge_compiled(root)
    cmd = [str(comsolbatch), "-nosave", "-inputfile", str(class_file)] + args

    proc = subprocess.run(
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
    if (sys.platform == "win32" and "<<<JSON_START>>>" not in (proc.stdout or "")
            and "Failed to start COMSOL" in (proc.stdout or "")
            and "FlNativeException" in (proc.stdout or "")):
        startup_failure = {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        proc = _run_standalone(root, class_file, args)
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
    if startup_failure is not None:
        data["batch_startup_failure"] = startup_failure
    return data


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



def is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is currently alive on the host system."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                kernel32.CloseHandle(handle)
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def is_task_active(lease_file: Path | str) -> bool:
    """Check whether a staging lease file corresponds to a currently running task."""
    lease_path = Path(lease_file)
    if not lease_path.is_file():
        return False
    try:
        f = open(lease_path, "a+b")
    except OSError:
        # File is locked exclusively by another process, definitely active!
        return True

    try:
        f.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        # Lock was acquirable without blocking; now check PID from metadata as second defense
        try:
            f.seek(0)
            content = f.read().decode("utf-8", errors="ignore").strip()
            if content:
                meta = json.loads(content)
                pid = meta.get("pid")
                if pid and is_pid_alive(int(pid)):
                    return True
        except Exception:
            pass
        return False
    except (OSError, BlockingIOError):
        # File lock could not be acquired: another process is actively holding it!
        return True
    finally:
        try:
            f.close()
        except OSError:
            pass


@contextlib.contextmanager
def task_staging_session(staging_path: Path | str, stage_id: str):
    """Manage lifecycle and active lease lock for a staging task.

    Creates and holds an exclusive non-blocking lock on `.lease_{stage_id}.lock`.
    While the task process is running, this lock remains held by the operating system kernel.
    If the process terminates, crashes, or is killed, the operating system kernel unconditionally
    releases the file lock.
    """
    staging_path = Path(staging_path)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    lease_file = staging_path.parent / f".lease_{stage_id}.lock"

    f = open(lease_file, "a+b")
    locked = False
    try:
        f.seek(0)
        f.truncate(0)
        meta = json.dumps({"pid": os.getpid(), "stage_id": stage_id, "start_time": time.time()})
        f.write(meta.encode("utf-8"))
        f.flush()
        f.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    finally:
        if locked:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            f.close()
        except OSError:
            pass
        try:
            lease_file.unlink(missing_ok=True)
        except OSError:
            pass


def _is_file_locked(file_path: Path) -> bool:
    """Check if a file is currently open or locked by an active process."""
    file_path = Path(file_path)
    if not file_path.is_file():
        return False
    if sys.platform == "win32":
        try:
            with open(file_path, "r+b") as test_f:
                import msvcrt

                try:
                    msvcrt.locking(test_f.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(test_f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    return True
            return False
        except (PermissionError, OSError):
            return True
    else:
        try:
            with open(file_path, "r+b") as test_f:
                import fcntl

                try:
                    fcntl.flock(test_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(test_f.fileno(), fcntl.LOCK_UN)
                except (BlockingIOError, OSError):
                    return True
            return False
        except (PermissionError, OSError):
            return True


def _safe_quarantine_move(src_path: Path, quarantine_dir: Path, base_filename: str) -> Path:
    """Move src_path into quarantine_dir under a unique name, strictly forbidding overwrite.

    Safety Guarantees:
    - Never overwrites an existing diagnostic artifact.
    - If base_filename already exists or races, appends timestamp and monotonic counter.
    - Preserves src_path completely in place if moving fails.
    """
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    src_path = Path(src_path)

    path_obj = Path(base_filename)
    stem = path_obj.stem
    suffix = path_obj.suffix
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    counter = 0
    while True:
        if counter == 0:
            target = quarantine_dir / base_filename
        else:
            target = quarantine_dir / f"{stem}_{timestamp}_{counter}{suffix}"

        if target.exists():
            counter += 1
            continue

        try:
            if sys.platform == "win32":
                os.rename(src_path, target)
            else:
                try:
                    os.link(src_path, target)
                    src_path.unlink()
                except OSError as err:
                    if err.errno == errno.EEXIST or target.exists():
                        counter += 1
                        continue
                    _posix_rename_noreplace(src_path, target, err)
            return target
        except FileExistsError:
            counter += 1
            continue


def quarantine_staging_file(
    staging_path: Path | str,
    output_path: Path | str,
    stage_id: str,
    reason: str = "failed",
) -> Path | None:
    """Move a staging file into quarantine to isolate failures or conflicts.

    Guarantees:
    - Strictly forbids overwriting existing diagnostic artifacts.
    - If moving fails (e.g., target disk full, permission denied), staging_path is
      STRICTLY PRESERVED in place (never unlinked) and a warning is logged to sys.stderr.

    Args:
        staging_path: Path to the temporary staging artifact.
        output_path: Intended final output path (used to determine parent dir and filename).
        stage_id: Unique task identifier.
        reason: 'failed' (execution failure) or 'conflict' (destination collision).

    Returns:
        Path to the quarantined file, or None if moving failed or staging_path did not exist.
    """
    staging_path = Path(staging_path)
    if not staging_path.is_file():
        return None
    quarantine_dir = Path(output_path).parent / "quarantine"
    base_name = f"{reason}_{stage_id}_{Path(output_path).name}"
    try:
        return _safe_quarantine_move(staging_path, quarantine_dir, base_name)
    except OSError as err:
        # Strictly preserve staging file in place. NEVER unlink on quarantine failure!
        sys.stderr.write(
            f"[QUARANTINE_WARNING] Failed to quarantine staging file {staging_path} to {quarantine_dir / base_name}: {err}. "
            f"Artifact retained in staging directory.\n"
        )
        return None


def _safe_quarantine_move_dir(staging_dir: Path, quarantine_dir: Path, base_dirname: str) -> Path:
    """Move staging_dir into quarantine_dir under a unique directory name, strictly forbidding overwrite.

    Safety Guarantees:
    - Never overwrites an existing diagnostic directory or artifact.
    - If base_dirname already exists, appends timestamp and monotonic counter.
    - Preserves staging_dir completely in place if moving fails.
    """
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(staging_dir)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    counter = 0
    while True:
        if counter == 0:
            target = quarantine_dir / base_dirname
        else:
            target = quarantine_dir / f"{base_dirname}_{timestamp}_{counter}"

        if target.exists():
            counter += 1
            continue

        try:
            shutil.move(str(staging_dir), str(target))
            return target
        except OSError as err:
            if target.exists():
                counter += 1
                continue
            sys.stderr.write(
                f"[QUARANTINE_WARNING] Failed to move staging dir {staging_dir} to {target}: {err}. "
                f"Directory retained in place.\n"
            )
            return staging_dir


def quarantine_staging_dir(
    staging_dir: Path | str,
    output_dir: Path | str,
    stage_id: str,
    reason: str = "failed",
) -> Path | None:
    """Safely move a multi-file staging directory to quarantine upon failure, conflict, or corruption.

    Guarantees:
    - Staging files are NEVER deleted with ignore_errors=True.
    - If moving fails, the staging directory is preserved in place.
    """
    staging_dir = Path(staging_dir)
    if not staging_dir.is_dir():
        return None
    quarantine_dir = Path(output_dir).parent / "quarantine"
    base_name = f"{reason}_{stage_id}_{Path(output_dir).name}"
    try:
        return _safe_quarantine_move_dir(staging_dir, quarantine_dir, base_name)
    except OSError as err:
        sys.stderr.write(
            f"[QUARANTINE_WARNING] Failed to quarantine staging dir {staging_dir}: {err}. "
            f"Directory retained in place.\n"
        )
        return None


@contextlib.contextmanager
def file_publish_lock(
    lock_path: Path | str,
    timeout_sec: float = 30.0,
    poll_interval: float = 0.05,
):
    """Acquire a cross-process lock for publishing files safely.

    Uses `msvcrt.locking` on Windows and `fcntl.flock` on POSIX systems.
    Prevents concurrent processes from overwriting or corrupting the destination.
    """
    lock_path = Path(lock_path)
    if lock_path.is_dir() or (not lock_path.suffix and lock_path.exists()):
        lock_file = lock_path.parent / f".publish_{lock_path.name}.lock"
    else:
        lock_file = lock_path
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file, "a+b")
    start_time = time.monotonic()
    locked = False
    try:
        while True:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, BlockingIOError):
                if time.monotonic() - start_time >= timeout_sec:
                    raise TimeoutError(
                        f"Timed out after {timeout_sec:.1f}s waiting for publish lock on: {lock_path}"
                    )
                time.sleep(poll_interval)
        yield
    finally:
        if locked:
            try:
                f.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            f.close()
        except OSError:
            pass


def _atomic_publish_new_file(staging_path: Path, output_path: Path, stage_id: str) -> None:
    """Atomically publish staging_path to output_path if output_path does NOT exist."""
    if sys.platform == "win32":
        # Windows: os.rename fails with FileExistsError (WinError 183) if output_path exists
        os.rename(staging_path, output_path)
    else:
        # POSIX / Linux: link() is an atomic kernel operation that fails with EEXIST if output_path exists
        try:
            os.link(staging_path, output_path)
            staging_path.unlink()
        except FileExistsError:
            raise
        except OSError as err:
            if err.errno == errno.EEXIST or output_path.exists():
                raise FileExistsError(
                    f"Destination output file already exists: {output_path}"
                ) from err
            _posix_rename_noreplace(staging_path, output_path, err)


def _posix_rename_noreplace(
    staging_path: Path, output_path: Path, original_err: Exception | None = None
) -> None:
    """Linux kernel SYS_renameat2 with RENAME_NOREPLACE (flag 1).

    Fails closed if atomic no-overwrite cannot be guaranteed, strictly avoiding
    TOCTOU race conditions from replace() fallbacks.
    """
    if output_path.exists():
        raise FileExistsError(f"Destination output file already exists: {output_path}")

    try:
        if sys.platform == "win32":
            raise OSError("SYS_renameat2 is not supported on Windows")
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        SYS_renameat2 = 316 if sys.maxsize > 2**32 else 353
        if hasattr(os, "uname") and os.uname().machine.startswith("aarch64"):
            SYS_renameat2 = 276
        AT_FDCWD = -100
        RENAME_NOREPLACE = 1
        ret = libc.syscall(
            SYS_renameat2,
            AT_FDCWD,
            os.fsencode(staging_path),
            AT_FDCWD,
            os.fsencode(output_path),
            RENAME_NOREPLACE,
        )
        if ret != 0:
            errno_val = ctypes.get_errno()
            if errno_val == errno.EEXIST:
                raise FileExistsError(f"Destination output file already exists: {output_path}")
            raise OSError(errno_val, f"SYS_renameat2 failed: {os.strerror(errno_val)}")
    except (AttributeError, OSError) as err:
        if output_path.exists():
            raise FileExistsError(f"Destination output file already exists: {output_path}") from (
                original_err or err
            )
        # Fail closed: never call staging_path.replace(output_path), which introduces TOCTOU overwrite race.
        raise FileExistsError(
            f"Atomic no-overwrite publish failed or is unsupported on destination: {output_path} ({err})"
        ) from (original_err or err)


def publish_staging_file(
    staging_path: Path | str,
    output_path: Path | str,
    stage_id: str,
    allow_overwrite: bool = False,
    lock_timeout_sec: float = 30.0,
) -> None:
    """Atomically publish a verified staging file to final output_path.

    Enforces:
    1. Cross-process publication synchronization using a dedicated lock file.
    2. Symbolic link rejection on destination path.
    3. Default forbid overwrite (`allow_overwrite=False`).
    4. Conflict isolation: if destination already exists and overwrite is forbidden,
       moves staging file to quarantine/conflict_{stage_id}_{output_path.name}
       and raises FileExistsError (preserving pre-existing destination and staging data).
    5. Clean atomic publication via atomic filesystem primitives on both Windows and POSIX.
    """
    output_path = Path(output_path)
    staging_path = Path(staging_path)

    if output_path.is_symlink():
        raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

    if not staging_path.is_file() or staging_path.stat().st_size == 0:
        if staging_path.is_file():
            quarantine_staging_file(staging_path, output_path, stage_id, reason="empty")
        raise ModelValidationError(
            f"Staging model file is missing or empty before publish: {staging_path}"
        )

    lock_dir = output_path.parent / ".locks"
    lock_file = lock_dir / f"{output_path.name}.lock"

    with file_publish_lock(lock_file, timeout_sec=lock_timeout_sec):
        # Re-check symlink inside lock in case created concurrently
        if output_path.is_symlink():
            raise ModelValidationError(f"Refusing to write to symbolic link: {output_path}")

        if output_path.exists():
            if not allow_overwrite:
                conflict_target = quarantine_staging_file(
                    staging_path, output_path, stage_id, reason="conflict"
                )
                raise FileExistsError(
                    f"Destination output file already exists and overwrite is forbidden: {output_path}. "
                    f"Staging artifact preserved in quarantine at {conflict_target}"
                )
            staging_path.replace(output_path)
        else:
            # Destination does not exist yet: atomic rename / move without overwrite
            try:
                _atomic_publish_new_file(staging_path, output_path, stage_id)
            except FileExistsError:
                if not allow_overwrite:
                    conflict_target = quarantine_staging_file(
                        staging_path, output_path, stage_id, reason="conflict"
                    )
                    raise FileExistsError(
                        f"Destination output file already exists and overwrite is forbidden: {output_path}. "
                        f"Staging artifact preserved in quarantine at {conflict_target}"
                    )
                staging_path.replace(output_path)


def list_quarantine_records(directory: Path | str) -> list[dict[str, Any]]:
    """List all quarantined files in the given directory or its 'quarantine' subdirectory."""
    p = Path(directory)
    q_dir = p if p.name == "quarantine" else p / "quarantine"
    if not q_dir.is_dir():
        return []
    records = []
    for f in sorted(q_dir.iterdir()):
        if f.is_file():
            kind = (
                "conflict"
                if f.name.startswith("conflict_")
                else ("orphan" if f.name.startswith("orphan_") else "failed")
            )
            records.append(
                {
                    "path": str(f.resolve()),
                    "name": f.name,
                    "kind": kind,
                    "size_bytes": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                }
            )
    return records


def recover_unclaimed_staging(
    directory: Path | str,
    min_age_sec: float = 30.0,
    return_skipped: bool = False,
) -> list[Path] | tuple[list[Path], list[dict[str, Any]]]:
    """Identify and quarantine orphan staging files left behind by crashed/interrupted runs.

    Safety Guarantees:
    - Active tasks holding a lease lock or living PID are strictly preserved and skipped.
    - Files with unconfirmed status or younger than min_age_sec are strictly retained.
    - Never overwrites existing diagnostic artifacts in quarantine.
    - Confirmed orphans (lease lock released & PID dead & min_age reached) are safely archived into quarantine/orphan_{name}.
    - Staging files are never unlinked upon quarantine failure.
    """
    p = Path(directory)
    staging_dir = p if p.name == ".staging" else p / ".staging"
    if not staging_dir.is_dir():
        return ([], []) if return_skipped else []

    quarantine_dir = staging_dir.parent / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    recovered = []
    skipped_active = []
    now = time.time()

    for f in sorted(staging_dir.iterdir()):
        if not f.is_file() or not f.name.startswith("stage_"):
            continue

        parts = f.name.split("_")
        stage_id = parts[1] if len(parts) >= 2 else ""
        lease_file = staging_dir / f".lease_{stage_id}.lock" if stage_id else None
        has_lease = lease_file is not None and lease_file.is_file()

        # Check if task is actively running via lease file
        if has_lease and is_task_active(lease_file):
            skipped_active.append(
                {
                    "path": str(f.resolve()),
                    "stage_id": stage_id,
                    "reason": "active_task_lease_held",
                }
            )
            continue

        try:
            stat_res = f.stat()
            file_age = now - stat_res.st_mtime
        except OSError as stat_err:
            # Status cannot be confirmed: retain staging file
            skipped_active.append(
                {
                    "path": str(f.resolve()),
                    "stage_id": stage_id,
                    "reason": f"unconfirmed_stat_error: {stat_err}",
                }
            )
            continue

        # If lease exists but inactive, check min_age_sec
        if has_lease and min_age_sec > 0.0 and file_age < min_age_sec:
            skipped_active.append(
                {
                    "path": str(f.resolve()),
                    "stage_id": stage_id,
                    "reason": f"recent_terminated_task (age {file_age:.1f}s < {min_age_sec}s)",
                }
            )
            continue

        # If file has NO lease file on disk, check min_age_sec
        if not has_lease and min_age_sec > 0.0 and file_age < min_age_sec:
            skipped_active.append(
                {
                    "path": str(f.resolve()),
                    "stage_id": stage_id,
                    "reason": f"fresh_unleased_file (age {file_age:.1f}s < {min_age_sec}s)",
                }
            )
            continue

        # Check if file is currently open or locked by any process
        if _is_file_locked(f):
            skipped_active.append(
                {
                    "path": str(f.resolve()),
                    "stage_id": stage_id,
                    "reason": "file_currently_locked_or_in_use",
                }
            )
            continue

        # Confirmed orphan: move into quarantine without overwriting any existing artifact
        base_name = f"orphan_{f.name}"
        try:
            target = _safe_quarantine_move(f, quarantine_dir, base_name)
            recovered.append(target)
            if has_lease and lease_file.is_file():
                lease_file.unlink(missing_ok=True)
        except OSError as err:
            sys.stderr.write(
                f"[RECOVER_WARNING] Failed to quarantine orphan staging file {f}: {err}\n"
            )

    if return_skipped:
        return recovered, skipped_active
    return recovered


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
