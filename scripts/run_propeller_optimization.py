"""CLI script to execute the ducted propeller optimization pipeline.

Usage:
    python scripts/run_propeller_optimization.py --config configs/propeller_opt_v10f.json --output-dir runs/propeller_opt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from hydro_disturb_bench.propeller.evaluator import PropellerEvaluator
from hydro_disturb_bench.propeller.workflow import run_optimization_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run ducted propeller multi-objective optimization pipeline"
    )
    parser.add_argument(
        "--config",
        default="configs/propeller_opt_v10f.json",
        help="Path to propeller optimization JSON config",
    )
    parser.add_argument(
        "--output-dir",
        default="runs/propeller_opt",
        help="Output directory for checkpoints and reports",
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=["precheck", "doe", "train", "optimize", "verify", "all"],
        help="Pipeline execution stage",
    )
    parser.add_argument("--doe-samples", type=int, default=20, help="Number of LHS DoE samples")
    parser.add_argument("--pop-size", type=int, default=24, help="NSGA-II population size")
    parser.add_argument("--gen", type=int, default=15, help="Number of generations")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")

    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        sys.stderr.write(f"Configuration file not found: {cfg_path}\n")
        return 1

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.stderr.write(f"Malformed configuration JSON: {exc}\n")
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ev = PropellerEvaluator(work_dir=out_dir, max_workers=1)
    res = run_optimization_pipeline(
        work_dir=out_dir,
        stage=args.stage,
        evaluator_fn=ev.evaluate,
        doe_samples=args.doe_samples,
        pop_size=args.pop_size,
        n_gen=args.gen,
        seed=args.seed,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
