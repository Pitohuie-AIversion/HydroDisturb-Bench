#!/usr/bin/env python3
"""
render_c04_3d_full_lifecycle.py - 3D Vortex Flow Full Lifecycle (t=0 to 60s) Video Pipeline.

Renders the complete 3D circular cylinder wake evolution from initial state (t=0s):
- Initial flow acceleration & boundary layer separation (t = 0 - 3s)
- Symmetric recirculation bubble elongation (t = 3 - 6s)
- Symmetry breaking & shear layer instability (t = 6 - 10s)
- First von Karman vortex shedding & convective rollout (t = 10 - 20s)
- Transition to fully developed quasi-steady 3D vortex street (t = 20 - 60s)
- Fixed isometric perspective (elev=22 deg, azim=-55 deg) with synchronized full 0-60s force HUD.

Outputs:
1. c04_3d_vortex_full_lifecycle.mp4: Full 60s lifecycle video (H.264, 10 fps).
2. c04_3d_vortex_full_lifecycle.gif: Smooth looping animated GIF.
3. c04_3d_lifecycle_6phases.png: 6-milestone comparison gallery across full lifecycle.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image
from scipy.ndimage import map_coordinates
from skimage.measure import marching_cubes

REPO_ROOT = Path(r"D:\HydroDisturb-Bench")
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from hydro_disturb_bench.comsol_java.bridge import _run_bridge

OUT_DIR = Path(r"E:\cfd_3d\05_reports_viz\video_c04_shedding")
FRAMES_DIR = OUT_DIR / "lifecycle_frames_3d"
MODEL_PATH = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3\B_rtol1e3.mph")
FORCE_CSV = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3\force_coefficients.csv")

CYLINDER_DIAMETER = 0.30  # D = 0.30 m
CYLINDER_RADIUS = 0.15    # R = 0.15 m
INFLOW_U0 = 0.50          # U0 = 0.50 m/s
RE_NUMBER = 126167
RHO_SEAWATER = 1025.8944
NU_SEAWATER = 1.1889e-6
Q_ISO_LEVEL = 0.85        # s^-2

# Comprehensive 62-frame schedule covering full 0 to 60s lifecycle:
# 1. Initial startup (t = 0.0 to 5.04s, dt = 0.24s): 22 frames
STEPS_INITIAL = list(range(1, 89, 4))
# 2. Onset & first shedding (t = 6.0 to 20.0s, dt ~ 1.0s): 15 frames
STEPS_ONSET = [101, 118, 135, 151, 168, 185, 201, 218, 235, 251, 268, 285, 301, 318, 335]
# 3. Transition to periodic (t = 22.0 to 48.0s, dt ~ 2.0s): 14 frames
STEPS_TRANSITION = [368, 401, 435, 468, 501, 535, 568, 601, 635, 668, 701, 735, 768, 801]
# 4. Developed shedding to end of simulation (t = 50.0 to 60.0s): 11 frames
STEPS_DEVELOPED = [835, 843, 851, 859, 867, 875, 883, 901, 935, 968, 1001]

FULL_LIFECYCLE_STEPS = STEPS_INITIAL + STEPS_ONSET + STEPS_TRANSITION + STEPS_DEVELOPED


def export_batch_steps(steps: list[int], model_path: Path, out_dir: Path):
    """Export missing steps in a single COMSOL batch invocation."""
    missing = [s for s in steps if not (out_dir / f"c04_vol_step_{s}.npz").is_file()]
    if not missing:
        print(f"[BATCH] All {len(steps)} NPZ volume files already exist.")
        return

    print(f"[BATCH] {len(missing)} steps missing from total {len(steps)} steps.")
    print(f"[BATCH] Missing list: {missing}")
    temp_base = out_dir / ".tmp_vol_lifecycle.txt"
    snums_str = ",".join(str(s) for s in missing)

    t0 = time.time()
    print(f"[BATCH] Invoking COMSOL bridge for {len(missing)} steps in one session...")
    args = [
        "export-c04-volume",
        str(model_path),
        str(temp_base),
        "dset3",
        snums_str
    ]
    res = _run_bridge(args)
    print(f"[BATCH] COMSOL bridge finished in {time.time()-t0:.1f}s: {res.get('status')}")

    # Process each exported text file
    for s in missing:
        step_txt = out_dir / f"{temp_base.name}.step_{s}"
        if not step_txt.is_file():
            print(f"[BATCH] Warning: Expected file {step_txt.name} not found!")
            continue

        t_val = (s - 1) * 0.06
        data = np.loadtxt(step_txt, comments="%", dtype=np.float32)
        x = data[:, 0].reshape((96, 64, 192))[0, 0, :]
        y_span = data[:, 1].reshape((96, 64, 192))[0, :, 0]
        z_cross = data[:, 2].reshape((96, 64, 192))[:, 0, 0]
        u = data[:, 3].reshape((96, 64, 192))
        v_span = data[:, 4].reshape((96, 64, 192))
        w_cross = data[:, 5].reshape((96, 64, 192))
        p = data[:, 6].reshape((96, 64, 192))

        npz_path = out_dir / f"c04_vol_step_{s}.npz"
        np.savez_compressed(
            npz_path,
            x=x, y_span=y_span, z_cross=z_cross,
            u=u, v_span=v_span, w_cross=w_cross, p=p,
            time_s=t_val, step=s
        )
        step_txt.unlink(missing_ok=True)
        print(f"[BATCH] Saved {npz_path.name} ({npz_path.stat().st_size/1024/1024:.2f} MB), removed raw text.")


def load_and_extract_q(npz_file: Path, q_level: float = Q_ISO_LEVEL) -> dict:
    """Load 3D volume, compute gradients, Q-criterion and marching cubes isosurface."""
    ds = np.load(npz_file)
    x = ds["x"]              # (192,)
    y_span = ds["y_span"]    # (64,)
    z_cross = ds["z_cross"]  # (96,)
    t_val = float(ds["time_s"])
    step_num = int(ds["step"])

    u = np.nan_to_num(ds["u"], nan=0.0)          # (96, 64, 192) -> (zc, ys, x)
    v_s = np.nan_to_num(ds["v_span"], nan=0.0)
    w_c = np.nan_to_num(ds["w_cross"], nan=0.0)

    dx = float(x[1] - x[0])
    dys = float(y_span[1] - y_span[0])
    dzc = float(z_cross[1] - z_cross[0])

    # Solid cylinder mask
    X, ZC = np.meshgrid(x, z_cross)
    R_mask = np.sqrt(X**2 + ZC**2) <= (CYLINDER_RADIUS * 1.03)

    # Velocity gradients
    du_dx = np.gradient(u, dx, axis=2)
    du_dys = np.gradient(u, dys, axis=1)
    du_dzc = np.gradient(u, dzc, axis=0)

    dw_dx = np.gradient(w_c, dx, axis=2)
    dw_dys = np.gradient(w_c, dys, axis=1)
    dw_dzc = np.gradient(w_c, dzc, axis=0)

    dv_dx = np.gradient(v_s, dx, axis=2)
    dv_dys = np.gradient(v_s, dys, axis=1)
    dv_dzc = np.gradient(v_s, dzc, axis=0)

    # Incompressible Q-criterion
    Q = -0.5 * (
        du_dx**2 + dw_dzc**2 + dv_dys**2
        + 2.0 * du_dzc * dw_dx
        + 2.0 * du_dys * dv_dx
        + 2.0 * dw_dys * dv_dzc
    )

    # Spanwise vorticity: omega_span = dw/dx - du/dzc
    omega_span = dw_dx - du_dzc

    # Mask solid cylinder interior
    for j in range(len(y_span)):
        Q[:, j, :][R_mask] = -100.0
        omega_span[:, j, :][R_mask] = 0.0

    # Handle very early frames where Q may be below threshold
    if np.max(Q) < q_level:
        effective_q = max(0.10, float(np.percentile(Q[Q > 0], 95))) if np.any(Q > 0) else q_level
    else:
        effective_q = q_level

    try:
        verts, faces, normals, values = marching_cubes(Q, level=effective_q, spacing=(dzc, dys, dx))
        zc_v = z_cross[0] + verts[:, 0]
        ys_v = y_span[0] + verts[:, 1]
        x_v = x[0] + verts[:, 2]
        ys_centered = ys_v - np.mean(y_span)

        zc_idx = np.clip(verts[:, 0] / dzc, 0, len(z_cross) - 1)
        ys_idx = np.clip(verts[:, 1] / dys, 0, len(y_span) - 1)
        x_idx = np.clip(verts[:, 2] / dx, 0, len(x) - 1)
        omega_v = map_coordinates(omega_span, [zc_idx, ys_idx, x_idx], order=1, mode="nearest")
    except Exception:
        # Fallback for quiescent initial frame
        x_v = np.array([])
        ys_centered = np.array([])
        zc_v = np.array([])
        faces = np.zeros((0, 3), dtype=int)
        omega_v = np.array([])

    return {
        "x": x_v,
        "y": ys_centered,
        "z": zc_v,
        "faces": faces,
        "omega_span": omega_v,
        "time_s": t_val,
        "step": step_num
    }


def load_force_series(csv_path: Path) -> dict:
    """Load hydrodynamic force history for telemetry HUD."""
    times, cls, cds = [], [], []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            times.append(float(row["time_s"]))
            cls.append(float(row["CL"]))
            cds.append(float(row["CD"]))
    return {
        "time": np.array(times),
        "cl": np.array(cls),
        "cd": np.array(cds)
    }


def render_all_lifecycle_frames(steps: list[int], out_dir: Path, force_data: dict) -> list[Path]:
    """Render 3D time-evolving frames from fixed orthographic/isometric perspective across full 60s."""
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    frame_paths = []

    FIXED_ELEV = 22
    FIXED_AZIM = -55

    norm = plt.Normalize(vmin=-12.0, vmax=12.0)
    cmap = plt.get_cmap("RdBu_r")

    t_all = force_data["time"]
    cl_all = force_data["cl"]
    cd_all = force_data["cd"]

    total_frames = len(steps)
    print(f"[RENDER] Rendering {total_frames} frames across full 60.0s lifecycle...")

    for idx, sn in enumerate(steps):
        npz_file = out_dir / f"c04_vol_step_{sn}.npz"
        if not npz_file.is_file():
            print(f"[RENDER] Skipping missing step {sn}")
            continue

        t_frame_start = time.time()
        iso = load_and_extract_q(npz_file, q_level=Q_ISO_LEVEL)
        t_cur = iso["time_s"]
        t_star = t_cur * INFLOW_U0 / CYLINDER_DIAMETER
        cl_cur = float(np.interp(t_cur, t_all, cl_all))
        cd_cur = float(np.interp(t_cur, t_all, cd_all))

        # Determine physical regime label
        if t_cur < 1.0:
            regime = "Initial Flow Startup & Boundary Layer Attachment"
        elif t_cur < 5.0:
            regime = "Symmetric Separation & Closed Recirculation Bubble Growth"
        elif t_cur < 10.0:
            regime = "Bubble Elongation & Symmetry Breaking (Instability Onset)"
        elif t_cur < 20.0:
            regime = "First Karman Vortex Shedding & Primary Wake Convection"
        elif t_cur < 45.0:
            regime = "Transition to Periodic Shedding & Wake Expansion"
        else:
            regime = "Fully Developed Quasi-Steady 3D Turbulent Vortex Street"

        # Setup figure: 1500 x 900, dark academic theme
        fig = plt.figure(figsize=(15, 9), dpi=100, facecolor="#08080c")
        gs = gridspec.GridSpec(
            2, 1, height_ratios=[3.8, 1.2],
            left=0.03, right=0.97, top=0.92, bottom=0.06, hspace=0.10
        )

        # ----------------------------------------------------
        # Panel 1: 3D Fixed Isometric Vortex Shedding View
        # ----------------------------------------------------
        ax3d = fig.add_subplot(gs[0], projection="3d")
        ax3d.set_facecolor("#08080c")

        x_v = iso["x"]
        y_v = iso["y"]
        z_v = iso["z"]
        faces = iso["faces"]
        omega_v = iso["omega_span"]

        if len(faces) > 0:
            sub_faces = faces[::2]
            face_omega = np.mean(omega_v[sub_faces], axis=1)
            face_colors = cmap(norm(face_omega))

            mesh_verts = np.column_stack([x_v, y_v, z_v])
            tri_polys = [mesh_verts[f] for f in sub_faces]
            poly_col = Poly3DCollection(tri_polys, facecolors=face_colors, edgecolors="none", alpha=0.88)
            ax3d.add_collection3d(poly_col)

        # 3D Cylinder geometry (solid vertical cylinder, height 1.2m = 4D)
        theta = np.linspace(0, 2 * np.pi, 30)
        y_cyl = np.linspace(-0.6, 0.6, 10)
        theta_g, y_cyl_g = np.meshgrid(theta, y_cyl)
        x_cyl = CYLINDER_RADIUS * np.cos(theta_g)
        z_cyl = CYLINDER_RADIUS * np.sin(theta_g)
        ax3d.plot_surface(x_cyl, y_cyl_g, z_cyl, color="#2c303e", edgecolor="#4a5068", lw=0.3, alpha=0.96)

        # Inflow arrow indicator
        ax3d.quiver(-0.55, 0, 0, 0.30, 0, 0, color="#30d158", lw=2.2, arrow_length_ratio=0.3)
        ax3d.text(-0.58, 0, 0.15, "Inflow U0=0.5m/s", color="#30d158", fontsize=9, fontweight="bold")

        ax3d.set_xlim(-0.6, 2.6)
        ax3d.set_ylim(-0.6, 0.6)
        ax3d.set_zlim(-0.6, 0.6)
        ax3d.view_init(elev=FIXED_ELEV, azim=FIXED_AZIM)

        ax3d.set_xlabel("Streamwise x [m] (Length 10D)", fontsize=9, color="#8e8e93", labelpad=2)
        ax3d.set_ylabel("Spanwise y [m] (Span 4D)", fontsize=9, color="#8e8e93", labelpad=2)
        ax3d.set_zlabel("Crossflow z [m] (Width 4D)", fontsize=9, color="#8e8e93", labelpad=2)
        ax3d.tick_params(colors="#8e8e93", labelsize=8)
        ax3d.xaxis.pane.fill = False
        ax3d.yaxis.pane.fill = False
        ax3d.zaxis.pane.fill = False
        ax3d.xaxis.pane.set_edgecolor("#1a1e2a")
        ax3d.yaxis.pane.set_edgecolor("#1a1e2a")
        ax3d.zaxis.pane.set_edgecolor("#1a1e2a")

        # Colorbar
        sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        cbar = plt.colorbar(sm, ax=ax3d, fraction=0.015, pad=0.02, shrink=0.65)
        cbar.set_label("Spanwise Vorticity wz [1/s]", color="#ffffff", fontsize=8)
        cbar.ax.tick_params(colors="#8e8e93", labelsize=7)

        # ----------------------------------------------------
        # Panel 2: Synchronized Full 0-60s Hydrodynamic Force HUD
        # ----------------------------------------------------
        ax_force = fig.add_subplot(gs[1])
        ax_force.set_facecolor("#12141c")

        # Drag curve on right axis (omit t=0 spike by starting plot at 0.5s or clipping)
        ax_drag = ax_force.twinx()
        mask_stable_drag = t_all >= 0.5
        l_cd, = ax_drag.plot(t_all[mask_stable_drag], cd_all[mask_stable_drag], color="#ff9f0a", lw=1.5, alpha=0.85, label="Drag C_D(t)")
        ax_drag.set_ylabel("Drag C_D", color="#ff9f0a", fontsize=9)
        ax_drag.tick_params(colors="#ff9f0a", labelsize=8)
        ax_drag.set_ylim(0.25, 1.25)
        ax_drag.spines["right"].set_color("#ff9f0a")
        ax_drag.spines["left"].set_color("#30d158")
        ax_drag.spines["top"].set_visible(False)
        ax_drag.spines["bottom"].set_color("#3a3e52")

        # Lift curve on left axis
        l_cl, = ax_force.plot(t_all, cl_all, color="#30d158", lw=1.8, label="Lift C_L(t)")
        ax_force.set_ylabel("Lift C_L", color="#30d158", fontsize=9)
        ax_force.tick_params(colors="#30d158", labelsize=8)
        ax_force.set_ylim(-0.12, 0.14)
        ax_force.axhline(0, color="#48484a", ls="--", lw=0.8)

        # Dynamic tracking timeline cursor across full 0-60s
        ax_force.axvline(t_cur, color="#ff453a", lw=2.0, ls="-")
        ax_force.scatter([t_cur], [cl_cur], color="#ff453a", s=60, zorder=5)
        if t_cur >= 0.5:
            ax_drag.scatter([t_cur], [cd_cur], color="#ff9f0a", s=40, zorder=5)

        ax_force.set_xlim(0.0, 60.0)
        ax_force.set_xlabel("Physical Time t [s] (Full 0 - 60s Simulation History)", color="#ffffff", fontsize=9)
        ax_force.grid(True, color="#1f2438", ls=":", lw=0.6)
        ax_force.set_title(
            f"Full 0-60s Hydrodynamic Force History | Current Frame: t = {t_cur:.2f} s  |  C_L = {cl_cur:+0.4f}  |  C_D = {cd_cur:0.4f}  |  {regime}",
            fontsize=10, fontweight="bold", color="#ffffff", pad=4
        )

        # ----------------------------------------------------
        # Master Telemetry Title Banner
        # ----------------------------------------------------
        fig.suptitle(
            f"C04 3D Cylinder Flow (Re=126,167) - Full Lifecycle Evolution (t=0 to 60s)\n"
            f"Time t = {t_cur:.2f} s  |  t* = {t_star:.1f}  |  Fixed Isometric View (Elev=22 deg, Azim=-55 deg)  |  Q = {Q_ISO_LEVEL} 1/s^2",
            fontsize=12, fontweight="bold", color="#ffffff", y=0.98
        )

        frame_file = FRAMES_DIR / f"frame_{idx:03d}.png"
        fig.savefig(frame_file, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        frame_paths.append(frame_file)

        dt_render = time.time() - t_frame_start
        print(f"[RENDER] Frame {idx+1}/{total_frames} (step {sn}, t={t_cur:.2f}s) completed in {dt_render:.1f}s -> {frame_file.name}")

    return frame_paths


def compile_videos(frame_paths: list[Path]) -> tuple[Path, Path, Path]:
    """Compile PNG frames into full lifecycle H.264 MP4, animated GIF, and 6-milestone gallery PNG."""
    mp4_path = OUT_DIR / "c04_3d_vortex_full_lifecycle.mp4"
    gif_path = OUT_DIR / "c04_3d_vortex_full_lifecycle.gif"
    gallery_path = OUT_DIR / "c04_3d_lifecycle_6phases.png"

    # 1. H.264 MP4 Video (10 fps)
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[ENCODE] Encoding full lifecycle H.264 MP4 using {ffmpeg_exe}...")
    cmd = [
        ffmpeg_exe, "-y",
        "-framerate", "10",
        "-i", str(FRAMES_DIR / "frame_%03d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        str(mp4_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and mp4_path.is_file():
        print(f"[ENCODE] Saved MP4: {mp4_path} ({mp4_path.stat().st_size / 1024:.1f} KB)")
    else:
        print(f"[ENCODE] MP4 encoding warning: {res.stderr}")

    # 2. Animated GIF (scaled to 900x540)
    print(f"[ENCODE] Generating animated GIF from {len(frame_paths)} frames...")
    frame_images = [Image.open(f) for f in frame_paths]
    resample_mode = getattr(Image, "Resampling", Image).BILINEAR
    gif_frames = [f.resize((900, 540), resample_mode) for f in frame_images]
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=100,  # 10 fps -> 100ms per frame
        loop=0,
        optimize=True
    )
    print(f"[ENCODE] Saved GIF: {gif_path} ({gif_path.stat().st_size / 1024 / 1024:.2f} MB)")

    # 3. 6-Milestone Comparison Gallery PNG (Startup -> Separation -> Bifurcation -> 1st Shedding -> Developed -> Final)
    print("[ENCODE] Creating 6-Milestone Lifecycle Gallery...")
    n_frames = len(frame_paths)
    milestone_indices = [
        0,                                   # Frame 1: t = 0.0s (Quiescent / Initial impulse)
        min(8, n_frames - 1),                # t ~ 1.92s (Symmetric separation & bubble growth)
        min(18, n_frames - 1),               # t ~ 4.32s (Elongated bubble & wave oscillation)
        min(26, n_frames - 1),               # t ~ 10.0s (First Karman vortex detachment)
        min(40, n_frames - 1),               # t ~ 26.0s (Transition to periodic shedding)
        n_frames - 1                         # t = 60.0s (Fully developed 3D turbulent vortex street)
    ]
    milestone_labels = [
        "1. Initial State (t = 0.0 s): Flow Startup & Front Impingement",
        "2. Bubble Growth (t = 1.9 s): Symmetric Recirculation Separation",
        "3. Symmetry Breaking (t = 4.3 s): Shear Layer Wave Instability",
        "4. First Shedding (t = 10.0 s): Primary Karman Vortex Detachment",
        "5. Periodic Transition (t = 26.0 s): Wake Expansion & Regular Cycles",
        "6. Fully Developed (t = 60.0 s): 3D Turbulent Street & Secondary Ribs"
    ]

    fig, axes = plt.subplots(3, 2, figsize=(18, 18), dpi=140, facecolor="#08080c")
    plt.subplots_adjust(left=0.01, right=0.99, top=0.95, bottom=0.01, hspace=0.10, wspace=0.04)

    for i, m_idx in enumerate(milestone_indices):
        ax = axes[i // 2, i % 2]
        img_f = frame_paths[m_idx]
        img_arr = np.asarray(Image.open(img_f))
        ax.imshow(img_arr)
        ax.axis("off")
        ax.set_title(f"Milestone: {milestone_labels[i]}", fontsize=11, fontweight="bold", color="#30d158", pad=6)

    fig.suptitle(
        "C04 Re=126,167 3D Cylinder Flow - Full Lifecycle Evolution (t = 0.0 s to 60.0 s)\n"
        "6 Key Milestone Phases: From Quiescent Initial Startup to Fully Developed 3D Turbulent Vortex Street",
        fontsize=14, fontweight="bold", color="#ffffff", y=0.98
    )
    fig.savefig(gallery_path, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    print(f"[ENCODE] Saved 6-Milestone Gallery: {gallery_path} ({gallery_path.stat().st_size / 1024:.1f} KB)")

    return mp4_path, gif_path, gallery_path


def main():
    t_start = time.time()
    print("=" * 70)
    print("C04 3D Cylinder Flow: Full Lifecycle (t=0 to 60s) Video Pipeline")
    print("=" * 70)

    # 1. Export missing batch steps
    export_batch_steps(FULL_LIFECYCLE_STEPS, MODEL_PATH, OUT_DIR)

    # 2. Load hydrodynamic force telemetry
    force_data = load_force_series(FORCE_CSV)

    # 3. Render all frames across full 60s
    frame_paths = render_all_lifecycle_frames(FULL_LIFECYCLE_STEPS, OUT_DIR, force_data)

    # 4. Compile videos & gallery
    mp4, gif, gallery = compile_videos(frame_paths)

    total_time = time.time() - t_start
    print("=" * 70)
    print(f"[SUCCESS] Full Lifecycle Pipeline completed in {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"  MP4 Video:   {mp4}")
    print(f"  GIF Video:   {gif}")
    print(f"  6-Phase PNG: {gallery}")
    print("=" * 70)


if __name__ == "__main__":
    main()
