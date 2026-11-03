"""Planning and local COMSOL integration commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .config import read_config
from .sampling import sample_cases
from .io import atomic_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HydroDisturb-Bench (case planning and COMSOL integration)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Existing MPh / Planning subcommands
    plan = sub.add_parser("plan", help="Generate deterministic, unsimulated case plans")
    plan.add_argument("--config", required=True)
    plan.add_argument("--count", type=int, default=20)
    plan.add_argument("--seed", type=int, default=2026)
    plan.add_argument("--out", required=True)

    inspect = sub.add_parser("inspect", help="Print a case plan, including hidden forcing truth")
    inspect.add_argument("--case", required=True)

    probe = sub.add_parser("probe", help="Read-only inspection of actual COMSOL model parameters via MPh")
    probe.add_argument("--model", required=True)
    probe.add_argument("--cores", type=int, default=1)

    solve = sub.add_parser("solve", help="Run one COMSOL study via MPh and save a separate .mph file")
    solve.add_argument("--model", required=True)
    solve.add_argument("--case", required=True)
    solve.add_argument("--output", required=True)
    solve.add_argument("--cores", type=int, default=4)

    # New Java API subcommands
    j_api = sub.add_parser("java-inspect-api", help="Inspect current COMSOL installation version and Java API specification")

    j_insp = sub.add_parser("java-inspect", help="Read-only inspection of model structure via COMSOL official Java API")
    j_insp.add_argument("--model", required=True, help="Path to .mph model")

    j_upd = sub.add_parser("java-update-param", help="Safely update a parameter on a model copy using official Java API")
    j_upd.add_argument("--model", required=True, help="Source baseline model")
    j_upd.add_argument("--output", required=True, help="Output destination for modified model copy")
    j_upd.add_argument("--name", required=True, help="Parameter name to modify")
    j_upd.add_argument("--expr", required=True, help="New parameter expression with unit (e.g. '1.2[m/s]')")
    j_upd.add_argument("--descr", default="", help="Optional parameter description")
    j_upd.add_argument("--allow-overwrite", action="store_true", default=False, help="Allow overwriting destination if it already exists")

    j_solve = sub.add_parser("java-solve", help="Execute a study on a validated model copy via COMSOL Java API")
    j_solve.add_argument("--model", required=True, help="Model to solve")
    j_solve.add_argument("--output", required=True, help="Output path for solved model")
    j_solve.add_argument("--study", required=True, help="Study tag (e.g. 'std1')")
    j_solve.add_argument("--tlist", default="", help="Optional override for time list (e.g. 'range(0, 0.05, 0.1)')")
    j_solve.add_argument("--allow-overwrite", action="store_true", default=False, help="Allow overwriting destination if it already exists")

    j_build = sub.add_parser("java-build-re100", help="Build independent Re=100 low-blockage benchmark model from baseline template")
    j_build.add_argument("--model", required=True, help="Baseline model path (e.g. case/cylinder_flow.mph)")
    j_build.add_argument("--output", required=True, help="Output benchmark model path (e.g. models/cylinder_2d/re100_benchmark.mph)")
    j_build.add_argument("--allow-overwrite", action="store_true", default=False, help="Allow overwriting destination if it already exists")

    j_exp = sub.add_parser("java-export-data", help="Export solution field data (u, v, p, vx-uy, spf.U) to text file")
    j_exp.add_argument("--model", required=True, help="Solved model path")
    j_exp.add_argument("--output", required=True, help="Output text file path")
    j_exp.add_argument("--allow-overwrite", action="store_true", default=False, help="Allow overwriting destination if it already exists")

    args = parser.parse_args(argv)

    if args.cmd == "plan":
        config = read_config(args.config)
        cases = sample_cases(config, count=args.count, seed=args.seed)
        out = Path(args.out)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError(f"Output directory not empty: {out}")
        for case in cases:
            atomic_json(out / "cases" / f'{case["case_id"]}.json', case)
        atomic_json(
            out / "manifest.json",
            {
                "schema_version": 1,
                "seed": args.seed,
                "total": args.count,
                "status": "PLANNED_NOT_SIMULATED",
                "cases": [f'cases/{c["case_id"]}.json' for c in cases],
            },
        )
        print(f"Planned {len(cases)} cases at {out}. No CFD runs performed.")
    elif args.cmd == "inspect":
        print(Path(args.case).read_text(encoding="utf-8"))
    elif args.cmd == "probe":
        from .comsol_adapter import probe_model
        names = probe_model(Path(args.model), cores=args.cores)
        print(json.dumps({"parameters": names}, indent=2))
    elif args.cmd == "solve":
        from .comsol_adapter import solve_single_case
        case = json.loads(Path(args.case).read_text(encoding="utf-8"))
        solve_single_case(Path(args.model), case, Path(args.output), cores=args.cores)
        print(f"COMSOL study completed; saved .mph model to {args.output}. Numerical validity NOT yet checked.")
    elif args.cmd == "java-inspect-api":
        from .comsol_java import inspect_api
        res = inspect_api()
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-inspect":
        from .comsol_java import inspect_model
        res = inspect_model(Path(args.model))
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-update-param":
        from .comsol_java import update_parameter
        res = update_parameter(
            model_path=Path(args.model),
            output_path=Path(args.output),
            param_name=args.name,
            param_expr=args.expr,
            descr=args.descr,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-solve":
        from .comsol_java import run_study
        res = run_study(
            model_path=Path(args.model),
            output_path=Path(args.output),
            study_tag=args.study,
            tlist_override=args.tlist,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-build-re100":
        from .comsol_java import build_re100_benchmark
        res = build_re100_benchmark(
            baseline_path=Path(args.model),
            output_path=Path(args.output),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-export-data":
        from .comsol_java import export_solution_data
        res = export_solution_data(
            model_path=Path(args.model),
            output_txt_path=Path(args.output),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
