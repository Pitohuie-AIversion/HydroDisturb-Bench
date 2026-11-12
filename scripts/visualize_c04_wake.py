#!/usr/bin/env python3
"""visualize_c04_wake.py - Publication-grade visualization for C04 3D/2D cylinder wake.

Generates:
1. reports/c04_wake_vorticity_evolution.png: 4-frame time sequence of vorticity field wz
2. reports/c04_wake_multiphysics_t3.png: 4-panel multiphysics dashboard (u, v, w, p) at t=0.18s
3. reports/c04_wake_evolution.gif: Animated GIF tracking wake vortex dynamics
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hydro_disturb_bench.sampling_3d import create_c04_2d_slice_grid, load_c04_dataset


def render_c04_visualizations(
    dataset_path: Path | str = "runs/c04_s1_slice_sequence.npz",
    output_dir: Path | str = "reports",
) -> dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ds_path = Path(dataset_path)
    if not ds_path.is_file():
        raise FileNotFoundError(f"C04 dataset not found at {ds_path}")

    ds = load_c04_dataset(ds_path)
    fields = ds["fields"]  # (4, 4, 96, 192) -> [frame, channel(u,v,w,p), y, x]
    times = ds["times"]
    solid_mask = ds["solid_mask"]

    grid = create_c04_2d_slice_grid(D=0.30)
    x = grid.x_1d
    y = grid.y_1d
    dx = x[1] - x[0]
    dy = y[1] - y[0]
    R_cyl = grid.cylinder_diameter / 2.0  # 0.15 m

    n_frames = len(times)

    # 1. Compute out-of-plane vorticity wz = dv/dx - du/dy
    vorticity_frames = []
    for k in range(n_frames):
        u_k = fields[k, 0].copy()
        v_k = fields[k, 1].copy()
        u_k[solid_mask] = 0.0
        v_k[solid_mask] = 0.0

        dv_dx = np.gradient(v_k, dx, axis=1)
        du_dy = np.gradient(u_k, dy, axis=0)
        wz = dv_dx - du_dy
        wz[solid_mask] = np.nan
        vorticity_frames.append(wz)

    # -------------------------------------------------------------
    # Figure 1: 4-frame time evolution of vorticity
    # -------------------------------------------------------------
    fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True, sharey=True, dpi=200)
    plt.subplots_adjust(hspace=0.25, left=0.08, right=0.92, top=0.93, bottom=0.07)

    wz_lim = 12.0  # s^-1
    extent = [x[0], x[-1], y[0], y[-1]]

    for k in range(n_frames):
        ax = axes[k]
        im = ax.imshow(
            vorticity_frames[k],
            extent=extent,
            origin="lower",
            cmap="RdBu_r",
            vmin=-wz_lim,
            vmax=wz_lim,
            interpolation="bilinear",
        )
        # Draw solid cylinder
        circ = Circle((0.0, 0.0), R_cyl, facecolor="dimgray", edgecolor="black", linewidth=1.2, zorder=5)
        ax.add_patch(circ)

        ax.set_ylabel("y [m]", fontsize=10, labelpad=4)
        ax.set_title(
            f"C04 Re=126,167 | Step {k+1} : Physical Time t = {times[k]:.2f} s "
            f"(Dimensionless t* = {times[k]*0.5/0.3:.2f})",
            fontsize=10,
            pad=4,
            fontweight="bold",
            loc="left",
        )
        ax.grid(True, linestyle=":", alpha=0.4)
        ax.set_aspect("equal")

    axes[-1].set_xlabel("x [m] (Streamwise)", fontsize=10)

    # Add shared colorbar on the right
    cbar_ax = fig.add_axes([0.93, 0.15, 0.02, 0.70])
    cbar = fig.colorbar(im, cax=cbar_ax)
    cbar.set_label("Vorticity $\\omega_z = \\partial v/\\partial x - \\partial u/\\partial y$ [s$^{-1}$]", fontsize=10)

    fig1_path = out_dir / "c04_wake_vorticity_evolution.png"
    fig.savefig(fig1_path, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {fig1_path}")

    # -------------------------------------------------------------
    # Figure 2: Multiphysics 4-panel dashboard at t = 0.18s
    # -------------------------------------------------------------
    fig2, axes2 = plt.subplots(2, 2, figsize=(12, 6.5), sharex=True, sharey=True, dpi=200)
    plt.subplots_adjust(hspace=0.25, wspace=0.15, left=0.07, right=0.92, top=0.92, bottom=0.08)

    t_idx = n_frames - 1  # Last frame (t = 0.18s)
    u_last = fields[t_idx, 0].copy()
    v_last = fields[t_idx, 1].copy()
    w_last = fields[t_idx, 2].copy()
    p_last = fields[t_idx, 3].copy()

    u_last[solid_mask] = np.nan
    v_last[solid_mask] = np.nan
    w_last[solid_mask] = np.nan
    p_last[solid_mask] = np.nan

    configs = [
        (axes2[0, 0], u_last, "viridis", "Streamwise Velocity u [m/s]", (0.0, 0.65)),
        (axes2[0, 1], v_last, "coolwarm", "Crossflow Velocity v [m/s]", (-0.35, 0.35)),
        (axes2[1, 0], w_last, "PuOr", "Spanwise Disturbance w [m/s]", (-0.05, 0.05)),
        (axes2[1, 1], p_last, "plasma", "Relative Pressure p [Pa]", (-150.0, 130.0)),
    ]

    for ax, data, cmap, title, (vmin, vmax) in configs:
        im = ax.imshow(
            data,
            extent=extent,
            origin="lower",
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            interpolation="bilinear",
        )
        circ = Circle((0.0, 0.0), R_cyl, facecolor="gray", edgecolor="black", linewidth=1.0, zorder=5)
        ax.add_patch(circ)
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.set_aspect("equal")
        ax.grid(True, linestyle=":", alpha=0.3)
        cbar = fig2.colorbar(im, ax=ax, fraction=0.032, pad=0.03)
        cbar.ax.tick_params(labelsize=8)

    axes2[1, 0].set_xlabel("x [m]", fontsize=10)
    axes2[1, 1].set_xlabel("x [m]", fontsize=10)
    axes2[0, 0].set_ylabel("y [m]", fontsize=10)
    axes2[1, 0].set_ylabel("y [m]", fontsize=10)

    fig2.suptitle(
        f"C04 Re=126,167 Multi-Physics Field Dashboard at t = {times[t_idx]:.2f} s (z = 1.50 m Center Slice)",
        fontsize=12,
        fontweight="bold",
        y=0.98,
    )

    fig2_path = out_dir / "c04_wake_multiphysics_t3.png"
    fig2.savefig(fig2_path, bbox_inches="tight")
    plt.close(fig2)
    print(f"Saved: {fig2_path}")

    # -------------------------------------------------------------
    # Figure 3: Animated GIF tracking wake vorticity dynamics
    # -------------------------------------------------------------
    gif_frames = []
    for k in range(n_frames):
        fig_gif, ax_gif = plt.subplots(figsize=(8, 3.2), dpi=150)
        im = ax_gif.imshow(
            vorticity_frames[k],
            extent=extent,
            origin="lower",
            cmap="RdBu_r",
            vmin=-wz_lim,
            vmax=wz_lim,
            interpolation="bilinear",
        )
        circ = Circle((0.0, 0.0), R_cyl, facecolor="dimgray", edgecolor="black", linewidth=1.2, zorder=5)
        ax_gif.add_patch(circ)

        ax_gif.set_title(
            f"C04 Re=126,167 | Vorticity $\\omega_z$ | t = {times[k]:.2f} s",
            fontsize=10,
            fontweight="bold",
        )
        ax_gif.set_xlabel("x [m]", fontsize=9)
        ax_gif.set_ylabel("y [m]", fontsize=9)
        ax_gif.set_aspect("equal")
        cbar = fig_gif.colorbar(im, ax=ax_gif, fraction=0.032, pad=0.03)
        cbar.set_label("$\\omega_z$ [s$^{-1}$]", fontsize=8)

        buf = io.BytesIO()
        fig_gif.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig_gif)
        buf.seek(0)
        gif_frames.append(Image.open(buf))

    gif_path = out_dir / "c04_wake_evolution.gif"
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=600,  # 600 ms per frame
        loop=0,
    )
    print(f"Saved: {gif_path}")

    return {
        "vorticity_evolution": fig1_path,
        "multiphysics_dashboard": fig2_path,
        "animated_gif": gif_path,
    }


if __name__ == "__main__":
    render_c04_visualizations()
