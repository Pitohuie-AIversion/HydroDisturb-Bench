#!/usr/bin/env python3
"""
render_c04_video_dashboard.py - Publication-Grade Multi-Physics Video Dashboard for C04 3D Flow.

Architecture:
- Top-Left:  2D Center-Plane Vorticity Field wz (RdBu_r colormap, [-15, 15] s^-1, cylinder masked)
- Top-Right: Streamwise Velocity Field u (viridis colormap, [-0.15, 0.65] m/s, separation & deficit)
- Bottom-Left: Synchronized Hydrodynamic Forces Cl(t) & Cd(t) with real-time vertical tracking cursor
- Bottom-Right: Limit-Cycle Phase Portrait (Cd vs Cl) with dynamic state orbit
- Top HUD: Physical Time t, Non-dimensional t*, Re=126,167, Seawater properties, St=0.207

Outputs:
1. E:\\cfd_3d\05_reports_viz\video_c04_shedding\\c04_vortex_shedding_dashboard.gif (Animated GIF, 79 frames @ 10 fps)
2. E:\\cfd_3d\05_reports_viz\video_c04_shedding\\c04_shedding_4phases.png (4-Phase Vortex Shedding Gallery)
3. E:\\cfd_3d\05_reports_viz\video_c04_shedding\frames\frame_*.png (Individual high-res dashboard frames)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hydro_disturb_bench.comsol_java.bridge import _run_bridge

MODEL_PATH = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3\B_rtol1e3.mph")
FORCE_CSV = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3\force_coefficients.csv")
OUT_DIR = Path(r"E:\cfd_3d\05_reports_viz\video_c04_shedding")
SLICES_DIR = OUT_DIR / "slices"
FRAMES_DIR = OUT_DIR / "frames"

# C04 Physical constants (seawater benchmark)
CYLINDER_DIAMETER = 0.30  # m
CYLINDER_RADIUS = 0.15    # m
INFLOW_U0 = 0.50          # m/s
RHO_SEAWATER = 1025.8944  # kg/m^3
NU_SEAWATER = 1.1889e-6   # m^2/s
RE_NUMBER = 126167

# Steps covering 3 full vortex shedding cycles (t = 50.04s to 59.40s)
# St ~ 0.2070 -> period T ~ 3.128s. 3 cycles = 9.38s. dt_step = 0.12s (step stride = 2)
START_STEP = 835
END_STEP = 991
STEP_STRIDE = 2
TARGET_STEPS = list(range(START_STEP, END_STEP + 1, STEP_STRIDE))


def export_slices_if_needed() -> list[Path]:
    """Export 2D slice files from COMSOL MPH using ComsolBridge in batch."""
    SLICES_DIR.mkdir(parents=True, exist_ok=True)

    needed_steps = []
    step_files = {}
    for sn in TARGET_STEPS:
        f = SLICES_DIR / f"slice_step_{sn}.txt"
        if not f.is_file() or f.stat().st_size < 1000:
            needed_steps.append(str(sn))
        step_files[sn] = f

    if not needed_steps:
        print(f"[EXTRACT] All {len(TARGET_STEPS)} slice files already exist in {SLICES_DIR}")
        return [step_files[sn] for sn in TARGET_STEPS]

    print(f"[EXTRACT] Need to export {len(needed_steps)} slices from {MODEL_PATH.name}...")
    t0 = time.time()

    temp_base = SLICES_DIR / ".tmp_batch_slice"
    snums_arg = ",".join(needed_steps)

    bridge_args = [
        "export-c04-slice",
        str(MODEL_PATH),
        str(temp_base),
        "dset3",
        snums_arg
    ]
    res = _run_bridge(bridge_args)
    print(f"[EXTRACT] Bridge finished in {time.time() - t0:.1f}s. Result status: {res.get('status')}")

    # Rename temp step files to canonical names
    for sn_str in needed_steps:
        src = SLICES_DIR / f"{temp_base.name}.step_{sn_str}"
        dst = SLICES_DIR / f"slice_step_{sn_str}.txt"
        if src.is_file():
            if dst.is_file():
                dst.unlink()
            src.rename(dst)
        else:
            raise FileNotFoundError(f"Expected export file missing: {src}")

    return [step_files[sn] for sn in TARGET_STEPS]


def load_slice(slice_path: Path) -> dict[str, np.ndarray]:
    """Parse COMSOL 2D slice export into structured fields."""
    data = np.loadtxt(slice_path, comments="%")
    # Coordinates (96 rows along y, 192 cols along x)
    x = data[:, 0].reshape((96, 192))
    y = data[:, 1].reshape((96, 192))
    u = data[:, 2].reshape((96, 192))    # Streamwise velocity
    # data[:, 3] is v (normal to slice in 3D, spanwise)
    w_cross = data[:, 4].reshape((96, 192)) # Crossflow velocity
    p = data[:, 5].reshape((96, 192))    # Pressure

    dx = x[0, 1] - x[0, 0]
    dy = y[1, 0] - y[0, 0]

    # Compute vorticity: omega_z = dw/dx - du/dy
    u_clean = np.nan_to_num(u, nan=0.0)
    w_clean = np.nan_to_num(w_cross, nan=0.0)
    dw_dx = np.gradient(w_clean, dx, axis=1)
    du_dy = np.gradient(u_clean, dy, axis=0)
    wz = dw_dx - du_dy

    # Mask solid cylinder
    r = np.sqrt(x**2 + y**2)
    solid = r <= CYLINDER_RADIUS
    wz[solid] = np.nan
    u[solid] = np.nan
    w_cross[solid] = np.nan
    p[solid] = np.nan

    return {
        "x": x,
        "y": y,
        "u": u,
        "v": w_cross,
        "wz": wz,
        "p": p,
        "extent": [x.min(), x.max(), y.min(), y.max()],
    }


def render_dashboard() -> tuple[Path, Path]:
    """Render animated multi-physics dashboard video and 4-phase comparison."""
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Export or get all slice files
    slice_paths = export_slices_if_needed()
    n_frames = len(slice_paths)
    print(f"[RENDER] Total sequence frames to render: {n_frames}")

    # 2. Load force coefficients time series
    force_data = np.genfromtxt(FORCE_CSV, delimiter=",", skip_header=1)
    t_all = force_data[:, 0]
    cl_all = force_data[:, 2]
    cd_all = force_data[:, 4]

    # Target frame timestamps
    frame_times = np.array([(sn - 1) * 0.06 for sn in TARGET_STEPS])
    frame_cl = np.interp(frame_times, t_all, cl_all)
    frame_cd = np.interp(frame_times, t_all, cd_all)

    # Global time bounds for plots
    t_min = frame_times[0]
    t_max = frame_times[-1]

    # Pre-load all slices
    print(f"[RENDER] Loading {n_frames} slice data files into memory...")
    slices_data = [load_slice(p) for p in slice_paths]
    extent = slices_data[0]["extent"]

    # Pre-select 4 representative phases within cycle 1 (0, 90, 180, 270 deg)
    # One cycle is ~26 steps (3.128s / 0.12s)
    cycle_len = 26
    phase_indices = [0, cycle_len // 4, cycle_len // 2, (3 * cycle_len) // 4]

    # -----------------------------------------------------------------
    # Generate 4-Phase Comparison Still Figure (Publication Quality)
    # -----------------------------------------------------------------
    print("[RENDER] Generating 4-phase vortex shedding comparison figure...")
    fig_phases, axes_ph = plt.subplots(4, 1, figsize=(12, 9), dpi=200, facecolor="#08080a")
    plt.subplots_adjust(hspace=0.28, left=0.08, right=0.90, top=0.92, bottom=0.08)

    phase_titles = [
        r"$\Phi = 0^\circ$ (Max Positive Lift $C_L \approx +0.063$ | Upper Shear Layer Shedding)",
        r"$\Phi = 90^\circ$ (Zero Lift Crossing $C_L \approx 0$ | Lower Vortex Roll-Up)",
        r"$\Phi = 180^\circ$ (Max Negative Lift $C_L \approx -0.018$ | Lower Shear Layer Shedding)",
        r"$\Phi = 270^\circ$ (Zero Lift Crossing $C_L \approx 0$ | Upper Vortex Roll-Up)",
    ]

    for p_idx, ax in zip(phase_indices, axes_ph):
        ax.set_facecolor("#08080a")
        data = slices_data[p_idx]
        im_ph = ax.imshow(
            data["wz"],
            extent=extent,
            origin="lower",
            cmap="RdBu_r",
            vmin=-15.0,
            vmax=15.0,
            interpolation="bilinear",
        )
        circ = Circle((0.0, 0.0), CYLINDER_RADIUS, facecolor="#1e2029", edgecolor="#ffffff", lw=1.5, zorder=10)
        ax.add_patch(circ)
        ax.set_xlim(-0.60, 3.00)
        ax.set_ylim(-0.90, 0.90)
        ax.set_aspect("equal")
        t_curr = frame_times[p_idx]
        t_star = t_curr * INFLOW_U0 / CYLINDER_DIAMETER
        title_str = f"{phase_titles[phase_indices.index(p_idx)]} — t = {t_curr:.2f} s (t* = {t_star:.1f})"
        ax.set_title(title_str, fontsize=10, fontweight="bold", color="#30d158", loc="left", pad=4)
        ax.set_ylabel("y [m]", fontsize=9, color="#a0a0b0")
        ax.tick_params(colors="#a0a0b0", labelsize=8)
        ax.grid(True, linestyle=":", alpha=0.2, color="#ffffff")

    axes_ph[-1].set_xlabel("x [m] (Streamwise Direction)", fontsize=10, color="#ffffff")

    # Add shared colorbar
    cbar_ax = fig_phases.add_axes([0.91, 0.15, 0.018, 0.70])
    cb = fig_phases.colorbar(im_ph, cax=cbar_ax)
    cb.set_label("Vorticity $\\omega_z = \\partial v/\\partial x - \\partial u/\\partial y$ [s$^{-1}$]", fontsize=9, color="#ffffff")
    cb.ax.tick_params(colors="#ffffff", labelsize=8)

    phase_png_path = OUT_DIR / "c04_shedding_4phases.png"
    fig_phases.savefig(phase_png_path, facecolor=fig_phases.get_facecolor(), edgecolor="none")
    plt.close(fig_phases)
    print(f"[RENDER] Saved: {phase_png_path}")

    # -----------------------------------------------------------------
    # Render Animated Dashboard Frames
    # -----------------------------------------------------------------
    print(f"[RENDER] Rendering {n_frames} dashboard frames...")
    plt.style.use("dark_background")

    # Layout: 15x8.5 inches @ 100 dpi = 1500 x 850 px
    fig = plt.figure(figsize=(15, 8.5), dpi=100)
    fig.patch.set_facecolor("#0a0b10")

    gs = gridspec.GridSpec(
        2, 2,
        height_ratios=[1.15, 0.95],
        width_ratios=[1.0, 1.0],
        left=0.06, right=0.94,
        top=0.90, bottom=0.08,
        hspace=0.32, wspace=0.22
    )

    # 1. Top-Left: Vorticity
    ax_wz = fig.add_subplot(gs[0, 0])
    ax_wz.set_facecolor("#0a0b10")
    im_wz = ax_wz.imshow(
        slices_data[0]["wz"],
        extent=extent,
        origin="lower",
        cmap="RdBu_r",
        vmin=-15.0,
        vmax=15.0,
        interpolation="bilinear"
    )
    circ_wz = Circle((0.0, 0.0), CYLINDER_RADIUS, facecolor="#1a1c24", edgecolor="#ffffff", lw=1.5, zorder=10)
    ax_wz.add_patch(circ_wz)
    ax_wz.set_xlim(-0.60, 3.00)
    ax_wz.set_ylim(-0.90, 0.90)
    ax_wz.set_aspect("equal")
    ax_wz.set_xlabel("x [m]", fontsize=9, color="#8e8e93")
    ax_wz.set_ylabel("y [m]", fontsize=9, color="#8e8e93")
    ax_wz.set_title("Vorticity Field $\\omega_z$ (von Kármán Vortex Street)", fontsize=11, fontweight="bold", color="#5ac8fa", loc="left", pad=4)
    ax_wz.tick_params(colors="#8e8e93", labelsize=8)
    cb_wz = plt.colorbar(im_wz, ax=ax_wz, orientation="vertical", pad=0.02, aspect=18)
    cb_wz.set_label("$\\omega_z$ [s$^{-1}$]", fontsize=9, color="#ffffff")
    cb_wz.ax.tick_params(colors="#ffffff", labelsize=8)

    # 2. Top-Right: Streamwise Velocity u
    ax_u = fig.add_subplot(gs[0, 1])
    ax_u.set_facecolor("#0a0b10")
    im_u = ax_u.imshow(
        slices_data[0]["u"],
        extent=extent,
        origin="lower",
        cmap="viridis",
        vmin=-0.15,
        vmax=0.65,
        interpolation="bilinear"
    )
    circ_u = Circle((0.0, 0.0), CYLINDER_RADIUS, facecolor="#1a1c24", edgecolor="#ffffff", lw=1.5, zorder=10)
    ax_u.add_patch(circ_u)
    ax_u.set_xlim(-0.60, 3.00)
    ax_u.set_ylim(-0.90, 0.90)
    ax_u.set_aspect("equal")
    ax_u.set_xlabel("x [m]", fontsize=9, color="#8e8e93")
    ax_u.set_ylabel("y [m]", fontsize=9, color="#8e8e93")
    ax_u.set_title("Streamwise Velocity $u$ (Wake Deficit & Separation)", fontsize=11, fontweight="bold", color="#30d158", loc="left", pad=4)
    ax_u.tick_params(colors="#8e8e93", labelsize=8)
    cb_u = plt.colorbar(im_u, ax=ax_u, orientation="vertical", pad=0.02, aspect=18)
    cb_u.set_label("$u$ [m/s]", fontsize=9, color="#ffffff")
    cb_u.ax.tick_params(colors="#ffffff", labelsize=8)

    # 3. Bottom-Left: Hydrodynamic Force Curves (CL & CD)
    ax_cl = fig.add_subplot(gs[1, 0])
    ax_cl.set_facecolor("#0a0b10")
    # Base full trajectory (semi-transparent)
    ax_cl.plot(frame_times, frame_cl, color="#5ac8fa", lw=1.2, alpha=0.35, label="Lift $C_L$ (Envelope)")
    # Active history trace
    line_cl, = ax_cl.plot([], [], color="#5ac8fa", lw=2.2, label="Lift $C_L$")
    ax_cl.set_xlim(t_min, t_max)
    ax_cl.set_ylim(-0.03, 0.08)
    ax_cl.set_xlabel("Physical Time t [s]", fontsize=9, color="#ffffff")
    ax_cl.set_ylabel("Lift Coefficient $C_L$", color="#5ac8fa", fontsize=9, fontweight="bold")
    ax_cl.tick_params(axis="y", labelcolor="#5ac8fa", labelsize=8)
    ax_cl.tick_params(axis="x", labelcolor="#8e8e93", labelsize=8)
    ax_cl.grid(True, linestyle=":", alpha=0.25, color="#ffffff")
    ax_cl.set_title("Synchronized Hydrodynamic Loads ($C_L$ & $C_D$)", fontsize=11, fontweight="bold", color="#ff9f0a", loc="left", pad=4)

    # Twin axis for CD
    ax_cd = ax_cl.twinx()
    ax_cd.plot(frame_times, frame_cd, color="#ff9f0a", lw=1.2, alpha=0.35, linestyle="--")
    line_cd, = ax_cd.plot([], [], color="#ff9f0a", lw=2.2, linestyle="--", label="Drag $C_D$")
    ax_cd.set_ylim(0.380, 0.400)
    ax_cd.set_ylabel("Drag Coefficient $C_D$", color="#ff9f0a", fontsize=9, fontweight="bold")
    ax_cd.tick_params(axis="y", labelcolor="#ff9f0a", labelsize=8)

    # Vertical cursor line
    cursor_line = ax_cl.axvline(x=t_min, color="#ff3b30", lw=1.8, linestyle="-", zorder=15)
    dot_cl, = ax_cl.plot([t_min], [frame_cl[0]], "o", color="#5ac8fa", markersize=7, zorder=20)
    dot_cd, = ax_cd.plot([t_min], [frame_cd[0]], "s", color="#ff9f0a", markersize=7, zorder=20)

    # 4. Bottom-Right: Limit-Cycle Phase Portrait (CD vs CL)
    ax_ph = fig.add_subplot(gs[1, 1])
    ax_ph.set_facecolor("#0a0b10")
    # Full orbit in background
    ax_ph.plot(frame_cd, frame_cl, color="#af52de", lw=1.2, alpha=0.35, label="Limit Cycle")
    line_ph, = ax_ph.plot([], [], color="#af52de", lw=2.2)
    dot_ph, = ax_ph.plot([frame_cd[0]], [frame_cl[0]], "o", color="#ff3b30", markersize=9, markeredgecolor="#ffffff", markeredgewidth=1.5, zorder=25)
    ax_ph.set_xlim(0.385, 0.398)
    ax_ph.set_ylim(-0.03, 0.08)
    ax_ph.set_xlabel("Drag Coefficient $C_D$", fontsize=9, color="#ffffff")
    ax_ph.set_ylabel("Lift Coefficient $C_L$", fontsize=9, color="#ffffff")
    ax_ph.tick_params(colors="#8e8e93", labelsize=8)
    ax_ph.grid(True, linestyle=":", alpha=0.25, color="#ffffff")
    ax_ph.set_title("Vortex-Induced Hydrodynamic Limit Cycle ($C_D$ vs $C_L$)", fontsize=11, fontweight="bold", color="#af52de", loc="left", pad=4)

    # HUD Super-Title Banner
    hud_title = fig.text(
        0.06, 0.955,
        "C04 SEAWATER CYLINDER FLOW BENCHMARK | Re = 126,167 | MESH1 FAST-SOLVER (76.9万单元)",
        fontsize=13, fontweight="bold", color="#ffffff"
    )
    hud_telemetry = fig.text(
        0.06, 0.925,
        "",
        fontsize=10, color="#30d158", fontfamily="monospace"
    )

    rendered_images = []
    t_start_render = time.time()

    for idx in range(n_frames):
        t_curr = frame_times[idx]
        t_star = t_curr * INFLOW_U0 / CYLINDER_DIAMETER
        cl_curr = frame_cl[idx]
        cd_curr = frame_cd[idx]

        # 1. Update colormaps
        im_wz.set_data(slices_data[idx]["wz"])
        im_u.set_data(slices_data[idx]["u"])

        # 2. Update force history
        sub_t = frame_times[:idx + 1]
        line_cl.set_data(sub_t, frame_cl[:idx + 1])
        line_cd.set_data(sub_t, frame_cd[:idx + 1])
        cursor_line.set_xdata([t_curr, t_curr])
        dot_cl.set_data([t_curr], [cl_curr])
        dot_cd.set_data([t_curr], [cd_curr])

        # 3. Update phase orbit
        line_ph.set_data(frame_cd[:idx + 1], frame_cl[:idx + 1])
        dot_ph.set_data([cd_curr], [cl_curr])

        # 4. Update HUD telemetry
        hud_telemetry.set_text(
            f"FRAME {idx+1:02d}/{n_frames:02d} | TIME: t = {t_curr:05.2f} s (t* = {t_star:05.1f}) | "
            f"CL = {cl_curr:+.4f} | CD = {cd_curr:.4f} | St = 0.2070 | fs = 0.320 Hz | rho = 1025.9 kg/m^3"
        )

        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        # Make a copy to avoid Agg reuse buffer corruption
        frame_rgb = rgba[:, :, :3].copy()

        # Save individual PNG frame
        frame_png = FRAMES_DIR / f"frame_{idx:03d}.png"
        img = Image.fromarray(frame_rgb)
        img.save(frame_png)
        rendered_images.append(img)

        if (idx + 1) % 15 == 0 or (idx + 1) == n_frames:
            elapsed = time.time() - t_start_render
            print(f"  [RENDER] Frame {idx+1}/{n_frames} ({elapsed:.1f}s, {elapsed/(idx+1):.2f}s/frame)")

    plt.close(fig)

    # -----------------------------------------------------------------
    # Save High-Resolution Animated GIF
    # -----------------------------------------------------------------
    gif_path = OUT_DIR / "c04_vortex_shedding_dashboard.gif"
    print(f"[RENDER] Compiling animated GIF to {gif_path} (10 fps, 100ms/frame)...")
    # Resize slightly for optimized animated GIF file size (1200 x 680)
    gif_frames = [f.resize((1200, 680), Image.Resampling.BILINEAR) for f in rendered_images]
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=100,  # 10 fps
        loop=0,
        optimize=True
    )
    print(f"[RENDER] Saved GIF: {gif_path} ({gif_path.stat().st_size / 1024 / 1024:.2f} MB)")

    # -----------------------------------------------------------------
    # Save Publication-Grade H.264 MP4 Video
    # -----------------------------------------------------------------
    mp4_path = OUT_DIR / "c04_vortex_shedding_dashboard.mp4"
    try:
        import subprocess

        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        print(f"[RENDER] Encoding H.264 MP4 to {mp4_path} using ffmpeg...")
        cmd = [
            ffmpeg_exe, "-y",
            "-framerate", "10",
            "-i", str(FRAMES_DIR / "frame_%03d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            str(mp4_path)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and mp4_path.is_file():
            print(f"[RENDER] Saved MP4: {mp4_path} ({mp4_path.stat().st_size / 1024:.1f} KB)")
        else:
            print(f"[RENDER] Warning: MP4 encoding returned code {proc.returncode}: {proc.stderr}")
    except Exception as e:
        print(f"[RENDER] Warning: Could not encode MP4 directly: {e}")

    return gif_path, mp4_path, phase_png_path


if __name__ == "__main__":
    t_start = time.time()
    gif, mp4, png = render_dashboard()
    print(f"[DONE] Completed video pipeline in {time.time() - t_start:.1f}s")
    print(f"[OUTPUT 1 - GIF] {gif}")
    print(f"[OUTPUT 2 - MP4] {mp4}")
    print(f"[OUTPUT 3 - PNG] {png}")
