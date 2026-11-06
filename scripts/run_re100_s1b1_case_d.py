"""Run only Case D (const maximum internal step 0.025 s) and compare C/D.

Loads the accepted Case C model and changes only the internal BDF step cap.
All prior models remain read-only; each invocation requires a new output directory.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
    export_s1_diagnostics,
    inspect_model,
    run_study,
)
from scripts.verify_and_compare_s1b1 import analyze_case, compare_cases
from scripts.verify_s1b1_prechecks import run_prechecks

CASE_C_DIR = ROOT / "runs/re100_s1b1_case_c_20261104_verified"
CASE_C_MODEL = CASE_C_DIR / "re100_s1b1_case_c_solved.mph"
PROTECTED_MODELS = (
    ROOT / "case/cylinder_flow.mph",
    ROOT / "models/cylinder_2d/re100_benchmark.mph",
    ROOT / "runs/re100_s1a_trial/re100_s1a_solved.mph",
    ROOT / "runs/re100_s1a2_trial/re100_s1a2_solved.mph",
    ROOT / "runs/re100_s1b1_trial/re100_s1b1_solved.mph",
    CASE_C_MODEL,
)


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def check_step_log(log_path: Path) -> dict:
    """Verify every logged accepted D step, permitting adaptive steps below the cap."""
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
    if any(dt <= 0 or dt > 0.0250001 for _, _, dt in rows):
        raise RuntimeError("Actual logged internal step violates 0 < dt <= 0.025 s")
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


def _check_case_c_source(solver: dict) -> None:
    if float(solver["rtol"]) != 1e-4:
        raise RuntimeError("Case C source rtol must be 1e-4")
    if solver["maxstepconstraintbdf"] != "const" or float(solver["maxstepbdf"]) != 0.05:
        raise RuntimeError("Case C live readback must confirm const / 0.05 s")


def check_settings(before: dict, after: dict, tree_c: dict, tree_d: dict) -> dict:
    """Require the accepted C reference and D cap, with equal non-target readbacks."""
    sol_c, sol_d = before["solver_details"], after["solver_details"]
    _check_case_c_source(sol_c)
    if float(sol_d["rtol"]) != 1e-4:
        raise RuntimeError("Case D rtol must remain 1e-4")
    if sol_d["maxstepconstraintbdf"] != "const" or float(sol_d["maxstepbdf"]) != 0.025:
        raise RuntimeError("Case D live readback does not confirm const / 0.025 s")
    changed = {}
    for key in sorted(set(sol_c) | set(sol_d)):
        if key == "maxstepbdf":
            continue
        if sol_c.get(key) != sol_d.get(key):
            changed[f"solver.{key}"] = [sol_c.get(key), sol_d.get(key)]
    for key in ("mesh_details", "physics_details"):
        if before[key] != after[key]:
            changed[key] = [before[key], after[key]]
    for key in ("parameters", "components"):
        if tree_c[key] != tree_d[key]:
            changed[key] = "Full model-tree readbacks differ"
    if changed:
        raise RuntimeError(f"Non-target setting mismatch: {changed}")
    return {
        "status": "PASS",
        "case_c_solver": sol_c,
        "case_d_solver": sol_d,
        "equal_readbacks": ["non-target solver fields", "mesh_details", "physics_details", "parameters", "components"],
        "preservation_method": "Loaded Case C directly; run_study applies only maxstep override",
        "readback_limits": "Component inspection covers physics selections and material tags; it does not enumerate every BC/material property. Unavailable physics fields are retained as UNOBTAINED.",
    }


def classify_comparison(stats_c: dict, stats_d: dict) -> dict:
    """Keep the established stable-cycle and four-metric 1% acceptance rules."""
    if any(stats.get("num_cycles", 0) < 5 for stats in (stats_c, stats_d)):
        comparison = {
            "can_compare": False, "conclusion": "INSUFFICIENT_CYCLES",
            "message": "C/D comparison requires at least five stable complete cycles per case",
            "case_a": stats_c, "case_b": stats_d,
        }
        acceptance = "INSUFFICIENT_CYCLES"
    elif not stats_c.get("is_stable", False) or not stats_d.get("is_stable", False):
        comparison = compare_cases(stats_c, stats_d)
        acceptance = "UNSTABLE"
    else:
        comparison = compare_cases(stats_c, stats_d)
        acceptance = "PASS" if comparison["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE" else "SENSITIVE"
    comparison["acceptance_status"] = acceptance
    comparison["phase_can_close"] = acceptance == "PASS"
    comparison["scientific_summary"] = (
        "C/D comparison at fixed rtol=1e-4 and unchanged associated absolute-tolerance strategy; "
        "only the adaptive internal maximum step cap changes from const 0.05 s to const 0.025 s. "
        "The existing four-metric 1% thresholds and stable full-cycle statistics apply. "
        + comparison["conclusion"]
        + ". These changes are sensitivity to the tested caps, not error relative to the exact solution."
    )
    return comparison


def run_case_d(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    hashes_before = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS}
    report = {"status": "RUNNING", "output_dir": str(output_dir), "protected_hashes_before": hashes_before}
    save_json(output_dir / "case_d_report.json", report)
    try:
        tree_c = inspect_model(CASE_C_MODEL)
        save_json(output_dir / "case_c_model_inspection.json", tree_c)
        transient_studies = [
            item["tag"] for item in tree_c["studies"]
            if any(feature["type"] == "Transient" for feature in item["features"])
        ]
        if len(transient_studies) != 1:
            raise RuntimeError(f"Expected one transient study; found {transient_studies}")
        print("Reading accepted Case C before Case D solve (no C solve)...", flush=True)
        pre_c = run_prechecks(
            model_path=CASE_C_MODEL,
            wake_u_path=CASE_C_DIR / "wake_u.txt",
            wake_v_path=CASE_C_DIR / "wake_v.txt",
            output_dir=output_dir / "precheck_c",
        )
        report["case_c_prechecks"] = pre_c
        if pre_c["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Case C prechecks failed; Case D was not started")
        _check_case_c_source(pre_c["precheck_b_solver_model"]["solver_details"])

        solved = output_dir / "re100_s1b1_case_d_solved.mph"
        print("Solving Case D from C: unchanged rtol=1e-4, const maximum step 0.025 s...", flush=True)
        start = time.perf_counter()
        solution = run_study(
            model_path=CASE_C_MODEL,
            output_path=solved,
            study_tag=transient_studies[0],
            maxstep_override="0.025",
            allow_overwrite=False,
        )
        elapsed = time.perf_counter() - start
        save_json(output_dir / "solve_response.json", solution)
        log_path = output_dir / "solver_run.log"
        log_path.write_text(solution["log"], encoding="utf-8")
        report["actual_step_log"] = check_step_log(log_path)
        export_response = export_s1_diagnostics(
            model_path=solved, output_dir=output_dir,
            expected_steps=1001, expected_dt=0.20, expected_t_final=200.0,
            snapshot_times=[10.0, 40.0, 100.0, 195.0, 200.0],
            allow_overwrite=False,
        )
        save_json(output_dir / "export_response.json", export_response)
        pre_d = run_prechecks(
            model_path=solved,
            wake_u_path=output_dir / "wake_u.txt",
            wake_v_path=output_dir / "wake_v.txt",
            output_dir=output_dir / "precheck_d",
        )
        report["case_d_prechecks"] = pre_d
        if pre_d["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Case D probe prechecks failed")
        tree_d = inspect_model(solved)
        save_json(output_dir / "case_d_model_inspection.json", tree_d)
        report["settings_comparison"] = check_settings(
            pre_c["precheck_b_solver_model"], pre_d["precheck_b_solver_model"], tree_c, tree_d,
        )
        save_json(output_dir / "solver_metadata.json", {
            "case_id": "Case D", "solve_duration_sec": elapsed,
            "source_model": str(CASE_C_MODEL), "source_sha256": solution["source_sha256"],
            "solved_model_sha256": solution["output_sha256"],
            **pre_d["precheck_b_solver_model"],
        })
        stats_c = analyze_case("Case C (rtol=1e-4, const 0.05 s)", CASE_C_DIR)
        stats_d = analyze_case("Case D (rtol=1e-4, const 0.025 s)", output_dir)
        comparison = classify_comparison(stats_c, stats_d)
        report["physical_comparison"] = comparison
        if not comparison["can_compare"]:
            raise RuntimeError(comparison.get("message", "C/D stable-cycle comparison failed"))
        report["execution_status"] = "COMPLETE"
        report["status"] = comparison["acceptance_status"]
        report["time_sensitivity_phase_can_close"] = comparison["phase_can_close"]
        print(json.dumps(comparison["comparisons"], indent=2), flush=True)
        print(f"C/D acceptance: {report['status']}", flush=True)
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
        hashes_after = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS}
        report["protected_hashes_after"] = hashes_after
        report["protected_models_unchanged"] = hashes_before == hashes_after
        if hashes_before != hashes_after:
            report["status"] = "FAIL"
            report["immutability_error"] = "A protected model changed"
        save_json(output_dir / "case_d_report.json", report)
        if hashes_before != hashes_after:
            raise RuntimeError("A protected model changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or ROOT / "runs" / f"re100_s1b1_case_d_{stamp}_{uuid.uuid4().hex[:8]}"
    print(f"Case D output: {output_dir}", flush=True)
    report = run_case_d(output_dir)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
