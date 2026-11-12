"""Planning and local COMSOL integration commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import read_config
from .io import atomic_json
from .sampling import sample_cases


def main(argv: list[str] | None = None) -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="HydroDisturb-Bench (case planning and COMSOL integration)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Existing MPh / Planning subcommands
    plan = sub.add_parser("plan", help="Generate deterministic, unsimulated case plans")
    plan.add_argument("--config", required=True)
    plan.add_argument("--count", type=int, default=20)
    plan.add_argument("--seed", type=int, default=2026)
    plan.add_argument("--out", required=True)

    inspect = sub.add_parser("inspect", help="Print a case plan, including hidden forcing truth")
    inspect.add_argument("--case", required=True)

    probe = sub.add_parser(
        "probe", help="Read-only inspection of actual COMSOL model parameters via MPh"
    )
    probe.add_argument("--model", required=True)
    probe.add_argument("--cores", type=int, default=1)

    solve = sub.add_parser(
        "solve", help="Run one COMSOL study via MPh and save a separate .mph file"
    )
    solve.add_argument("--model", required=True)
    solve.add_argument("--case", required=True)
    solve.add_argument("--output", required=True)
    solve.add_argument("--cores", type=int, default=4)

    # New Java API subcommands
    j_api = sub.add_parser(
        "java-inspect-api",
        help="Inspect current COMSOL installation version and Java API specification",
    )

    j_insp = sub.add_parser(
        "java-inspect", help="Read-only inspection of model structure via COMSOL official Java API"
    )
    j_insp.add_argument("--model", required=True, help="Path to .mph model")

    j_upd = sub.add_parser(
        "java-update-param",
        help="Safely update a parameter on a model copy using official Java API",
    )
    j_upd.add_argument("--model", required=True, help="Source baseline model")
    j_upd.add_argument("--output", required=True, help="Output destination for modified model copy")
    j_upd.add_argument("--name", required=True, help="Parameter name to modify")
    j_upd.add_argument(
        "--expr", required=True, help="New parameter expression with unit (e.g. '1.2[m/s]')"
    )
    j_upd.add_argument("--descr", default="", help="Optional parameter description")
    j_upd.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    j_solve = sub.add_parser(
        "java-solve", help="Execute a study on a validated model copy via COMSOL Java API"
    )
    j_solve.add_argument("--model", required=True, help="Model to solve")
    j_solve.add_argument("--output", required=True, help="Output path for solved model")
    j_solve.add_argument("--study", required=True, help="Study tag (e.g. 'std1')")
    j_solve.add_argument(
        "--tlist", default="", help="Optional override for time list (e.g. 'range(0, 0.05, 0.1)')"
    )
    j_solve.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    j_build = sub.add_parser(
        "java-build-re100",
        help="Build independent Re=100 low-blockage benchmark model from baseline template",
    )
    j_build.add_argument(
        "--model", required=True, help="Baseline model path (e.g. case/cylinder_flow.mph)"
    )
    j_build.add_argument(
        "--output",
        required=True,
        help="Output benchmark model path (e.g. models/cylinder_2d/re100_benchmark.mph)",
    )
    j_build.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    j_build_c04 = sub.add_parser(
        "java-build-c04",
        help="Build independent C04 (Re=126k, 30Dx20Dx10D) 3D cylinder benchmark model from baseline template",
    )
    j_build_c04.add_argument(
        "--model",
        default="models/cylinder_3d/cylinder_flow_turbulent.mph",
        help="Baseline official 3D model path (default: models/cylinder_3d/cylinder_flow_turbulent.mph)",
    )
    j_build_c04.add_argument(
        "--output",
        default="models/cylinder_3d/c04_re126k_benchmark.mph",
        help="Output C04 benchmark model path (default: models/cylinder_3d/c04_re126k_benchmark.mph)",
    )
    j_build_c04.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    j_mesh2 = sub.add_parser(
        "java-build-c04-mesh2",
        help="Build tuned 1.55M element mesh2 on C04 3D benchmark model",
    )
    j_mesh2.add_argument("--model", required=True, help="Input C04 model path")
    j_mesh2.add_argument("--output", required=True, help="Output model path with built mesh2")
    j_mesh2.add_argument(
        "--allow-overwrite", action="store_true", default=False, help="Allow overwrite"
    )

    j_exp_slice = sub.add_parser(
        "java-export-c04-slice",
        help="Export 2D mid-span slice (192 x 96) from solved C04 model to NPZ dataset",
    )
    j_exp_slice.add_argument("--model", required=True, help="Solved C04 model path")
    j_exp_slice.add_argument("--output", required=True, help="Output .npz file path")
    j_exp_slice.add_argument("--time", type=float, default=0.0, help="Physical time (seconds)")
    j_exp_slice.add_argument("--dataset", default="auto", help="Dataset tag (default: auto)")
    j_exp_slice.add_argument(
        "--solnum", default="auto", help="Solution number or 'all' (default: auto)"
    )
    j_exp_slice.add_argument(
        "--allow-overwrite", action="store_true", default=False, help="Allow overwrite"
    )

    j_exp_vol = sub.add_parser(
        "java-export-c04-volume",
        help="Export 3D regular volume (192 x 96 x 64) from solved C04 model to NPZ dataset",
    )
    j_exp_vol.add_argument("--model", required=True, help="Solved C04 model path")
    j_exp_vol.add_argument("--output", required=True, help="Output .npz file path")
    j_exp_vol.add_argument("--time", type=float, default=0.0, help="Physical time (seconds)")
    j_exp_vol.add_argument("--dataset", default="auto", help="Dataset tag (default: auto)")
    j_exp_vol.add_argument(
        "--solnum", default="auto", help="Solution number or 'all' (default: auto)"
    )
    j_exp_vol.add_argument(
        "--allow-overwrite", action="store_true", default=False, help="Allow overwrite"
    )

    j_seq_slice = sub.add_parser(
        "java-export-c04-slice-sequence",
        help="Export multi-frame 2D mid-span slice sequence from solved C04 model to NPZ dataset",
    )
    j_seq_slice.add_argument("--model", required=True, help="Solved C04 model path")
    j_seq_slice.add_argument("--output", required=True, help="Output .npz file path")
    j_seq_slice.add_argument(
        "--solnums", default="1,2,3,4", help="Comma-separated solnums (default: 1,2,3,4)"
    )
    j_seq_slice.add_argument(
        "--times", default="0.0,0.06,0.12,0.18", help="Comma-separated physical times in seconds"
    )
    j_seq_slice.add_argument("--dataset", default="dset3", help="Dataset tag (default: dset3)")
    j_seq_slice.add_argument(
        "--allow-overwrite", action="store_true", default=False, help="Allow overwrite"
    )

    j_seq_vol = sub.add_parser(
        "java-export-c04-volume-sequence",
        help="Export multi-frame 3D volume sequence from solved C04 model to NPZ dataset",
    )
    j_seq_vol.add_argument("--model", required=True, help="Solved C04 model path")
    j_seq_vol.add_argument("--output", required=True, help="Output .npz file path")
    j_seq_vol.add_argument(
        "--solnums", default="1,2,3,4", help="Comma-separated solnums (default: 1,2,3,4)"
    )
    j_seq_vol.add_argument(
        "--times", default="0.0,0.06,0.12,0.18", help="Comma-separated physical times in seconds"
    )
    j_seq_vol.add_argument("--dataset", default="dset3", help="Dataset tag (default: dset3)")
    j_seq_vol.add_argument(
        "--allow-overwrite", action="store_true", default=False, help="Allow overwrite"
    )

    j_exp = sub.add_parser(
        "java-export-data", help="Export solution field data (u, v, p, vx-uy, spf.U) to text file"
    )
    j_exp.add_argument("--model", required=True, help="Solved model path")
    j_exp.add_argument("--output", required=True, help="Output text file path")
    j_exp.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    j_diag = sub.add_parser(
        "java-export-s1-diagnostics",
        help="Export S1 lift, drag, wake, inlet, and stagnation diagnostics via COMSOL Java API",
    )
    j_diag.add_argument("--model", required=True, help="Solved S1 model path")
    j_diag.add_argument(
        "--output-dir", required=True, help="Output directory for diagnostic text and image files"
    )
    j_diag.add_argument(
        "--allow-overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting destination if it already exists",
    )

    # C04 World Model Data Windows and Baseline Forecasting
    c04_win = sub.add_parser(
        "c04-inspect-windows",
        help="Inspect sliding windows and metadata for a C04 sequence dataset",
    )
    c04_win.add_argument("--dataset", required=True, help="Path to C04 NPZ dataset archive")
    c04_win.add_argument("--history", type=int, default=2, help="History frames count (default: 2)")
    c04_win.add_argument(
        "--forecast", type=int, default=1, help="Forecast frames count (default: 1)"
    )
    c04_win.add_argument("--stride", type=int, default=1, help="Window sliding stride (default: 1)")
    c04_win.add_argument("--out", default=None, help="Optional output path to save JSON manifest")

    c04_eval = sub.add_parser(
        "c04-evaluate-baselines",
        help="Evaluate history-only baseline forecasts (persistence, last_two_linear) on C04 dataset",
    )
    c04_eval.add_argument("--dataset", required=True, help="Path to C04 NPZ dataset archive")
    c04_eval.add_argument(
        "--history", type=int, default=2, help="History frames count (default: 2)"
    )
    c04_eval.add_argument(
        "--forecast", type=int, default=1, help="Forecast frames count (default: 1)"
    )
    c04_eval.add_argument(
        "--stride", type=int, default=1, help="Window sliding stride (default: 1)"
    )
    c04_eval.add_argument(
        "--method",
        default="persistence",
        choices=["persistence", "last_two_linear"],
        help="Baseline forecasting method",
    )

    args = parser.parse_args(argv)

    if args.cmd == "plan":
        config = read_config(args.config)
        cases = sample_cases(config, count=args.count, seed=args.seed)
        out = Path(args.out)
        if out.exists() and any(out.iterdir()):
            raise FileExistsError(f"Output directory not empty: {out}")
        for case in cases:
            atomic_json(out / "cases" / f"{case['case_id']}.json", case)
        atomic_json(
            out / "manifest.json",
            {
                "schema_version": 1,
                "seed": args.seed,
                "total": args.count,
                "status": "PLANNED_NOT_SIMULATED",
                "cases": [f"cases/{c['case_id']}.json" for c in cases],
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
        print(
            f"COMSOL study completed; saved .mph model to {args.output}. Numerical validity NOT yet checked."
        )
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
    elif args.cmd == "java-build-c04":
        from .comsol_java import build_c04_benchmark

        res = build_c04_benchmark(
            baseline_path=Path(args.model),
            output_path=Path(args.output),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-build-c04-mesh2":
        from .comsol_java import build_c04_mesh2

        res = build_c04_mesh2(
            model_path=Path(args.model),
            output_path=Path(args.output),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-export-c04-slice":
        from .comsol_java import export_c04_slice_dataset

        saved_path = export_c04_slice_dataset(
            model_path=Path(args.model),
            output_npz=Path(args.output),
            time_s=args.time,
            dataset_tag=args.dataset,
            solnum=args.solnum,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps({"status": "SUCCESS", "dataset_path": str(saved_path)}, indent=2))
    elif args.cmd == "java-export-c04-volume":
        from .comsol_java import export_c04_volume_dataset

        saved_path = export_c04_volume_dataset(
            model_path=Path(args.model),
            output_npz=Path(args.output),
            time_s=args.time,
            dataset_tag=args.dataset,
            solnum=args.solnum,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps({"status": "SUCCESS", "dataset_path": str(saved_path)}, indent=2))
    elif args.cmd == "java-export-c04-slice-sequence":
        from .comsol_java import export_c04_slice_sequence

        snums = [s.strip() for s in args.solnums.split(",") if s.strip()]
        times_list = [float(t.strip()) for t in args.times.split(",") if t.strip()]
        saved_path = export_c04_slice_sequence(
            model_path=Path(args.model),
            output_npz=Path(args.output),
            solnums=snums,
            times_s=times_list,
            dataset_tag=args.dataset,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps({"status": "SUCCESS", "dataset_path": str(saved_path)}, indent=2))
    elif args.cmd == "java-export-c04-volume-sequence":
        from .comsol_java import export_c04_volume_sequence

        snums = [s.strip() for s in args.solnums.split(",") if s.strip()]
        times_list = [float(t.strip()) for t in args.times.split(",") if t.strip()]
        saved_path = export_c04_volume_sequence(
            model_path=Path(args.model),
            output_npz=Path(args.output),
            solnums=snums,
            times_s=times_list,
            dataset_tag=args.dataset,
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps({"status": "SUCCESS", "dataset_path": str(saved_path)}, indent=2))
    elif args.cmd == "java-export-data":
        from .comsol_java import export_solution_data

        res = export_solution_data(
            model_path=Path(args.model),
            output_txt_path=Path(args.output),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "java-export-s1-diagnostics":
        from .comsol_java import export_s1_diagnostics

        res = export_s1_diagnostics(
            model_path=Path(args.model),
            output_dir=Path(args.output_dir),
            allow_overwrite=args.allow_overwrite,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif args.cmd == "c04-inspect-windows":
        from .c04_windows import C04WindowIndex, C04WindowSpec

        spec = C04WindowSpec(
            history_frames=args.history,
            forecast_frames=args.forecast,
            stride=args.stride,
        )
        index = C04WindowIndex(spec)
        index.add_dataset(args.dataset)
        data = index.to_dict()
        if args.out:
            index.save_manifest(args.out)
            data["saved_manifest"] = str(args.out)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.cmd == "c04-evaluate-baselines":
        from .c04_forecasting import evaluate_c04_dataset_baselines
        from .c04_windows import C04WindowIndex, C04WindowSpec

        spec = C04WindowSpec(
            history_frames=args.history,
            forecast_frames=args.forecast,
            stride=args.stride,
        )
        index = C04WindowIndex(spec)
        index.add_dataset(args.dataset)
        report = evaluate_c04_dataset_baselines(index, method=args.method)
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
