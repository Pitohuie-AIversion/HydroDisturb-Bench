#!/usr/bin/env python
"""Update a verified parameter in a COMSOL model copy with readback verification."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

src_dir = Path(__file__).resolve().parents[4] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from hydro_disturb_bench.comsol_java.bridge import update_parameter


def main():
    parser = argparse.ArgumentParser(description="Update parameter on COMSOL model working copy.")
    parser.add_argument("model_path", help="Path to source COMSOL .mph file (read-only baseline)")
    parser.add_argument("output_path", help="Path to destination .mph file (working copy)")
    parser.add_argument("param_name", help="Name of parameter to modify (must exist)")
    parser.add_argument("param_expr", help="New expression with units, e.g., '1.15[m/s]'")
    parser.add_argument("--descr", "-d", default="", help="Optional parameter description")
    args = parser.parse_args()

    try:
        res = update_parameter(
            model_path=Path(args.model_path),
            output_path=Path(args.output_path),
            param_name=args.param_name,
            param_expr=args.param_expr,
            descr=args.descr,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"Error updating parameter: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
