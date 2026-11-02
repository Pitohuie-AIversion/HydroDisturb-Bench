#!/usr/bin/env python
"""Inspect COMSOL Multiphysics version, tools, and verified Java API methods."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add src to sys.path if not present
src_dir = Path(__file__).resolve().parents[4] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from hydro_disturb_bench.comsol_java.bridge import inspect_api


def main():
    try:
        res = inspect_api()
        print(json.dumps(res, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"Error inspecting COMSOL API: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
