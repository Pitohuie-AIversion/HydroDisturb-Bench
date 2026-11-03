"""COMSOL Java API Bridge for HydroDisturb-Bench.

Provides programmatic access to COMSOL Multiphysics using the official
Java API and comsolbatch/comsolcompile tools, guaranteeing baseline
model immutability, parameter validation, and detailed execution logging.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import errno
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
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
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\COMSOL\COMSOL64"
            ) as key:
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
        proc = subprocess.run(cmd, cwd=str(java_dir), capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ComsolConnectionError(
                f"Failed to compile ComsolBridge.java (exit code {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
            )

    return java_class


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
        check=False,
    )

    stdout = proc.stdout
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
        raise ComsolConnectionError("ComsolBridge JSON response missing valid string field 'operation'")

    if op == "inspect_api":
        if "comsol_version" not in data or "api_specification" not in data:
            raise ComsolConnectionError("Schema violation in inspect_api response: missing version or specification")
    elif op == "inspect_model":
        if "model_tag" not in data or "parameters" not in data or "components" not in data:
            raise ComsolConnectionError("Schema violation in inspect_model response: missing model_tag, parameters, or components")
    elif op == "update_parameter":
        if "param_name" not in data or "new_expr" not in data or "readback_value" not in data:
            raise ComsolConnectionError("Schema violation in update_parameter response: missing param_name, new_expr, or readback_value")
    elif op == "run_study":
        if "study_tag" not in data or "elapsed_ms" not in data or "sol_tags" not in data or "solution_stats" not in data:
            raise ComsolConnectionError("Schema violation in run_study response: missing study_tag, elapsed_ms, sol_tags, or solution_stats")

    data.setdefault("log", stdout)
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
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")
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
                raise FileExistsError(f"Destination output file already exists: {output_path}") from err
            _posix_rename_noreplace(staging_path, output_path, err)


def _posix_rename_noreplace(staging_path: Path, output_path: Path, original_err: Exception | None = None) -> None:
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
            raise FileExistsError(
                f"Destination output file already exists: {output_path}"
            ) from (original_err or err)
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
            kind = "conflict" if f.name.startswith("conflict_") else ("orphan" if f.name.startswith("orphan_") else "failed")
            records.append({
                "path": str(f.resolve()),
                "name": f.name,
                "kind": kind,
                "size_bytes": f.stat().st_size,
                "mtime": f.stat().st_mtime,
            })
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
        has_lease = (lease_file is not None and lease_file.is_file())

        # Check if task is actively running via lease file
        if has_lease and is_task_active(lease_file):
            skipped_active.append({
                "path": str(f.resolve()),
                "stage_id": stage_id,
                "reason": "active_task_lease_held",
            })
            continue

        try:
            stat_res = f.stat()
            file_age = now - stat_res.st_mtime
        except OSError as stat_err:
            # Status cannot be confirmed: retain staging file
            skipped_active.append({
                "path": str(f.resolve()),
                "stage_id": stage_id,
                "reason": f"unconfirmed_stat_error: {stat_err}",
            })
            continue

        # If lease exists but inactive, check min_age_sec
        if has_lease and file_age < min_age_sec:
            skipped_active.append({
                "path": str(f.resolve()),
                "stage_id": stage_id,
                "reason": f"recent_terminated_task (age {file_age:.1f}s < {min_age_sec}s)",
            })
            continue

        # If file has NO lease file on disk, check min_age_sec
        if not has_lease and file_age < min_age_sec:
            skipped_active.append({
                "path": str(f.resolve()),
                "stage_id": stage_id,
                "reason": f"fresh_unleased_file (age {file_age:.1f}s < {min_age_sec}s)",
            })
            continue

        # Check if file is currently open or locked by any process
        if _is_file_locked(f):
            skipped_active.append({
                "path": str(f.resolve()),
                "stage_id": stage_id,
                "reason": "file_currently_locked_or_in_use",
            })
            continue

        # Confirmed orphan: move into quarantine without overwriting any existing artifact
        base_name = f"orphan_{f.name}"
        try:
            target = _safe_quarantine_move(f, quarantine_dir, base_name)
            recovered.append(target)
            if has_lease and lease_file.is_file():
                lease_file.unlink(missing_ok=True)
        except OSError as err:
            sys.stderr.write(f"[RECOVER_WARNING] Failed to quarantine orphan staging file {f}: {err}\n")

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
    if tlist_override:
        args.append(tlist_override.strip())

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
            quarantine_staging_file(staging_path, output_txt_path, stage_id, reason="source_mutated")
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
