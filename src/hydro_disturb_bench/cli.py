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
        "--rtol", default="", help="Optional override for relative tolerance (e.g. '1e-4')"
    )
    j_solve.add_argument(
        "--maxstep",
        default="",
        help="Optional override for constant max BDF time step in seconds (e.g. '0.02')",
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

    obs_plan = sub.add_parser(
        "observation-plan",
        help="Generate standard sparse sensor observation layout (rake, surface ring, AUV)",
    )
    obs_plan.add_argument(
        "--type",
        required=True,
        choices=["rake-2d", "rake-3d", "surface-2d", "auv-2d"],
        help="Sensor layout geometry type",
    )
    obs_plan.add_argument("--out", required=True, help="Output path for sensor layout JSON")

    obs_samp = sub.add_parser(
        "observation-sample",
        help="Extract sparse sensor observation time series from 2D or 3D flow dataset",
    )
    obs_samp.add_argument("--layout", required=True, help="Path to sensor layout JSON")
    obs_samp.add_argument("--dataset", required=True, help="Path to 2D or 3D NPZ flow dataset")
    obs_samp.add_argument("--out", required=True, help="Output path for sampled observations JSON")
    obs_samp.add_argument("--snr", type=float, default=None, help="Optional SNR in dB for noise injection")

    recon_eval = sub.add_parser(
        "reconstruction-eval",
        help="Evaluate fluid-restricted reconstruction accuracy between reconstructed and true fields",
    )
    recon_eval.add_argument("--pred", required=True, help="Path to reconstructed field NPZ")
    recon_eval.add_argument("--true", required=True, help="Path to ground truth field NPZ")
    recon_eval.add_argument("--out", default=None, help="Optional output JSON path for metrics report")

    vort_eval = sub.add_parser(
        "vorticity-eval",
        help="Evaluate vorticity, Q-criterion, enstrophy, and incompressibility metrics on flow fields",
    )
    vort_eval.add_argument("--dataset", required=True, help="Path to 2D or 3D flow NPZ dataset")
    vort_eval.add_argument("--out", default=None, help="Optional output JSON path for kinematics report")

    inv_eval = sub.add_parser(
        "inversion-eval",
        help="Evaluate disturbance parameter inversion against ground truth",
    )
    inv_eval.add_argument("--truth", required=True, help="Path to ground truth disturbance JSON")
    inv_eval.add_argument("--pred", required=True, help="Path to predicted disturbance JSON")
    inv_eval.add_argument("--diameter", type=float, default=0.30, help="Cylinder diameter D in meters (default: 0.30)")
    inv_eval.add_argument("--out", default=None, help="Optional output JSON path for inversion report")

    bundle_create = sub.add_parser(
        "bundle-create",
        help="Package a flow dataset into a verified self-contained benchmark bundle",
    )
    bundle_create.add_argument("--dataset", required=True, help="Path to source flow NPZ")
    bundle_create.add_argument("--out", required=True, help="Destination bundle NPZ path")
    bundle_create.add_argument("--name", default="HydroDisturb-Bench", help="Benchmark suite name")

    bundle_verify = sub.add_parser(
        "bundle-verify",
        help="Verify cryptographic checksums and physical schema of a benchmark bundle",
    )
    bundle_verify.add_argument("--bundle", required=True, help="Path to benchmark bundle NPZ")

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
            rtol_override=args.rtol,
            maxstep_override=args.maxstep,
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
    elif args.cmd == "observation-plan":
        from .observation_operator import (
            make_auv_patrol_path,
            make_cylinder_surface_probes_2d,
            make_downstream_rake_2d,
            make_downstream_rake_3d,
        )

        if args.type == "rake-2d":
            layout = make_downstream_rake_2d(
                x_stations=[0.10, 0.15, 0.20, 0.30],
                y_min=0.04,
                y_max=0.20,
                n_probes_per_station=17,
            )
        elif args.type == "rake-3d":
            layout = make_downstream_rake_3d(
                x_stations=[0.60, 1.50, 3.00],
                y_min=-1.0,
                y_max=1.0,
                z_stations=[0.5, 1.5, 2.5],
                n_probes_y=9,
            )
        elif args.type == "surface-2d":
            layout = make_cylinder_surface_probes_2d(
                center_x=0.0,
                center_y=0.0,
                radius=0.005,
                n_sensors=16,
            )
        elif args.type == "auv-2d":
            layout, _ = make_auv_patrol_path(
                start_coords=(0.05, 0.04),
                end_coords=(0.35, 0.20),
                speed_m_s=0.05,
                t_start=180.0,
            )
        else:
            raise ValueError(f"Unknown layout type: {args.type}")

        layout.save_json(args.out)
        print(
            json.dumps(
                {"status": "SUCCESS", "layout": layout.to_dict(), "saved_to": str(args.out)},
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "observation-sample":
        import numpy as np

        from .observation_operator import ObservationOperator, SensorLayout

        layout = SensorLayout.load_json(args.layout)
        operator = ObservationOperator(layout)
        npz_data = np.load(args.dataset)

        channel_names = tuple(
            npz_data.get(
                "channel_names",
                ("u", "v", "p") if layout.dim == 2 else ("u", "v", "w", "p"),
            )
        )
        if layout.dim == 2:
            data = npz_data["data"]
            x_coords = npz_data["x"]
            y_coords = npz_data["y"]
            sampled = operator.sample_2d_field(
                data, x_coords, y_coords, channel_names=channel_names
            )
        else:
            data = npz_data["data"]
            x_coords = npz_data["x"]
            y_coords = npz_data["y"]
            z_coords = npz_data["z"]
            sampled = operator.sample_3d_field(
                data, x_coords, y_coords, z_coords, channel_names=channel_names
            )

        if args.snr is not None:
            sampled = operator.add_sensor_noise(sampled, snr_db=args.snr)

        result = {
            "layout_name": layout.name,
            "num_probes": layout.num_probes,
            "sampled_shape": list(sampled.shape),
            "snr_db": args.snr,
            "sample_summary": {
                "mean": float(np.nanmean(sampled)),
                "std": float(np.nanstd(sampled)),
                "min": float(np.nanmin(sampled)),
                "max": float(np.nanmax(sampled)),
            },
        }
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "reconstruction-eval":
        import numpy as np

        from .probabilistic_metrics import evaluate_field_reconstruction

        pred_npz = np.load(args.pred)
        true_npz = np.load(args.true)
        pred_field = pred_npz["data"]
        true_field = true_npz["data"]
        mask = true_npz.get("fluid_mask", true_npz.get("mask", None))
        if mask is not None:
            mask = np.asarray(mask, dtype=bool)

        channel_names = tuple(
            true_npz.get(
                "channel_names",
                ("u", "v", "w", "p") if pred_field.ndim >= 5 else ("u", "v", "p"),
            )
        )
        metrics = evaluate_field_reconstruction(
            pred_field, true_field, fluid_mask=mask, channel_names=channel_names
        )
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    elif args.cmd == "vorticity-eval":
        import numpy as np

        from .vorticity_dynamics import (
            compute_divergence_2d,
            compute_divergence_3d,
            compute_enstrophy,
            compute_q_criterion_2d,
            compute_q_criterion_3d,
            compute_vorticity_2d,
            compute_vorticity_3d,
            evaluate_incompressibility_metrics,
        )

        npz = np.load(args.dataset)
        data = npz["data"]
        x = npz["x"]
        y = npz["y"]
        mask = npz.get("solid_mask", None)

        if data.ndim == 4 or (data.ndim == 3 and "z" not in npz):
            # 2D field: shape (T, C, Ny, Nx) or (C, Ny, Nx)
            if data.ndim == 3:
                u, v = data[0], data[1]
            else:
                u, v = data[:, 0], data[:, 1]
            vort = compute_vorticity_2d(u, v, x, y, solid_mask=mask)
            div = compute_divergence_2d(u, v, x, y, solid_mask=mask)
            q = compute_q_criterion_2d(u, v, x, y, solid_mask=mask)
            enstrophy = compute_enstrophy(vort, solid_mask=mask)
            incomp = evaluate_incompressibility_metrics(div, solid_mask=mask)
            dim = 2
        else:
            # 3D field: shape (T, C, Nz, Ny, Nx) or (C, Nz, Ny, Nx)
            z = npz["z"]
            if data.ndim == 4:
                u, v, w = data[0], data[1], data[2]
            else:
                u, v, w = data[:, 0], data[:, 1], data[:, 2]
            wx, wy, wz = compute_vorticity_3d(u, v, w, x, y, z, solid_mask=mask)
            div = compute_divergence_3d(u, v, w, x, y, z, solid_mask=mask)
            q = compute_q_criterion_3d(u, v, w, x, y, z, solid_mask=mask)
            enstrophy = compute_enstrophy((wx, wy, wz), solid_mask=mask)
            incomp = evaluate_incompressibility_metrics(div, solid_mask=mask)
            dim = 3

        report = {
            "dataset": str(args.dataset),
            "dim": dim,
            "incompressibility": incomp,
            "enstrophy_mean": float(np.nanmean(enstrophy)),
            "enstrophy_max": float(np.nanmax(enstrophy)),
            "q_criterion_mean": float(np.nanmean(q)),
            "q_criterion_max": float(np.nanmax(q)),
        }
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.cmd == "inversion-eval":
        from .disturbance_inversion import (
            DisturbanceGroundTruth,
            DisturbancePrediction,
            evaluate_disturbance_inversion,
        )

        truth_data = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        pred_data = json.loads(Path(args.pred).read_text(encoding="utf-8"))
        truth = DisturbanceGroundTruth.from_dict(truth_data)
        pred = DisturbancePrediction.from_dict(pred_data)
        metrics = evaluate_disturbance_inversion(
            truth, pred, characteristic_length_D=args.diameter
        )
        report = metrics.to_dict()
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.cmd == "bundle-create":
        import numpy as np

        from .benchmark_bundle import create_benchmark_bundle

        npz = np.load(args.dataset)
        times = npz["times"]
        data = npz["data"]
        coords = {"x": npz["x"], "y": npz["y"]}
        if "z" in npz:
            coords["z"] = npz["z"]
        mask = npz.get("solid_mask", None)
        sdf = npz.get("sdf", None)

        out_path = create_benchmark_bundle(
            output_path=args.out,
            times=times,
            coords=coords,
            values=data,
            solid_mask=mask,
            sdf=sdf,
            benchmark_name=args.name,
        )
        print(f"Created benchmark bundle: {out_path}")
    elif args.cmd == "bundle-verify":
        from .benchmark_bundle import verify_benchmark_bundle

        res = verify_benchmark_bundle(args.bundle)
        print(json.dumps(res, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
