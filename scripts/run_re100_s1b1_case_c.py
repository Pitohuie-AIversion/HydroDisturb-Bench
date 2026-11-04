"""Run Case C after strict probe provenance checks, then compare B/C.

Only the internal BDF step cap changes from Case B: const, 0.05 s.
Every invocation uses a new directory; previous runs are never reused.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
import uuid

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

CASE_B_DIR = ROOT / "runs/re100_s1b1_trial"
CASE_B_MODEL = CASE_B_DIR / "re100_s1b1_solved.mph"
PROTECTED_MODELS = (
    ROOT / "case/cylinder_flow.mph",
    ROOT / "models/cylinder_2d/re100_benchmark.mph",
    ROOT / "runs/re100_s1a_trial/re100_s1a_solved.mph",
    ROOT / "runs/re100_s1a2_trial/re100_s1a2_solved.mph",
    CASE_B_MODEL,
)


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def check_step_log(log_path: Path) -> dict:
    """Check actual accepted Stepsize entries, including scientific notation."""
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    pattern = re.compile(rf"^\s*(\d+)\s+({number})\s+({number})(?:\s|$)")
    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            step, at_time, step_size = int(match[1]), float(match[2]), float(match[3])
            if step > 0:
                rows.append((step, at_time, step_size))
    if not rows or rows[-1][1] < 199.9:
        raise RuntimeError("Missing complete internal-step log through t=200 s")
    if any(dt <= 0 or dt > 0.0500001 for _, _, dt in rows):
        raise RuntimeError("Actual logged internal step violates 0 < dt <= 0.05 s")
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
        "cap_verified": True,
    }


def check_settings(before: dict, after: dict, tree_b: dict, tree_c: dict) -> dict:
    """Require equal available non-target readbacks; retain both raw trees."""
    sol_b, sol_c = before["solver_details"], after["solver_details"]
    if float(sol_b["rtol"]) != 1e-4 or float(sol_c["rtol"]) != 1e-4:
        raise RuntimeError("B/C rtol must both be 1e-4")
    if sol_c["maxstepconstraintbdf"] != "const" or float(sol_c["maxstepbdf"]) != 0.05:
        raise RuntimeError("Case C live readback does not confirm const / 0.05 s")
    changed = {}
    for key in sorted(set(sol_b) | set(sol_c)):
        if key in {"maxstepconstraintbdf", "maxstepbdf", "rtol"}:
            continue
        if sol_b.get(key) != sol_c.get(key):
            changed[f"solver.{key}"] = [sol_b.get(key), sol_c.get(key)]
    for key in ("mesh_details", "physics_details"):
        if before[key] != after[key]:
            changed[key] = [before[key], after[key]]
    for key in ("parameters", "components"):
        if tree_b[key] != tree_c[key]:
            changed[key] = "Full model-tree readbacks differ"
    if changed:
        raise RuntimeError(f"Non-target setting mismatch: {changed}")
    return {
        "status": "PASS",
        "case_b_solver": sol_b,
        "case_c_solver": sol_c,
        "equal_readbacks": ["non-target solver fields", "mesh_details", "physics_details", "parameters", "components"],
        "preservation_method": "Loaded Case B directly; run_study applies only rtol and maxstep overrides",
        "readback_limits": "Component inspection covers physics selections and material tags; it does not enumerate every BC/material property. Unavailable physics fields are retained as UNOBTAINED.",
    }


def run_case_c(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    hashes_before = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS}
    report = {"status": "RUNNING", "output_dir": str(output_dir), "protected_hashes_before": hashes_before}
    save_json(output_dir / "case_c_report.json", report)
    try:
        tree_b = inspect_model(CASE_B_MODEL)
        save_json(output_dir / "case_b_model_inspection.json", tree_b)
        transient_studies = [
            item["tag"] for item in tree_b["studies"]
            if any(feature["type"] == "Transient" for feature in item["features"])
        ]
        if len(transient_studies) != 1:
            raise RuntimeError(f"Expected one transient study; found {transient_studies}")

        print("Checking Case B probe provenance before Case C solve...", flush=True)
        pre_b = run_prechecks(
            model_path=CASE_B_MODEL,
            wake_u_path=CASE_B_DIR / "wake_u.txt",
            wake_v_path=CASE_B_DIR / "wake_v.txt",
            output_dir=output_dir / "precheck_b",
        )
        report["case_b_prechecks"] = pre_b
        if pre_b["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Strict Case B prechecks failed; Case C was not started")
        if float(pre_b["precheck_b_solver_model"]["solver_details"]["rtol"]) != 1e-4:
            raise RuntimeError("Case B source rtol differs from 1e-4")

        solved = output_dir / "re100_s1b1_case_c_solved.mph"
        print("Solving Case C: rtol=1e-4, maxstepconstraintbdf=const, maxstepbdf=0.05...", flush=True)
        start = time.perf_counter()
        solution = run_study(
            model_path=CASE_B_MODEL,
            output_path=solved,
            study_tag=transient_studies[0],
            rtol_override="1e-4",
            maxstep_override="0.05",
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
        pre_c = run_prechecks(
            model_path=solved,
            wake_u_path=output_dir / "wake_u.txt",
            wake_v_path=output_dir / "wake_v.txt",
            output_dir=output_dir / "precheck_c",
        )
        report["case_c_prechecks"] = pre_c
        if pre_c["overall_prechecks_status"] != "PASS":
            raise RuntimeError("Strict Case C probe prechecks failed")
        tree_c = inspect_model(solved)
        save_json(output_dir / "case_c_model_inspection.json", tree_c)
        report["settings_comparison"] = check_settings(
            pre_b["precheck_b_solver_model"], pre_c["precheck_b_solver_model"], tree_b, tree_c,
        )
        save_json(output_dir / "solver_metadata.json", {
            "case_id": "Case C", "solve_duration_sec": elapsed,
            "source_model": str(CASE_B_MODEL), "source_sha256": solution["source_sha256"],
            "solved_model_sha256": solution["output_sha256"],
            **pre_c["precheck_b_solver_model"],
        })
        stats_b = analyze_case("Case B (rtol=1e-4, auto)", CASE_B_DIR)
        stats_c = analyze_case("Case C (rtol=1e-4, const 0.05 s)", output_dir)
        comparison = compare_cases(stats_b, stats_c)
        comparison["scientific_summary"] = (
            "B/C comparison at fixed rtol=1e-4; only the internal maximum step constraint "
            "changes from auto to const 0.05 s. Four metrics use each case's last five "
            "complete cycles and the existing stability gate. " + comparison["conclusion"]
        )
        report["physical_comparison"] = comparison
        if not comparison["can_compare"] or any(
            stats.get("num_cycles", 0) < 5 for stats in (stats_b, stats_c)
        ):
            raise RuntimeError("B/C comparison requires at least five stable complete cycles per case")
        report["status"] = "PASS"
        print(json.dumps(comparison["comparisons"], indent=2), flush=True)
        return report
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = str(exc)
        raise
    finally:
        hashes_after = {str(path): compute_file_hash(path) for path in PROTECTED_MODELS}
        report["protected_hashes_after"] = hashes_after
        report["protected_models_unchanged"] = hashes_before == hashes_after
        if hashes_before != hashes_after:
            report["status"] = "FAIL"
            report["immutability_error"] = "A protected model changed"
        save_json(output_dir / "case_c_report.json", report)
        if hashes_before != hashes_after:
            raise RuntimeError("A protected model changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or ROOT / "runs" / f"re100_s1b1_case_c_{stamp}_{uuid.uuid4().hex[:8]}"
    print(f"Case C output: {output_dir}", flush=True)
    run_case_c(output_dir)
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
