"""Filesystem leases, publication and retained-failure recovery.

This module has no COMSOL process or model dependencies. Its bridge aliases
preserve existing callers while this module owns transaction implementation.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .errors import ModelValidationError


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
