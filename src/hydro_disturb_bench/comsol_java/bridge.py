"""COMSOL Java API Bridge for HydroDisturb-Bench.

Provides programmatic access to COMSOL Multiphysics using the official
Java API and comsolbatch/comsolcompile tools, guaranteeing baseline
model immutability, parameter validation, and detailed execution logging.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import winreg
from typing import Any


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

    if start_tag not in stdout or end_tag not in stdout:
        raise ComsolConnectionError(
            f"ComsolBridge execution produced invalid output (exit code {proc.returncode}):\n"
            f"STDOUT:\n{stdout}\nSTDERR:\n{proc.stderr}"
        )

    json_str = stdout.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as err:
        raise ComsolConnectionError(
            f"Failed to decode JSON from ComsolBridge: {err}\nRaw output: {json_str}"
        ) from err

    if data.get("status") == "ERROR":
        err_code = data.get("error_code", ERR_COMSOL_CONNECTION_FAILED)
        msg = data.get("message", "Unknown bridge error")
        details = data.get("details", "")
        raise map_error(err_code, msg, details)

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


def update_parameter(
    model_path: Path | str,
    output_path: Path | str,
    param_name: str,
    param_expr: str,
    descr: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Modify a verified parameter in a model copy and read back the value."""
    model_path = Path(model_path)
    output_path = Path(output_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")

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

    args = [
        "update-param",
        str(model_path.resolve()),
        str(output_path.resolve()),
        param_name.strip(),
        param_expr.strip(),
    ]
    if descr:
        args.append(descr.strip())

    res = _run_bridge(args, root=root)

    # Verify baseline model was completely untouched
    hash_after = compute_file_hash(model_path)
    if hash_before != hash_after:
        raise ModelValidationError(
            f"Safety violation: original model file was modified during parameter update! "
            f"Original SHA-256 changed from {hash_before} to {hash_after}"
        )

    res["source_sha256"] = hash_before
    res["output_sha256"] = compute_file_hash(output_path)
    return res


def run_study(
    model_path: Path | str,
    output_path: Path | str,
    study_tag: str,
    tlist_override: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Execute a study on a validated model copy and save results to output_path."""
    model_path = Path(model_path)
    output_path = Path(output_path)

    if not model_path.is_file():
        raise ModelNotFoundError(f"Source model file does not exist: {model_path}")

    if model_path.resolve() == output_path.resolve():
        raise ModelValidationError(
            "Refusing to overwrite baseline model: source and output path are identical"
        )

    # Record baseline hash before execution
    hash_before = compute_file_hash(model_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "run-study",
        str(model_path.resolve()),
        str(output_path.resolve()),
        study_tag.strip(),
    ]
    if tlist_override:
        args.append(tlist_override.strip())

    res = _run_bridge(args, root=root)

    # Verify baseline model untouched
    hash_after = compute_file_hash(model_path)
    if hash_before != hash_after:
        raise ModelValidationError(
            f"Safety violation: source model was modified during study run! "
            f"Original SHA-256 changed from {hash_before} to {hash_after}"
        )

    res["source_sha256"] = hash_before
    res["output_sha256"] = compute_file_hash(output_path)
    return res
