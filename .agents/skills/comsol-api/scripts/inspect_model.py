#!/usr/bin/env python
"""Inspect real COMSOL Multiphysics model structure in a strictly read-only manner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

src_dir = Path(__file__).resolve().parents[4] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from hydro_disturb_bench.comsol_java.bridge import inspect_model


def main():
    parser = argparse.ArgumentParser(description="Inspect COMSOL model structure.")
    parser.add_argument("model_path", help="Path to the COMSOL .mph model file")
    parser.add_argument("--out", "-o", help="Optional path to save JSON inspection report")
    args = parser.parse_args()

    try:
        res = inspect_model(Path(args.model_path))
        formatted = json.dumps(res, indent=2, ensure_ascii=False)
        print(formatted)
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(formatted, encoding="utf-8")
            print(f"\nInspection saved to: {out_path}", file=sys.stderr)
    except Exception as exc:
        print(f"Error inspecting model: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
