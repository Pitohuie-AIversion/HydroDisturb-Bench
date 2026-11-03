"""Execute Stage S1-A2 continuous simulation to t = 200s and export full diagnostics."""

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
    run_study,
)

BASELINE_CASE = Path("case/cylinder_flow.mph")
BENCHMARK_MODEL = Path("models/cylinder_2d/re100_benchmark.mph")
TRIAL_DIR = Path("runs/re100_s1a2_trial")
SOLVED_MPH = TRIAL_DIR / "re100_s1a2_solved.mph"

EXPECTED_CASE_HASH = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
EXPECTED_S0_HASH = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"


def main() -> int:
    print("=================================================================")
    print("HydroDisturb-Bench: Stage S1-A2 (t = 200s) Continuous Solver Run")
    print("=================================================================")

    # 1. Pre-execution hash check
    h_case_before = compute_file_hash(BASELINE_CASE)
    h_s0_before = compute_file_hash(BENCHMARK_MODEL)
    print(f"1. Baseline Case SHA-256:     {h_case_before}")
    print(f"   S0 Benchmark SHA-256:      {h_s0_before}")
    assert h_case_before == EXPECTED_CASE_HASH, f"Baseline Case hash mutated! {h_case_before}"
    assert h_s0_before == EXPECTED_S0_HASH, f"S0 Benchmark hash mutated! {h_s0_before}"
    print("   [OK] Source models strictly immutable.")

    TRIAL_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Run continuous study to t = 200s (dt = 0.2s, 1001 steps)
    tlist = "range(0, 0.2, 200)"
    print(f"\n2. Starting continuous CFD solve: t in [0, 200s], dt = 0.2s ({tlist})...")
    print(f"   Target model path: {SOLVED_MPH}")
    t0 = time.time()
    solve_res = run_study(
        model_path=BENCHMARK_MODEL,
        output_path=SOLVED_MPH,
        study_tag="std1",
        tlist_override=tlist,
        allow_overwrite=True,
    )
    t1 = time.time()
    wall_clock = t1 - t0
    print(f"   [SOLVER SUCCESS] Wall-clock time: {wall_clock:.2f} s ({wall_clock / 60:.2f} min)")

    sol_stats = solve_res.get("solution_stats", {})
    print(f"   Num DOFs:        {sol_stats.get('num_dofs')}")
    print(f"   Num Timesteps:   {sol_stats.get('num_timesteps')}")
    print(f"   Time Range:      [{sol_stats.get('time_min')}, {sol_stats.get('time_max')}] s")
    print(f"   Has NaN or Inf:  {sol_stats.get('has_nan_or_inf')}")
    print(f"   Has Physical:    {sol_stats.get('has_physical_field')}")

    # 3. Post-execution baseline immutability check
    h_case_after = compute_file_hash(BASELINE_CASE)
    h_s0_after = compute_file_hash(BENCHMARK_MODEL)
    assert h_case_after == EXPECTED_CASE_HASH, "Baseline Case mutated after solve!"
    assert h_s0_after == EXPECTED_S0_HASH, "S0 Benchmark mutated after solve!"
    print("   [OK] Source models unmodified after solve.")

    # 4. Export S1-A2 diagnostics
    print(f"\n3. Exporting S1-A2 diagnostics into {TRIAL_DIR}...")
    export_t0 = time.time()
    diag_res = export_s1_diagnostics(
        model_path=SOLVED_MPH,
        output_dir=TRIAL_DIR,
        allow_overwrite=True,
        expected_steps=1001,
        expected_dt=0.20,
        expected_t_final=200.0,
        versioned=True,
    )
    export_t1 = time.time()
    print(f"   [EXPORT SUCCESS] Wall-clock time: {export_t1 - export_t0:.2f} s")
    print(f"   Manifest: {diag_res.get('manifest_file')}")
    print(f"   Published files: {len(diag_res.get('published_files', []))} artifacts")
    for f in diag_res.get("published_files", []):
        fp = TRIAL_DIR / f
        sz = fp.stat().st_size if fp.exists() else 0
        print(f"     - {f} ({sz} bytes)")

    # 5. Record solver metadata json
    meta = {
        "stage": "S1-A2",
        "description": "Continuous long-time cylinder flow trial run to t = 200s",
        "solve_wall_clock_sec": wall_clock,
        "export_wall_clock_sec": export_t1 - export_t0,
        "time_config": {
            "t_start": 0.0,
            "t_final": 200.0,
            "dt": 0.20,
            "num_steps": 1001,
            "tlist_expr": tlist,
        },
        "solver_settings": {
            "timemethod": "bdf",
            "tstepsbdf": "intermediate",
            "maxorder": 2,
            "minorder": 1,
            "rtol": 0.001,
        },
        "solution_stats": sol_stats,
        "model_hashes": {
            "case_cylinder_flow_sha256": h_case_after,
            "re100_benchmark_sha256": h_s0_after,
            "re100_s1a2_solved_sha256": compute_file_hash(SOLVED_MPH),
        },
    }
    meta_path = TRIAL_DIR / "solver_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved solver metadata: {meta_path}")
    print("\n=================================================================")
    print("Stage S1-A2 Simulation & Diagnostic Export Complete: SUCCESS")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
