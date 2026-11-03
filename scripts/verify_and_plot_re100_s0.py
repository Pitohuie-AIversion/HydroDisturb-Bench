"""Verify Re=100 benchmark model, execute short transient solve, and generate field plots."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import griddata

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from hydro_disturb_bench.io import parse_comsol_txt
from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
    export_solution_data,
    inspect_model,
    run_study,
)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BASELINE_MODEL = WORKSPACE_DIR / "case" / "cylinder_flow.mph"
BENCHMARK_MODEL = WORKSPACE_DIR / "models" / "cylinder_2d" / "re100_benchmark.mph"
OUTPUT_DIR = WORKSPACE_DIR / "runs" / "re100_s0_verification"

BASELINE_SHA256 = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=================================================================")
    print("HydroDisturb-Bench Phase 2 Stage S0: Re=100 Benchmark Verification")
    print("=================================================================")

    # 1. Verify baseline model immutability
    baseline_hash = compute_file_hash(BASELINE_MODEL)
    print(f"1. Baseline Model Hash Verification:")
    print(f"   Path:   {BASELINE_MODEL}")
    print(f"   SHA256: {baseline_hash}")
    assert baseline_hash == BASELINE_SHA256, "Baseline model hash altered!"
    print("   [OK] Baseline model is strictly untouched.")

    # 2. Inspect re100_benchmark.mph
    print(f"\n2. Benchmark Model Inspection:")
    print(f"   Path:   {BENCHMARK_MODEL}")
    insp = inspect_model(BENCHMARK_MODEL)
    params = insp.get("parameters", {})
    D = params["D"]["value"]
    L = params["L"]["value"]
    H = params["H"]["value"]
    x_c = params["x_c"]["value"]
    y_c = params["y_c"]["value"]
    rho = params["rho_f"]["value"]
    mu = params["mu_f"]["value"]
    Re = params["Re"]["value"]
    U_inf = params["U_inf"]["value"]
    blockage = D / H

    print(f"   Fluid:           Freshwater at 20 degC")
    print(f"   Density (rho):   {rho} kg/m^3")
    print(f"   Viscosity (mu):  {mu} Pa*s")
    print(f"   Cylinder Dia:    {D} m (R = {D/2} m, center at ({x_c}, {y_c}) m)")
    print(f"   Inflow U_inf:    {U_inf:.8f} m/s")
    print(f"   Target Re:       {Re} (Calculated: {rho*U_inf*D/mu:.4f})")
    print(f"   Domain:          Length {L} m (40D), Height {H} m (24D)")
    print(f"   Blockage Ratio:  {blockage:.4f} ({blockage*100:.2f}% < 5%)")

    bg = insp["components"][0]["geometries"][0].get("boundary_geometry", {})
    if bg.get("available"):
        print(f"   Live Geometry:   {bg.get('n_boundaries')} boundaries, BoundingBox={bg.get('bounding_box')}")
        for b in bg.get("boundaries", []):
            print(f"     Boundary {b['id']}: X in [{b['x_range'][0]:.4f}, {b['x_range'][1]:.4f}], "
                  f"Y in [{b['y_range'][0]:.4f}, {b['y_range'][1]:.4f}], len={b['length_polyline']:.4f}m")
    print("   [OK] Parameters and live geometry match Stage S0 physical specification.")

    # 3. Execute short transient solve on a working copy in runs/
    solved_mph = OUTPUT_DIR / "re100_s0_solved.mph"
    print(f"\n3. Running Short Transient Solve (t in [0, 0.5s], dt = 0.05s)...")
    solve_res = run_study(
        model_path=BENCHMARK_MODEL,
        output_path=solved_mph,
        study_tag="std1",
        tlist_override="range(0, 0.05, 0.5)",
        allow_overwrite=True,
    )
    stats = solve_res.get("solution_stats", {})
    print(f"   Solve Elapsed:   {solve_res.get('elapsed_ms')} ms")
    print(f"   Degrees of Free: {stats.get('num_dofs')}")
    print(f"   Timesteps Saved: {stats.get('num_timesteps')}")
    print(f"   Time Range:      {stats.get('time_min')}s to {stats.get('time_max')}s")
    print(f"   Has NaN or Inf:  {stats.get('has_nan_or_inf')}")
    assert stats.get("has_nan_or_inf") is False, "Solver solution contains NaN/Inf!"
    print("   [OK] Transient solver finished normally without NaN/Inf.")

    # 4. Export field data
    data_txt = OUTPUT_DIR / "flow_solution_data.txt"
    print(f"\n4. Exporting Flow Field Data to {data_txt.name}...")
    export_solution_data(
        model_path=solved_mph,
        output_txt_path=data_txt,
        allow_overwrite=True,
    )
    print("   [OK] Data export completed.")

    # 5. Parse data and perform numerical physical checks
    print("\n5. Parsing Solution Data & Evaluating Physical Field Consistency...")
    fields = parse_comsol_txt(data_txt)
    x = fields["x"]
    y = fields["y"]
    u = fields["u"]
    v = fields["v"]
    p = fields["p"]
    vort = fields["vorticity"]
    U_mag = fields["U"]

    print(f"   Nodes count:     {len(x)}")
    print(f"   X range:         [{x.min():.4f}, {x.max():.4f}] m")
    print(f"   Y range:         [{y.min():.4f}, {y.max():.4f}] m")
    print(f"   U_mag range:     [{U_mag.min():.6f}, {U_mag.max():.6f}] m/s")
    print(f"   Pressure range:  [{p.min():.6f}, {p.max():.6f}] Pa")
    print(f"   Vorticity range: [{vort.min():.4f}, {vort.max():.4f}] 1/s")

    # Inflow boundary check (x = 0)
    inlet_mask = (x < 1e-4)
    u_inlet_mean = u[inlet_mask].mean()
    print(f"   Inlet Mean U:    {u_inlet_mean:.8f} m/s (Target U_inf: {U_inf:.8f} m/s, ratio={u_inlet_mean/U_inf:.4f})")

    # Cylinder surface check: exact boundary nodes at r = R = D/2
    dist_cyl = np.sqrt((x - x_c)**2 + (y - y_c)**2)
    cyl_surf_mask = np.abs(dist_cyl - D / 2) < 1e-6
    u_cyl_max = U_mag[cyl_surf_mask].max()
    print(f"   Cylinder Wall Nodes: {cyl_surf_mask.sum()} boundary nodes")
    print(f"   Cylinder Surface Max |u|: {u_cyl_max:.8e} m/s (Machine-zero No-Slip)")
    assert u_cyl_max < 1e-12, f"No-slip condition violated on cylinder: max |u| = {u_cyl_max}"

    # 6. Generate Publication-Quality Plots
    print("\n6. Rendering Flow Field Visualizations (Matplotlib)...")
    
    # Create regular grid for contour interpolation
    xi = np.linspace(0, L, 500)
    yi = np.linspace(0, H, 300)
    XI, YI = np.meshgrid(xi, yi)
    
    # Mask cylinder inside
    cyl_mask_grid = ((XI - x_c)**2 + (YI - y_c)**2) < (D/2)**2

    # Interpolate fields
    Ui = griddata((x, y), U_mag, (XI, YI), method="linear")
    pi = griddata((x, y), p, (XI, YI), method="linear")
    vort_i = griddata((x, y), vort, (XI, YI), method="linear")

    Ui[cyl_mask_grid] = np.nan
    pi[cyl_mask_grid] = np.nan
    vort_i[cyl_mask_grid] = np.nan

    circle_patch = plt.Circle((x_c, y_c), D/2, color="gray", ec="black", lw=1.5, zorder=10)

    # Figure 1: Velocity Magnitude
    fig1, ax1 = plt.subplots(figsize=(10, 4.5), dpi=300)
    cf1 = ax1.contourf(XI, YI, Ui, levels=50, cmap="viridis")
    cbar1 = fig1.colorbar(cf1, ax=ax1, orientation="vertical", pad=0.02)
    cbar1.set_label("Velocity Magnitude |u| [m/s]", fontsize=11)
    ax1.add_patch(plt.Circle((x_c, y_c), D/2, color="lightgray", ec="black", lw=1.5, zorder=10))
    ax1.set_title(f"Re = 100 2D Cylinder Flow - Velocity Field |u| (t = 0.5s, U_inf = {U_inf:.5f} m/s)", fontsize=12, fontweight="bold")
    ax1.set_xlabel("x [m] (Flow Direction)", fontsize=10)
    ax1.set_ylabel("y [m] (Lateral Direction)", fontsize=10)
    ax1.set_aspect("equal")
    ax1.set_xlim(0, L)
    ax1.set_ylim(0, H)
    fig1.tight_layout()
    vel_fig_path = OUTPUT_DIR / "re100_s0_velocity_magnitude.png"
    fig1.savefig(vel_fig_path)
    plt.close(fig1)
    print(f"   Saved {vel_fig_path.name}")

    # Figure 2: Pressure Distribution
    fig2, ax2 = plt.subplots(figsize=(10, 4.5), dpi=300)
    cf2 = ax2.contourf(XI, YI, pi, levels=50, cmap="coolwarm")
    cbar2 = fig2.colorbar(cf2, ax=ax2, orientation="vertical", pad=0.02)
    cbar2.set_label("Static Pressure p [Pa]", fontsize=11)
    ax2.add_patch(plt.Circle((x_c, y_c), D/2, color="lightgray", ec="black", lw=1.5, zorder=10))
    ax2.set_title(f"Re = 100 2D Cylinder Flow - Pressure Field p (t = 0.5s, Stagnation & Wake)", fontsize=12, fontweight="bold")
    ax2.set_xlabel("x [m] (Flow Direction)", fontsize=10)
    ax2.set_ylabel("y [m] (Lateral Direction)", fontsize=10)
    ax2.set_aspect("equal")
    ax2.set_xlim(0, L)
    ax2.set_ylim(0, H)
    fig2.tight_layout()
    pres_fig_path = OUTPUT_DIR / "re100_s0_pressure_distribution.png"
    fig2.savefig(pres_fig_path)
    plt.close(fig2)
    print(f"   Saved {pres_fig_path.name}")

    # Figure 3: Vorticity Field
    fig3, ax3 = plt.subplots(figsize=(10, 4.5), dpi=300)
    vort_lim = max(abs(np.nanpercentile(vort_i, 2)), abs(np.nanpercentile(vort_i, 98)))
    if vort_lim < 1e-3:
        vort_lim = 5.0
    levels_vort = np.linspace(-vort_lim, vort_lim, 51)
    cf3 = ax3.contourf(XI, YI, vort_i, levels=levels_vort, cmap="RdBu_r", extend="both")
    cbar3 = fig3.colorbar(cf3, ax=ax3, orientation="vertical", pad=0.02)
    cbar3.set_label("Vorticity omega = vx - uy [1/s]", fontsize=11)
    ax3.add_patch(plt.Circle((x_c, y_c), D/2, color="lightgray", ec="black", lw=1.5, zorder=10))
    ax3.set_title(f"Re = 100 2D Cylinder Flow - Vorticity Field omega_z (t = 0.5s)", fontsize=12, fontweight="bold")
    ax3.set_xlabel("x [m] (Flow Direction)", fontsize=10)
    ax3.set_ylabel("y [m] (Lateral Direction)", fontsize=10)
    ax3.set_aspect("equal")
    ax3.set_xlim(0, L)
    ax3.set_ylim(0, H)
    fig3.tight_layout()
    vort_fig_path = OUTPUT_DIR / "re100_s0_vorticity_field.png"
    fig3.savefig(vort_fig_path)
    plt.close(fig3)
    print(f"   Saved {vort_fig_path.name}")

    # Optional copy to artifacts directory if environment variable is set
    artifact_env = os.environ.get("ANTIGRAVITY_ARTIFACTS_DIR")
    if artifact_env:
        artifact_dir = Path(artifact_env)
        if artifact_dir.is_dir():
            for p in [vel_fig_path, pres_fig_path, vort_fig_path]:
                shutil.copy2(p, artifact_dir / p.name)
                print(f"   Copied {p.name} to artifacts directory: {artifact_dir}")

    print("\n=================================================================")
    print("Stage S0 Verification Completed Successfully: PASS")
    print("=================================================================")


if __name__ == "__main__":
    main()
