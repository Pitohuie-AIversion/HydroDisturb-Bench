"""
Floating License Queue & Concurrency Management.
Guarantees commercial token protection and serialized execution.
"""
import os
from pathlib import Path
from filelock import FileLock

LOCK_DIR = Path(os.getenv("MCP_LOCK_DIR", "./runs/.locks")).resolve()
LOCK_DIR.mkdir(parents=True, exist_ok=True)

LICENSE_LOCK_FILE = LOCK_DIR / "commercial_license.lock"
license_lock = FileLock(str(LICENSE_LOCK_FILE), timeout=int(os.getenv("MCP_LICENSE_TIMEOUT", "300")))
