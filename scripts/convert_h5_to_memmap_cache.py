#!/usr/bin/env python3
"""Standalone CLI entrypoint for converting HDF5 golden trajectories into Memmap cache."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = BENCH_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.memmap_cache import (  # noqa: E402
    benchmark_memmap_read,
    convert_h5_archive_to_memmap,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert compressed 3D CFD HDF5 trajectories into high-throughput 4ch Memmap cache."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path(r"E:\cfd_3d\03_world_model_dataset"),
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--dtype", choices=["bf16", "fp32"], default="bf16")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--chunk-frames", type=int, default=32)
    parser.add_argument("--cases", type=str, default="")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()

    case_list = [x.strip() for x in args.cases.split(",") if x.strip()] or None
    report = convert_h5_archive_to_memmap(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        dtype_mode=args.dtype,
        workers=args.workers,
        chunk_frames=args.chunk_frames,
        cases=case_list,
        verify=args.verify,
    )
    if args.benchmark:
        out_dir = (
            args.output_dir.resolve()
            if args.output_dir
            else args.input_dir.resolve().parent / f"{args.input_dir.resolve().name}_memmap_{args.dtype}"
        )
        report["benchmark"] = benchmark_memmap_read(out_dir, report["cases"])
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
