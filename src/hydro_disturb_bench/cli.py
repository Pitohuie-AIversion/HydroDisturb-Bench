"""Planning and optional local COMSOL integration commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .config import read_config
from .sampling import sample_cases
from .io import atomic_json


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description="HydroDisturb-Bench (case planning; CFD not validated)")
    sub=parser.add_subparsers(dest="cmd",required=True)
    plan=sub.add_parser("plan",help="Generate deterministic, unsimulated case plans")
    plan.add_argument("--config",required=True)
    plan.add_argument("--count",type=int,default=20)
    plan.add_argument("--seed",type=int,default=2026)
    plan.add_argument("--out",required=True)
    inspect=sub.add_parser("inspect",help="Print a case plan, including hidden forcing truth")
    inspect.add_argument("--case",required=True)
    probe=sub.add_parser("probe",help="Read-only inspection of actual COMSOL model parameters")
    probe.add_argument("--model",required=True)
    probe.add_argument("--cores",type=int,default=1)
    solve=sub.add_parser("solve",help="Run one COMSOL study and save a separate .mph file")
    solve.add_argument("--model",required=True)
    solve.add_argument("--case",required=True)
    solve.add_argument("--output",required=True)
    solve.add_argument("--cores",type=int,default=4)
    args=parser.parse_args(argv)
    if args.cmd=="plan":
        config=read_config(args.config)
        cases=sample_cases(config,count=args.count,seed=args.seed)
        out=Path(args.out)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError(f"Output directory not empty: {out}")
        for case in cases:
            atomic_json(out/"cases"/f'{case["case_id"]}.json',case)
        atomic_json(out/"manifest.json",{"schema_version":1,"seed":args.seed,"total":args.count,"status":"PLANNED_NOT_SIMULATED","cases":[f'cases/{c["case_id"]}.json' for c in cases]})
        print(f"Planned {len(cases)} cases at {out}. No CFD runs performed.")
    elif args.cmd=="inspect":
        print(Path(args.case).read_text(encoding="utf-8"))
    elif args.cmd=="probe":
        from .comsol_adapter import probe_model
        names=probe_model(Path(args.model),cores=args.cores)
        print(json.dumps({"parameters":names},indent=2))
    elif args.cmd=="solve":
        from .comsol_adapter import solve_single_case
        case=json.loads(Path(args.case).read_text(encoding="utf-8"))
        solve_single_case(Path(args.model),case,Path(args.output),cores=args.cores)
        print(f"COMSOL study completed; saved .mph model to {args.output}. Numerical validity NOT yet checked.")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
