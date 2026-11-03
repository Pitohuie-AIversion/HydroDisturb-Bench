"""Explicit compiler and integrity verification for COMSOL Java Bridge."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure stdout/stderr handle UTF-8 / emojis on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add src to sys.path
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java.bridge import (  # noqa: E402
    ComsolConnectionError,
    ensure_bridge_compiled,
    find_comsol_root,
)


def main() -> int:
    print("[HydroDisturb-Bench] Compiling & Verifying COMSOL Java Bridge...")
    try:
        root = find_comsol_root()
        print(f"  Found COMSOL installation: {root}")
    except ComsolConnectionError as e:
        print(f"  [SKIPPED] COMSOL environment not found: {e}")
        return 0

    try:
        class_file = ensure_bridge_compiled(root)
        print(f"  [SUCCESS] ComsolBridge compiled successfully: {class_file}")
        print(f"  Bytecode size: {class_file.stat().st_size} bytes")
        return 0
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"  [ERROR] Compilation failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
