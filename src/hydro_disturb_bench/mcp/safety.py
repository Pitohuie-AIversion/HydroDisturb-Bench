"""Safety guardrails, Copy-on-Write validation, and error taxonomy for COMSOL MCP."""

import hashlib
import os
from pathlib import Path
from typing import Any

from filelock import FileLock

# Standard Error Code Taxonomy
ERR_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
ERR_COW_VIOLATION = "COW_VIOLATION"
ERR_NODE_NOT_FOUND = "NODE_NOT_FOUND"
ERR_INVALID_PARAMETER = "INVALID_PARAMETER"
ERR_COMPILATION_FAILED = "COMPILATION_FAILED"
ERR_EXECUTION_FAILED = "EXECUTION_FAILED"
ERR_SOLVER_DIVERGED = "SOLVER_DIVERGED"
ERR_TIMEOUT_EXCEEDED = "TIMEOUT_EXCEEDED"
ERR_LICENSE_ERROR = "LICENSE_ERROR"

# Workspace & Concurrency Lock
WORKSPACE_ROOT = Path(os.getenv("COMSOL_MCP_WORKSPACE", "./runs/mcp_workspace")).resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
LICENSE_LOCK_FILE = WORKSPACE_ROOT / ".comsol_license.lock"
license_lock = FileLock(str(LICENSE_LOCK_FILE), timeout=300)


def compute_sha256(path: Path | str) -> str:
    """Computes SHA-256 hash of a file for immutability verification."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def validate_cow_paths(source_path: str | Path | None, output_path: str | Path | None) -> dict[str, Any] | None:
    """Enforces strict Copy-on-Write: output_path must be distinct from source_path."""
    if source_path is None or output_path is None:
        return None
    src = Path(source_path).resolve()
    dst = Path(output_path).resolve()
    if src == dst:
        return {
            "status": "ERROR",
            "code": ERR_COW_VIOLATION,
            "message": f"Copy-on-Write violation: source_model ({src}) and output_model ({dst}) must be different paths. Never overwrite baseline models in place.",
        }
    return None


def format_error(code: str, message: str, details: str = "") -> dict[str, Any]:
    """Helper to return consistent structured error responses."""
    resp = {
        "status": "ERROR",
        "code": code,
        "message": message,
    }
    if details:
        resp["details"] = details
    return resp
