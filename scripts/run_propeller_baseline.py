"""Reproduce CW (+1400 rpm) & CCW (-1400 rpm) 3D propeller baselines via COMSOL Java API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.propeller.baseline import (  # noqa: E402
    DEFAULT_CAD_PATH,
    DEFAULT_CCW_MODEL,
    DEFAULT_CW_MODEL,
    DEFAULT_OUT_DIR,
    reproduce_propeller_baselines,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce CW (+1400 rpm) & CCW (-1400 rpm) V10f 3D propeller baselines"
    )
    parser.add_argument("--cw-model", default=str(DEFAULT_CW_MODEL))
    parser.add_argument("--ccw-model", default=str(DEFAULT_CCW_MODEL))
    parser.add_argument("--cad-path", default=str(DEFAULT_CAD_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--solver-mode",
        choices=["warm_fine", "pilot_coarse"],
        default="warm_fine",
    )
    parser.add_argument("--save-mph", action="store_true", default=False)
    parser.add_argument("--allow-overwrite", action="store_true", default=False)
    parser.add_argument("--inspect-only", action="store_true", default=False)
    parser.add_argument("--skip-hash-pin", action="store_true", default=False)
    args = parser.parse_args(argv)

    summary = reproduce_propeller_baselines(
        cw_model=args.cw_model,
        ccw_model=args.ccw_model,
        cad_path=args.cad_path if args.cad_path else None,
        output_dir=args.output_dir,
        solver_mode=args.solver_mode,
        save_mph=args.save_mph,
        allow_overwrite=args.allow_overwrite,
        inspect_only=args.inspect_only,
        pin_hashes=not args.skip_hash_pin,
    )
    compact = {
        "status": summary["status"],
        "immutability_verified": summary["immutability_verified"],
        "summary_json": summary["summary_json"],
        "verification": summary.get("verification"),
    }
    print(json.dumps(compact, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
