#!/usr/bin/env python
"""Execute a study on a validated COMSOL model working copy and save solved output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

src_dir = Path(__file__).resolve().parents[4] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from hydro_disturb_bench.comsol_java.bridge import run_study


def main():
    parser = argparse.ArgumentParser(description="Run study on COMSOL model working copy.")
    parser.add_argument("model_path", help="Path to source COMSOL .mph file (read-only baseline)")
    parser.add_argument("output_path", help="Path to destination solved .mph file")
    parser.add_argument("--study", "-s", default="std1", help="Study tag to solve (default: std1)")
    parser.add_argument(
        "--tlist", "-t", default="", help="Optional transient time range, e.g., 'range(0,0.05,0.1)'"
    )
    parser.add_argument("--log-out", "-l", help="Optional path to write raw solver execution log")
    args = parser.parse_args()

    try:
        res = run_study(
            model_path=Path(args.model_path),
            output_path=Path(args.output_path),
            study_tag=args.study,
            tlist_override=args.tlist,
        )

        log_content = res.get("log", "")
        if args.log_out:
            log_path = Path(args.log_out)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(log_content, encoding="utf-8")
            print(f"Solver log saved to: {log_path}", file=sys.stderr)

        print(json.dumps(res, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"Error executing study: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
