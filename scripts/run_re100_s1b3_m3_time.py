"""Run M3 internal time-step halving study (const maximum internal step 0.05 s -> 0.025 s).

Solves the identical M3 native mesh with the internal BDF step cap reduced to 0.025 s.
All prior models remain strictly read-only; each invocation requires a new output directory.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
    export_s1_diagnostics,
    inspect_model,
    inspect_native_mesh,
    run_study,
)
from scripts.verify_and_compare_s1b1 import analyze_case, compare_cases, compute_demeaned_rms
from scripts.verify_s1b1_prechecks import run_prechecks

M3_BASELINE_DIR = ROOT / "runs/re100_s1b2_m3_20261105_verified"
M3_BASELINE_SOLVED = M3_BASELINE_DIR / "re100_s1b2_m3_solved.mph"
M3_MESH_MODEL = M3_BASELINE_DIR / "re100_s1b2_m3_mesh.mph"

PROTECTED_MODELS = (
    ROOT / "case/cylinder_flow.mph",
    ROOT / "models/cylinder_2d/re100_benchmark.mph",
    ROOT / "runs/re100_s1a_trial/re100_s1a_solved.mph",
    ROOT / "runs/re100_s1a2_trial/re100_s1a2_solved.mph",
    ROOT / "runs/re100_s1b1_trial/re100_s1b1_solved.mph",
    ROOT / "runs/re100_s1b1_case_c_20261104_verified/re100_s1b1_case_c_solved.mph",
    ROOT / "runs/re100_s1b1_case_d_20261104_verified/re100_s1b1_case_d_solved.mph",
    ROOT / "runs/re100_s1b2_m1_20261105_verified/re100_s1b2_m1_solved.mph",
    ROOT / "runs/re100_s1b2_m2_20261105_verified/re100_s1b2_m2_solved.mph",
    M3_BASELINE_SOLVED,
    M3_MESH_MODEL,
)


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def check_step_log(log_path: Path, cap: float = 0.025) -> dict:
    """Verify every logged accepted internal step, ensuring 0 < dt <= cap through t=200 s."""
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    pattern = re.compile(rf"^\s*(\d+)\s+({number})\s+({number})(?:\s|$)")
    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match and int(match[1]) > 0:
            rows.append((int(match[1]), float(match[2]), float(match[3])))
    if not rows or rows[-1][1] < 200.0 - 1e-8:
        raise RuntimeError("Missing complete internal-step log through t=200 s")
    if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("Internal-step IDs must be contiguous and unique from 1")
    if any(not math.isfinite(t) or not math.isfinite(dt) for _, t, dt in rows):
        raise RuntimeError("Non-finite internal-step log value")
    if any(a[1] >= b[1] for a, b in zip(rows, rows[1:])):
        raise RuntimeError("Internal-step times must be strictly increasing")
    cap_tol = cap * (1.0 + 1e-4)
    if any(dt <= 0 or dt > cap_tol for _, _, dt in rows):
        raise RuntimeError(f"Actual logged internal step violates 0 < dt <= {cap} s")
    late = [dt for _, at_time, dt in rows if at_time >= 160.0]
    if not late:
        raise RuntimeError("No actual late-time internal steps in solver log")
    return {
        "source": str(log_path),
        "logged_rows": len(rows),
        "last_step_number": rows[-1][0],
        "last_logged_time": rows[-1][1],
        "all_dt_range": [min(row[2] for row in rows), max(row[2] for row in rows)],
        "late_dt_range": [min(late), max(late)],
        "late_dt_mean": sum(late) / len(late),
        "sequential_unique_step_ids": True,
        "strictly_increasing_time": True,
        "cap_verified": True,
    }


def _check_m3_baseline_source(solver: dict) -> None:
    if float(solver["rtol"]) != 1e-4:
        raise RuntimeError("M3 baseline source rtol must be 1e-4")
    if solver["maxstepconstraintbdf"] != "const" or float(solver["maxstepbdf"]) != 0.05:
        raise RuntimeError("M3 baseline live readback must confirm const / 0.05 s")


def check_settings(before: dict, after: dict, tree_baseline: dict, tree_halved: dict) -> dict:
    """Verify identical mesh, physics, and rtol, with only maxstep changed to 0.025 s."""
    sol_b, sol_h = before["solver_details"], after["solver_details"]
    _check_m3_baseline_source(sol_b)
    if float(sol_h["rtol"]) != 1e-4:
        raise RuntimeError("M3 halved rtol must remain 1e-4")
    if sol_h["maxstepconstraintbdf"] != "const" or float(sol_h["maxstepbdf"]) != 0.025:
        raise RuntimeError("M3 halved live readback does not confirm const / 0.025 s")
    changed = {}
    for key in sorted(set(sol_b) | set(sol_h)):
        if key == "maxstepbdf":
            continue
        if sol_b.get(key) != sol_h.get(key):
            changed[f"solver.{key}"] = [sol_b.get(key), sol_h.get(key)]
    for key in ("mesh_details", "physics_details"):
        if before[key] != after[key]:
            changed[key] = [before[key], after[key]]
    for key in ("parameters", "components"):
        if tree_baseline[key] != tree_halved[key]:
            changed[key] = "Full model-tree readbacks differ"
    if changed:
        raise RuntimeError(f"Non-target setting mismatch: {changed}")
    return {
        "status": "PASS",
        "baseline_solver": sol_b,
        "halved_solver": sol_h,
        "equal_readbacks": ["non-target solver fields", "mesh_details", "physics_details", "parameters", "components"],
        "mesh_invariance_verified": True,
        "preservation_method": "Loaded M3 mesh model directly; run_study applies only maxstep=0.025 override",
    }


def classify_m3_time_comparison(stats_baseline: dict, stats_halved: dict) -> dict:
    """Compare baseline M3 (dt=0.05s) and halved M3 (dt=0.025s) with dedicated time-sensitivity phrasing."""
    if any(stats.get("num_cycles", 0) < 5 for stats in (stats_baseline, stats_halved)):
        return {
            "can_compare": False,
            "conclusion": "INSUFFICIENT_CYCLES",
            "acceptance_status": "INSUFFICIENT_CYCLES",
            "message": "M3 time-step comparison requires at least five stable complete cycles per case",
            "case_a": stats_baseline,
            "case_b": stats_halved,
            "phase_can_close": False,
        }
    if not stats_baseline.get("is_stable", False) or not stats_halved.get("is_stable", False):
        comp = compare_cases(stats_baseline, stats_halved, study_type="mesh")
        comp["acceptance_status"] = "UNSTABLE"
        comp["phase_can_close"] = False
        comp["scientific_summary"] = (
            "M3 time-step comparison halted at periodic stability check before accuracy evaluation."
        )
        return comp

    comp = compare_cases(stats_baseline, stats_halved, study_type="mesh")
    acceptance = "PASS" if comp["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE" else "SENSITIVE"
    comp["acceptance_status"] = acceptance
    comp["phase_can_close"] = acceptance == "PASS"

    c = comp.get("comparisons", {})
    st_rel = c.get("St_zc", {}).get("rel_diff_pct", 0.0)
    cd_rel = c.get("Cd_mean", {}).get("rel_diff_pct", 0.0)
    amp_rel = c.get("Cl_amp", {}).get("rel_diff_pct", 0.0)
    rms_rel = c.get("Cl_rms", {}).get("rel_diff_pct", 0.0)
    cost = comp.get("computational_cost", {})

    comp["scientific_summary"] = (
        f"M3 internal time-step halving study on identical M3 native mesh (34,752 elements, 88,483 DOFs) "
        f"at unchanged rtol=1e-4; max internal step cap halved from const 0.05 s to const 0.025 s. "
        f"Internal steps increased by {cost.get('step_ratio', 'N/A')} ({cost.get('steps_b')} vs {cost.get('steps_a')}), "
        f"solve time {cost.get('time_ratio', 'N/A')} ({cost.get('time_b_sec')}s vs {cost.get('time_a_sec')}s). "
        f"Over the last 5 verified stable cycles, all 4 physical metrics changed by: "
        f"St: {st_rel:+.4f}% (tol 1.0%), "
        f"Mean Cd: {cd_rel:+.4f}% (tol 1.0%), "
        f"Lift Amplitude: {amp_rel:+.4f}% (tol 1.0%), "
        f"De-meaned RMS: {rms_rel:+.4f}% (tol 1.0%). "
        f"Overall Conclusion: {comp['conclusion']} (Sensitive reasons: {comp['sensitive_reasons'] if comp['sensitive_reasons'] else 'None'}). "
        f"These differences represent sensitivity to internal time discretization on the M3 mesh, "
        f"not an exact-solution error bound."
    )
    return comp


def run_m3_time_study(
    output_dir: Path,
    baseline_dir: Path = M3_BASELINE_DIR,
    maxstep: str = "0.025",
) -> dict:
    """Execute M3 time-step halving study from existing M3 mesh."""
    output_dir = output_dir.resolve()
    baseline_dir = baseline_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    hashes_before = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS if path.exists()}
    report = {
        "status": "RUNNING",
        "output_dir": str(output_dir),
        "baseline_dir": str(baseline_dir),
        "maxstep_target": float(maxstep),
        "protected_hashes_before": hashes_before,
    }
    save_json(output_dir / "m3_time_report.json", report)

    try:
        baseline_solved = baseline_dir / "re100_s1b2_m3_solved.mph"
        baseline_mesh = baseline_dir / "re100_s1b2_m3_mesh.mph"
        if not baseline_solved.exists() or not baseline_mesh.exists():
            raise FileNotFoundError(f"Baseline M3 models missing in {baseline_dir}")

        tree_b = inspect_model(baseline_solved)
        save_json(output_dir / "baseline_m3_model_inspection.json", tree_b)
        transient_studies = [
            item["tag"] for item in tree_b["studies"]
            if any(feature["type"] == "Transient" for feature in item["features"])
        ]
        if len(transient_studies) != 1:
            raise RuntimeError(f"Expected one transient study in M3; found {transient_studies}")

        print("Checking baseline M3 prechecks...", flush=True)
        pre_b = run_prechecks(
            model_path=baseline_solved,
            wake_u_path=baseline_dir / "wake_u.txt",
            wake_v_path=baseline_dir / "wake_v.txt",
            output_dir=output_dir / "precheck_baseline_m3",
        )
        report["baseline_m3_prechecks"] = pre_b
        if pre_b["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Baseline M3 prechecks failed; halved study was not started")
        _check_m3_baseline_source(pre_b["precheck_b_solver_model"]["solver_details"])

        solved = output_dir / "re100_s1b3_m3_dt025_solved.mph"
        print(f"Solving M3 with halved maxstep={maxstep} s on identical M3 mesh...", flush=True)
        start = time.perf_counter()
        solution = run_study(
            model_path=baseline_mesh,
            output_path=solved,
            study_tag=transient_studies[0],
            maxstep_override=maxstep,
            allow_overwrite=False,
        )
        elapsed = time.perf_counter() - start
        save_json(output_dir / "solve_response.json", solution)

        log_path = output_dir / "solver_run.log"
        log_path.write_text(solution["log"], encoding="utf-8")
        report["actual_step_log"] = check_step_log(log_path, cap=float(maxstep))

        print("Exporting diagnostics (1001 steps, 5 vorticity snapshots)...", flush=True)
        export_response = export_s1_diagnostics(
            model_path=solved,
            output_dir=output_dir,
            expected_steps=1001,
            expected_dt=0.20,
            expected_t_final=200.0,
            snapshot_times=[10.0, 40.0, 100.0, 195.0, 200.0],
            allow_overwrite=False,
        )
        save_json(output_dir / "export_response.json", export_response)

        pre_h = run_prechecks(
            model_path=solved,
            wake_u_path=output_dir / "wake_u.txt",
            wake_v_path=output_dir / "wake_v.txt",
            output_dir=output_dir / "precheck_halved_m3",
        )
        report["halved_m3_prechecks"] = pre_h
        if pre_h["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Halved M3 probe prechecks failed")

        tree_h = inspect_model(solved)
        save_json(output_dir / "halved_m3_model_inspection.json", tree_h)

        report["settings_comparison"] = check_settings(
            pre_b["precheck_b_solver_model"], pre_h["precheck_b_solver_model"], tree_b, tree_h,
        )

        save_json(output_dir / "solver_metadata.json", {
            "case_id": "M3 (maxstep=0.025 s)",
            "solve_duration_sec": elapsed,
            "source_model": str(baseline_mesh),
            "source_sha256": solution["source_sha256"],
            "solved_model_sha256": solution["output_sha256"],
            **pre_h["precheck_b_solver_model"],
        })

        print("Analyzing limit cycle and computing 4 physical metrics...", flush=True)
        stats_b = analyze_case("M3 dt=0.05s (baseline)", baseline_dir)
        stats_h = analyze_case("M3 dt=0.025s (halved)", output_dir)
        comparison = classify_m3_time_comparison(stats_b, stats_h)
        report["physical_comparison"] = comparison

        if not comparison["can_compare"]:
            raise RuntimeError(comparison.get("message", "M3 time-step stable-cycle comparison failed"))

        u_inf = float(tree_b["parameters"]["U_inf"]["value"])
        try:
            from scripts.run_re100_s1b2_mesh import analyze_wake, compare_wake_statistics
            report["wake_comparison"] = compare_wake_statistics(
                analyze_wake(baseline_dir, stats_b["window"], u_inf),
                analyze_wake(output_dir, stats_h["window"], u_inf),
            )
        except Exception as w_err:
            report["wake_comparison"] = {"status": "SKIPPED", "error": str(w_err)}

        report["execution_status"] = "COMPLETE"
        report["status"] = comparison["acceptance_status"]
        report["time_sensitivity_phase_can_close"] = comparison["phase_can_close"]
        print(json.dumps(comparison["comparisons"], indent=2), flush=True)
        print(f"M3 time-step sensitivity acceptance: {report['status']}", flush=True)
        return report

    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = str(exc)
        report["error_details"] = getattr(exc, "details", "")
        (output_dir / "failure.log").write_text(
            traceback.format_exc() + "\n" + report["error_details"], encoding="utf-8"
        )
        raise
    finally:
        hashes_after = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS if path.exists()}
        report["protected_hashes_after"] = hashes_after
        report["protected_models_unchanged"] = hashes_before == hashes_after
        if hashes_before != hashes_after:
            report["status"] = "FAIL"
            report["immutability_error"] = "A protected model changed"
        save_json(output_dir / "m3_time_report.json", report)
        if hashes_before != hashes_after:
            raise RuntimeError("A protected model changed")


def recover_saved_m3_time_run(
    output_dir: Path,
    baseline_dir: Path = M3_BASELINE_DIR,
) -> dict:
    """Recover postprocessing and statistics from an already-solved M3 halved model."""
    output_dir = output_dir.resolve()
    baseline_dir = baseline_dir.resolve()
    solved = output_dir / "re100_s1b3_m3_dt025_solved.mph"
    solution_path = output_dir / "solve_response.json"
    if not solved.exists() or not solution_path.exists():
        raise FileNotFoundError(f"Missing solved model or solve_response.json in {output_dir}")

    solution = json.loads(solution_path.read_text(encoding="utf-8"))
    baseline_solved = baseline_dir / "re100_s1b2_m3_solved.mph"
    baseline_mesh = baseline_dir / "re100_s1b2_m3_mesh.mph"
    hashes_before = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS if path.exists()}

    report_path = output_dir / "m3_time_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {
        "status": "RUNNING", "output_dir": str(output_dir), "baseline_dir": str(baseline_dir),
    }

    tree_b = inspect_model(baseline_solved)
    pre_b = run_prechecks(
        model_path=baseline_solved,
        wake_u_path=baseline_dir / "wake_u.txt",
        wake_v_path=baseline_dir / "wake_v.txt",
        output_dir=output_dir / "precheck_baseline_m3",
    )
    report["baseline_m3_prechecks"] = pre_b

    # Export if not already exported
    if not (output_dir / "wake_u.txt").exists():
        export_response = export_s1_diagnostics(
            model_path=solved,
            output_dir=output_dir,
            expected_steps=1001,
            expected_dt=0.20,
            expected_t_final=200.0,
            snapshot_times=[10.0, 40.0, 100.0, 195.0, 200.0],
            allow_overwrite=False,
        )
        save_json(output_dir / "export_response.json", export_response)

    pre_h = run_prechecks(
        model_path=solved,
        wake_u_path=output_dir / "wake_u.txt",
        wake_v_path=output_dir / "wake_v.txt",
        output_dir=output_dir / "precheck_halved_m3",
    )
    report["halved_m3_prechecks"] = pre_h

    tree_h = inspect_model(solved)
    save_json(output_dir / "halved_m3_model_inspection.json", tree_h)
    report["settings_comparison"] = check_settings(
        pre_b["precheck_b_solver_model"], pre_h["precheck_b_solver_model"], tree_b, tree_h,
    )

    stats_b = analyze_case("M3 dt=0.05s (baseline)", baseline_dir)
    stats_h = analyze_case("M3 dt=0.025s (halved)", output_dir)
    comparison = classify_m3_time_comparison(stats_b, stats_h)
    report["physical_comparison"] = comparison

    u_inf = float(tree_b["parameters"]["U_inf"]["value"])
    from scripts.run_re100_s1b2_mesh import analyze_wake, compare_wake_statistics
    report["wake_comparison"] = compare_wake_statistics(
        analyze_wake(baseline_dir, stats_b["window"], u_inf),
        analyze_wake(output_dir, stats_h["window"], u_inf),
    )

    report["execution_status"] = "COMPLETE"
    report["status"] = comparison["acceptance_status"]
    report["time_sensitivity_phase_can_close"] = comparison["phase_can_close"]
    hashes_after = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS if path.exists()}
    report["protected_hashes_after"] = hashes_after
    report["protected_models_unchanged"] = hashes_before == hashes_after
    save_json(output_dir / "m3_time_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M3 internal time-step halving study (0.05s -> 0.025s)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Isolated output directory")
    parser.add_argument("--baseline-dir", type=Path, default=M3_BASELINE_DIR, help="M3 baseline directory")
    parser.add_argument("--maxstep", type=str, default="0.025", help="Max internal step override")
    parser.add_argument("--postprocess-only", action="store_true", help="Recover/re-run postprocessing on existing solved model")
    args = parser.parse_args()

    try:
        if args.postprocess_only:
            report = recover_saved_m3_time_run(args.output_dir, args.baseline_dir)
        else:
            report = run_m3_time_study(args.output_dir, args.baseline_dir, args.maxstep)
        return 0 if report.get("status") == "PASS" else 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

