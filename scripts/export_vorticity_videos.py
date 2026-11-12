#!/usr/bin/env python3
"""
export_vorticity_videos.py - Generates publication-grade MP4 and animated GIF videos
of the Re=100 cylinder wake vorticity field from the verified M3 151-frame sequence.

Outputs:
1. reports/vorticity_dynamics_dashboard.mp4 & .gif:
   Multi-physics dashboard showing:
   - Full domain overview (40D x 24D)
   - Near-wake vortex shedding zoom
   - Synchronized real-time lift (C_L) and drag (C_D) phase trajectory tracking
2. reports/vorticity_wake_zoom.mp4 & .gif:
   Clean 16:9 widescreen near-wake vortex shedding animation with HUD telemetry.
"""

import shutil
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import matplotlib

matplotlib.use("Agg")
import imageio.v3 as iio
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from hydro_disturb_bench.field_sequence import load_full_field_sequence


def render_dashboard_video(seq_dir: Path, output_dir: Path, fps: int = 15) -> tuple[Path, Path]:
    """Render 3-in-1 multi-physics dashboard video."""
    print("Loading sequence data for dashboard video...")
    seq = load_full_field_sequence(seq_dir)
    values = seq["values"]  # (151, 3, 97, 161)
    x = seq["x"]
    y = seq["y"]
    times = seq["times"]
    n_frames = len(times)

    dx = x[1] - x[0]
    dy = y[1] - y[0]

    # Pre-compute vorticity with no-slip boundary at cylinder
    print(f"Pre-computing vorticity field across {n_frames} frames...")
    u_all = np.nan_to_num(values[:, 0, :, :], nan=0.0)
    v_all = np.nan_to_num(values[:, 1, :, :], nan=0.0)
    dv_dx = np.gradient(v_all, dx, axis=2)
    du_dy = np.gradient(u_all, dy, axis=1)
    omega_all = dv_dx - du_dy

    # Load lift and drag data
    lift_file = REPO_ROOT / "runs" / "re100_s1b2_m3_20261105_verified" / "lift_coeff.txt"
    drag_file = REPO_ROOT / "runs" / "re100_s1b2_m3_20261105_verified" / "drag_coeff.txt"
    lift_data = np.loadtxt(lift_file, comments="%")
    drag_data = np.loadtxt(drag_file, comments="%")

    mask_time = (lift_data[:, 0] >= times[0] - 1e-6) & (lift_data[:, 0] <= times[-1] + 1e-6)
    t_curve = lift_data[mask_time, 0]
    cl_curve = lift_data[mask_time, 1]
    cd_curve = drag_data[mask_time, 1]

    # Set up dark professional canvas (1680 x 960, both divisible by 16)
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(14, 8), dpi=120)
    fig.patch.set_facecolor("#08080a")
    gs = gridspec.GridSpec(
        2, 2, height_ratios=[1.1, 1.0], width_ratios=[1.3, 1.0], hspace=0.34, wspace=0.25
    )

    # 1. Upper panel: Full domain
    ax_full = fig.add_subplot(gs[0, :])
    ax_full.set_facecolor("#08080a")
    ax_full.set_aspect("equal")
    im_full = ax_full.imshow(
        omega_all[0],
        extent=[x[0], x[-1], y[0], y[-1]],
        origin="lower",
        cmap="RdBu_r",
        vmin=-2.0,
        vmax=2.0,
        interpolation="bicubic",
    )
    ax_full.set_xlim(0, 0.40)
    ax_full.set_ylim(0, 0.24)
    ax_full.set_xlabel("x [m]", fontsize=10, color="#a0a0b0")
    ax_full.set_ylabel("y [m]", fontsize=10, color="#a0a0b0")

    circle_full = plt.Circle(
        (0.10, 0.12), 0.005, facecolor="#ffffff", edgecolor="#202025", linewidth=1.5, zorder=20
    )
    ax_full.add_patch(circle_full)

    cbar = plt.colorbar(im_full, ax=ax_full, orientation="vertical", pad=0.015, aspect=15)
    cbar.set_label("Vorticity $\\omega_z$ [s$^{-1}$]", fontsize=10, color="#ffffff")
    cbar.ax.tick_params(labelsize=9, colors="#ffffff")

    # 2. Lower-left panel: Near-wake zoom
    ax_zoom = fig.add_subplot(gs[1, 0])
    ax_zoom.set_facecolor("#08080a")
    ax_zoom.set_aspect("equal")
    im_zoom = ax_zoom.imshow(
        omega_all[0],
        extent=[x[0], x[-1], y[0], y[-1]],
        origin="lower",
        cmap="RdBu_r",
        vmin=-2.0,
        vmax=2.0,
        interpolation="bicubic",
    )
    ax_zoom.set_xlim(0.08, 0.30)
    ax_zoom.set_ylim(0.07, 0.17)
    ax_zoom.set_xlabel("x [m]", fontsize=10, color="#a0a0b0")
    ax_zoom.set_ylabel("y [m]", fontsize=10, color="#a0a0b0")
    ax_zoom.set_title(
        "Near-Wake Vortex Shedding Zoom (x $\\in$ [0.08, 0.30] m)",
        fontsize=11,
        fontweight="bold",
        color="#30d158",
    )

    circle_zoom = plt.Circle(
        (0.10, 0.12), 0.005, facecolor="#ffffff", edgecolor="#202025", linewidth=1.8, zorder=20
    )
    ax_zoom.add_patch(circle_zoom)

    # 3. Lower-right panel: Lift / Drag phase curve
    ax_coeff = fig.add_subplot(gs[1, 1])
    ax_coeff.set_facecolor("#08080a")
    ax_coeff.plot(t_curve, cl_curve, color="#5ac8fa", lw=1.8, label="Lift $C_L$")
    ax_coeff.set_xlabel("Simulation Time t [s]", fontsize=10, color="#ffffff")
    ax_coeff.set_ylabel("Lift Coefficient $C_L$", color="#5ac8fa", fontsize=10)
    ax_coeff.tick_params(axis="y", labelcolor="#5ac8fa")
    ax_coeff.set_xlim(times[0], times[-1])
    ax_coeff.set_ylim(-0.45, 0.45)
    ax_coeff.grid(True, linestyle="--", alpha=0.25)

    ax_drag = ax_coeff.twinx()
    ax_drag.plot(t_curve, cd_curve, color="#ff9f0a", lw=1.8, linestyle="-", label="Drag $C_D$")
    ax_drag.set_ylabel("Drag Coefficient $C_D$", color="#ff9f0a", fontsize=10)
    ax_drag.tick_params(axis="y", labelcolor="#ff9f0a")
    ax_drag.set_ylim(1.34, 1.39)

    vline = ax_coeff.axvline(times[0], color="#ff375f", linestyle=":", lw=1.8)
    scatter_cl = ax_coeff.scatter(
        [times[0]], [cl_curve[0]], color="#5ac8fa", s=65, zorder=10, edgecolor="white"
    )
    scatter_cd = ax_drag.scatter(
        [times[0]], [cd_curve[0]], color="#ff9f0a", s=65, zorder=10, edgecolor="white"
    )

    fig.suptitle(
        "Re=100 Cylinder Wake - Vorticity Dynamics (M3 Grid: 34,752 elements)",
        fontsize=13.5,
        fontweight="bold",
        y=0.98,
    )

    # Output paths
    mp4_path = output_dir / "vorticity_dynamics_dashboard.mp4"
    gif_path = output_dir / "vorticity_dynamics_dashboard.gif"

    frames_rgb = []
    print(f"Rendering {n_frames} frames for dashboard video...")
    t_start = time.time()

    for i in range(n_frames):
        t_now = times[i]
        omega_i = omega_all[i]

        im_full.set_data(omega_i)
        im_zoom.set_data(omega_i)

        ax_full.set_title(
            f"Full Domain Overview (40D x 24D) | Vorticity $\\omega_z$ at t = {t_now:.1f} s",
            fontsize=11.5,
            fontweight="bold",
            color="#5ac8fa",
        )

        # Update phase tracking
        cl_now = np.interp(t_now, t_curve, cl_curve)
        cd_now = np.interp(t_now, t_curve, cd_curve)

        vline.set_xdata([t_now, t_now])
        scatter_cl.set_offsets([[t_now, cl_now]])
        scatter_cd.set_offsets([[t_now, cd_now]])

        ax_coeff.set_title(
            f"Phase State: $C_L$ = {cl_now:+.3f}, $C_D$ = {cd_now:.3f}",
            fontsize=11,
            fontweight="bold",
            color="#ffffff",
        )

        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        # CRITICAL: .copy() prevents memoryview aliasing from Agg buffer reuse!
        frames_rgb.append(rgba[:, :, :3].copy())

        if (i + 1) % 30 == 0 or (i + 1) == n_frames:
            elapsed = time.time() - t_start
            print(f"  Frame {i + 1}/{n_frames} rendered ({elapsed:.1f}s)")

    plt.close(fig)

    # Verify frame motion
    diff_mid = np.max(np.abs(frames_rgb[n_frames // 2].astype(int) - frames_rgb[0].astype(int)))
    print(
        f"Frame motion verification: max diff between frame 0 and {n_frames // 2} is {diff_mid} (must be > 100)"
    )
    assert diff_mid > 100, "FATAL: Frame buffer aliased! Frames must animate dynamically."

    # Encode MP4
    print(f"Encoding MP4: {mp4_path.name} (fps={fps})...")
    iio.imwrite(str(mp4_path), frames_rgb, fps=fps, codec="libx264")

    # Encode animated GIF using Pillow for guaranteed multi-frame looping
    print(f"Encoding animated GIF: {gif_path.name}...")
    sub_frames = [
        Image.fromarray(f).resize((840, 480), Image.Resampling.BILINEAR) for f in frames_rgb[::2]
    ]
    sub_frames[0].save(
        str(gif_path),
        save_all=True,
        append_images=sub_frames[1:],
        duration=133,  # ~7.5 fps
        loop=0,
        optimize=True,
    )

    print(f"Dashboard video exported successfully in {time.time() - t_start:.1f}s.")
    return mp4_path, gif_path


def render_wake_zoom_video(seq_dir: Path, output_dir: Path, fps: int = 15) -> tuple[Path, Path]:
    """Render high-definition 16:9 near-wake zoom video."""
    print("\nLoading sequence data for wake zoom video...")
    seq = load_full_field_sequence(seq_dir)
    values = seq["values"]
    x = seq["x"]
    y = seq["y"]
    times = seq["times"]
    n_frames = len(times)

    dx = x[1] - x[0]
    dy = y[1] - y[0]

    u_all = np.nan_to_num(values[:, 0, :, :], nan=0.0)
    v_all = np.nan_to_num(values[:, 1, :, :], nan=0.0)
    omega_all = np.gradient(v_all, dx, axis=2) - np.gradient(u_all, dy, axis=1)

    # 16:9 widescreen canvas: 1680 x 944 or 1920 x 1080 (both 1920 and 1088 divisible by 16)
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    fig.patch.set_facecolor("#08080a")
    ax.set_facecolor("#08080a")

    ax.set_aspect("equal")
    im = ax.imshow(
        omega_all[0],
        extent=[x[0], x[-1], y[0], y[-1]],
        origin="lower",
        cmap="RdBu_r",
        vmin=-2.0,
        vmax=2.0,
        interpolation="bicubic",
    )
    ax.set_xlim(0.08, 0.32)
    ax.set_ylim(0.065, 0.175)
    ax.set_xlabel("x [m]", fontsize=13, color="#a0a0b0", labelpad=8)
    ax.set_ylabel("y [m]", fontsize=13, color="#a0a0b0", labelpad=8)
    ax.tick_params(axis="both", labelsize=11, colors="#a0a0b0")

    circle = plt.Circle(
        (0.10, 0.12), 0.005, facecolor="#ffffff", edgecolor="#202025", linewidth=2.0, zorder=20
    )
    ax.add_patch(circle)

    cbar = plt.colorbar(im, ax=ax, orientation="vertical", pad=0.02, aspect=20)
    cbar.set_label("Vorticity $\\omega_z$ [s$^{-1}$]", fontsize=13, color="#ffffff", labelpad=10)
    cbar.ax.tick_params(labelsize=11, colors="#ffffff")

    hud_text = ax.text(
        0.083,
        0.168,
        "",
        fontsize=12,
        color="#ffffff",
        family="monospace",
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.5", facecolor="#14141a", edgecolor="#5ac8fa", alpha=0.9, lw=1.2
        ),
        zorder=30,
    )

    ax.set_title(
        "Re=100 Karman Vortex Street - Near-Wake Vorticity Dynamics",
        fontsize=16,
        fontweight="bold",
        pad=12,
        color="#ffffff",
    )

    mp4_path = output_dir / "vorticity_wake_zoom.mp4"
    gif_path = output_dir / "vorticity_wake_zoom.gif"

    frames_rgb = []
    print(f"Rendering {n_frames} frames for wake zoom video...")
    t_start = time.time()

    for i in range(n_frames):
        t_now = times[i]
        omega_i = omega_all[i]

        im.set_data(omega_i)
        hud_text.set_text(f"TIME: {t_now:05.1f} s | Re: 100 | M3 MESH (34,752 elem)")

        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        # CRITICAL: .copy() prevents memoryview aliasing from Agg buffer reuse!
        frames_rgb.append(rgba[:, :, :3].copy())

        if (i + 1) % 30 == 0 or (i + 1) == n_frames:
            elapsed = time.time() - t_start
            print(f"  Frame {i + 1}/{n_frames} rendered ({elapsed:.1f}s)")

    plt.close(fig)

    diff_mid = np.max(np.abs(frames_rgb[n_frames // 2].astype(int) - frames_rgb[0].astype(int)))
    print(
        f"Frame motion verification: max diff between frame 0 and {n_frames // 2} is {diff_mid} (must be > 100)"
    )
    assert diff_mid > 100, "FATAL: Frame buffer aliased! Frames must animate dynamically."

    print(f"Encoding MP4: {mp4_path.name} (fps={fps})...")
    iio.imwrite(str(mp4_path), frames_rgb, fps=fps, codec="libx264")

    print(f"Encoding animated GIF: {gif_path.name}...")
    sub_frames = [
        Image.fromarray(f).resize((960, 540), Image.Resampling.BILINEAR) for f in frames_rgb[::2]
    ]
    sub_frames[0].save(
        str(gif_path),
        save_all=True,
        append_images=sub_frames[1:],
        duration=133,  # ~7.5 fps
        loop=0,
        optimize=True,
    )

    print(f"Wake zoom video exported successfully in {time.time() - t_start:.1f}s.")
    return mp4_path, gif_path


def main():
    seq_dir = REPO_ROOT / "runs" / "re100_m3_field_sequence_20261105_verified"
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)

    if not seq_dir.exists():
        print(f"Error: Sequence directory not found: {seq_dir.name}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting vorticity video export to: {reports_dir.name}")
    t0 = time.time()

    # 1. Multi-physics dashboard video
    dash_mp4, dash_gif = render_dashboard_video(seq_dir, reports_dir, fps=15)

    # 2. 16:9 Widescreen pure wake zoom video
    zoom_mp4, zoom_gif = render_wake_zoom_video(seq_dir, reports_dir, fps=15)

    # Copy to artifacts directory
    art_dir = Path(
        r"C:\Users\Administrator\.gemini\antigravity-ide\brain\78e0afb2-6233-4e38-b94c-b25dc49b4926"
    )
    if art_dir.exists():
        for f in [dash_mp4, dash_gif, zoom_mp4, zoom_gif]:
            shutil.copy2(f, art_dir / f.name)

    total_time = time.time() - t0
    print(f"\nALL VIDEOS EXPORTED SUCCESSFULLY in {total_time:.1f}s:")
    print(f"  - Dashboard MP4: {dash_mp4.name} ({dash_mp4.stat().st_size / 1e6:.2f} MB)")
    print(f"  - Dashboard GIF: {dash_gif.name} ({dash_gif.stat().st_size / 1e6:.2f} MB)")
    print(f"  - Wake Zoom MP4: {zoom_mp4.name} ({zoom_mp4.stat().st_size / 1e6:.2f} MB)")
    print(f"  - Wake Zoom GIF: {zoom_gif.name} ({zoom_gif.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
