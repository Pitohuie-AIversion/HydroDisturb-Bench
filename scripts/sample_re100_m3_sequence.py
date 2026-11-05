"""Export 151 consecutive saved M3 frames in 32-frame batches, without solving."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, inspect_model
from hydro_disturb_bench.field_sequence import export_full_field_sequence

MODEL = ROOT / "runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_solved.mph"
MODEL_SHA256 = "15e841fc4240d93db877932c1b371646426f1229bdb40d0335b0ed7609d145b4"
REFERENCE = ROOT / "runs/re100_m3_field_sample_20261105_verified/grid_161x97"
REFERENCE_SHA256 = "d9646e1bf03687ea9a6e10a0275f64d750cc4e78ca02a89c2f5942593cf77570"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="A new directory; existing paths are rejected")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(args.output)
    if compute_file_hash(MODEL).lower() != MODEL_SHA256:
        raise RuntimeError("Source differs from the accepted M3 model")
    if compute_file_hash(REFERENCE / "field_sample.npz").lower() != REFERENCE_SHA256:
        raise RuntimeError("Overlap reference differs from the accepted five-frame sample")
    inspection = inspect_model(MODEL)
    if inspection.get("status") != "SUCCESS":
        raise RuntimeError("Live M3 inspection failed")
    report = export_full_field_sequence(MODEL, args.output, overlap_reference=REFERENCE)
    (args.output / "model_inspection.json").write_bytes(
        (json.dumps(inspection, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(json.dumps({key: report[key] for key in ("status", "output_dir", "shape", "source_sha256",
        "readback_passed", "direct_point_checks", "max_direct_absolute_errors", "overlap_check", "fluid_entries", "masked_entries")},
        indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
