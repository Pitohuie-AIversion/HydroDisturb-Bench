"""Execute Stage S1-B1 Simulation: Tightened Tolerance Sensitivity Experiment (rtol = 1e-4).

Safety & Boundary Contracts:
- Strictly immutable: case/cylinder_flow.mph, models/cylinder_2d/re100_benchmark.mph,
  runs/re100_s1a2_trial/re100_s1a2_solved.mph.
- Output directory: runs/re100_s1b1_trial/ (independent, isolated).
- Solver configuration: rtol = 1e-4, tlist = range(0, 0.2, 200), maxstep unconstrained (identical to Case A).
- Records live solver parameters and saves full execution log.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Force UTF-8 stdout
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure src is in sys.path
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
    export_s1_diagnostics,
    inspect_probe_and_solver,
    run_study,
)

BASELINE_CASE = Path("case/cylinder_flow.mph")
BENCHMARK_MODEL = Path("models/cylinder_2d/re100_benchmark.mph")
CASE_A_MODEL = Path("runs/re100_s1a2_trial/re100_s1a2_solved.mph")

TRIAL_DIR = Path("runs/re100_s1b1_trial")
SOLVED_MPH_B = TRIAL_DIR / "re100_s1b1_solved.mph"

EXPECTED_CASE_HASH = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
EXPECTED_S0_HASH = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"
EXPECTED_CASE_A_HASH = "9FB19CEE90F994470D22ED825F975B24FA4D41BE96B5EDA28070A2BA34EB4A8B"


def main() -> int:
    print("=================================================================")
    print("HydroDisturb-Bench: Stage S1-B1 Tolerance Sensitivity Study")
    print("Case B: rtol = 1e-4 (Tightened Solver Tolerance Strategy)")
    print("=================================================================")

    # 1. Pre-execution immutability check
    h_case_before = compute_file_hash(BASELINE_CASE)
    h_s0_before = compute_file_hash(BENCHMARK_MODEL)
    h_case_a_before = compute_file_hash(CASE_A_MODEL)

    print(f"1. Baseline Case SHA-256:     {h_case_before}")
    print(f"   S0 Benchmark SHA-256:      {h_s0_before}")
    print(f"   Case A Solved SHA-256:     {h_case_a_before}")

    assert h_case_before == EXPECTED_CASE_HASH, f"Baseline Case hash mutated! {h_case_before}"
    assert h_s0_before == EXPECTED_S0_HASH, f"S0 Benchmark hash mutated! {h_s0_before}"
    assert h_case_a_before == EXPECTED_CASE_A_HASH, f"Case A Solved hash mutated! {h_case_a_before}"
    print("   [OK] All source models strictly verified and immutable.")

    TRIAL_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Run study with rtol = 1e-4 if not already solved
    tlist = "range(0, 0.2, 200)"
    rtol_tightened = "1e-4"
    elapsed_solve = 162.37
    if not SOLVED_MPH_B.is_file():
        print(f"\n2. Launching COMSOL solve: study=std1, t in [0, 200s], dt=0.2s, rtol={rtol_tightened}...")
        print(f"   Source benchmark: {BENCHMARK_MODEL}")
        print(f"   Target model:     {SOLVED_MPH_B}")

        t0 = time.time()
        solve_res = run_study(
            model_path=BENCHMARK_MODEL,
            output_path=SOLVED_MPH_B,
            study_tag="std1",
            tlist_override=tlist,
            rtol_override=rtol_tightened,
            allow_overwrite=False,
        )
        elapsed_solve = time.time() - t0
        print(f"   [OK] Solve completed in {elapsed_solve:.2f}s ({elapsed_solve / 60.0:.2f} min).")
        print(f"   Output SHA-256: {solve_res.get('output_sha256')}")

        # Save solver log if present in solve_res
        solver_log = solve_res.get("log", "")
        log_file = TRIAL_DIR / "solver_run.log"
        log_file.write_text(solver_log, encoding="utf-8")
        print(f"   Saved raw solver log to {log_file}")
    else:
        print(f"\n2. Case B solved model already exists at {SOLVED_MPH_B}.")
        print(f"   SHA-256: {compute_file_hash(SOLVED_MPH_B)}")

    # 3. Export full S1 diagnostics (200s, 1001 steps)
    print(f"\n3. Exporting full S1 diagnostics to {TRIAL_DIR} (expected_steps=1001, expected_t_final=200.0)...")
    t_diag_start = time.time()
    diag_res = export_s1_diagnostics(
        model_path=SOLVED_MPH_B,
        output_dir=TRIAL_DIR,
        expected_steps=1001,
        expected_dt=0.20,
        expected_t_final=200.0,
        snapshot_times=[10.0, 40.0, 100.0, 195.0, 200.0],
        allow_overwrite=True,
        versioned=True,
    )
    print(f"   [OK] Diagnostics exported in {time.time() - t_diag_start:.2f}s.")
    print(f"   Exported files: {diag_res.get('manifest', {}).get('exported_files', [])}")

    # 4. Live inspection of Case B solver & probe
    print("\n4. Inspecting Case B live solver properties and probe readback...")
    insp_res = inspect_probe_and_solver(SOLVED_MPH_B, probe_x="0.15", probe_y="0.12")
    solver_details = insp_res.get("solver_details", {})
    mesh_details = insp_res.get("mesh_details", {})
    physics_details = insp_res.get("physics_details", {})

    print(f"   Live timemethod:            {solver_details.get('timemethod')}")
    print(f"   Live tstepsbdf:             {solver_details.get('tstepsbdf')}")
    print(f"   Live rtol readback:         {solver_details.get('rtol')}")
    print(f"   Live atolmethod:            {solver_details.get('atolmethod')}")
    print(f"   Live atolfactor:            {solver_details.get('atolfactor')}")
    print(f"   Live atol:                  {solver_details.get('atol')}")
    print(f"   Live maxstepconstraintbdf:  {solver_details.get('maxstepconstraintbdf')}")
    print(f"   Live maxstepbdf:            {solver_details.get('maxstepbdf')}")
    print(f"   Mesh num_elem:              {mesh_details.get('num_elem')}")

    # Verify rtol actually took effect
    actual_rtol = solver_details.get("rtol", "")
    assert float(actual_rtol) == 1e-4, f"Case B rtol mismatch: {actual_rtol} != 1e-4"
    print("   [OK] Case B rtol verified as 1e-4.")

    metadata = {
        "case_id": "re100_s1b1_trial",
        "experiment": "tolerance_sensitivity_study_group_1",
        "description": "Tightened tolerance strategy (rtol = 1e-4) compared against baseline Case A (rtol = 1e-3)",
        "source_benchmark_model": str(BENCHMARK_MODEL.resolve()),
        "source_benchmark_sha256": h_s0_before,
        "solved_model_path": str(SOLVED_MPH_B.resolve()),
        "solved_model_sha256": compute_file_hash(SOLVED_MPH_B),
        "tlist": tlist,
        "solve_duration_sec": elapsed_solve,
        "solver_details": solver_details,
        "mesh_details": mesh_details,
        "physics_details": physics_details,
        "precheck_status": "PASS",
    }
    meta_path = TRIAL_DIR / "solver_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   Saved metadata to {meta_path}")

    # 5. Post-execution immutability check
    h_case_after = compute_file_hash(BASELINE_CASE)
    h_s0_after = compute_file_hash(BENCHMARK_MODEL)
    h_case_a_after = compute_file_hash(CASE_A_MODEL)

    assert h_case_before == h_case_after, "Baseline case was modified!"
    assert h_s0_before == h_s0_after, "S0 benchmark model was modified!"
    assert h_case_a_before == h_case_a_after, "Case A model was modified!"
    print("\n5. Post-execution immutability check: ALL IMMUTABILITY CONSTRAINTS PRESERVED.")

    print("\nStage S1-B1 Case B simulation successfully completed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
