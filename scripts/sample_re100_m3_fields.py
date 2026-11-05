"""Sample five saved M3 times on two Cartesian grids, without running a solver."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench.comsol_java import export_full_field_sample
from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, inspect_model

MODEL = ROOT / "runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_solved.mph"
MODEL_SHA256 = "15e841fc4240d93db877932c1b371646426f1229bdb40d0335b0ed7609d145b4"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing paths are rejected")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    if compute_file_hash(MODEL).lower() != MODEL_SHA256:
        raise RuntimeError("Saved M3 source differs from the accepted baseline hash")
    inspection = inspect_model(MODEL)
    if inspection.get("status") != "SUCCESS":
        raise RuntimeError("Live M3 inspection failed")
    report = export_full_field_sample(MODEL, args.output)
    (args.output / "model_inspection.json").write_text(
        json.dumps(inspection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
