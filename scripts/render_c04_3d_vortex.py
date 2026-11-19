#!/usr/bin/env python3
"""
render_c04_3d_vortex.py - Publication-Grade 3D Vortex Shedding Visualization for C04 Flow.

Generates:
1. c04_3d_vortex_interactive.html: Full interactive 3D WebGL dashboard (Plotly)
   - 3D Q-criterion vortex core tubes colored by spanwise vorticity
   - 3D solid cylinder model
   - 2D mid-span vorticity contour cut-plane
2. c04_3d_vortex_multiview.png: High-resolution 4-view publication gallery
   - 3D Isometric View, Top View (X-Y spanwise), Side View (X-Z crossflow), Downstream View
3. c04_3d_vortex_orbit.mp4 & .gif: Dynamic 360° 3D orbital fly-around animation
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import subprocess

import imageio_ffmpeg
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image
from scipy.ndimage import map_coordinates
from skimage.measure import marching_cubes

REPO_ROOT = Path(r"D:\HydroDisturb-Bench")
OUT_DIR = Path(r"E:\cfd_3d\05_reports_viz\video_c04_shedding")
NPZ_PATH = OUT_DIR / "c04_vol_t50.04.npz"

CYLINDER_DIAMETER = 0.30
CYLINDER_RADIUS = 0.15
INFLOW_U0 = 0.50
RE_NUMBER = 126167


def load_and_compute_vortex_fields(npz_file: Path):
    """Load 3D volume, compute velocity gradients, Q-criterion and vorticity."""
    print(f"[3D] Loading dataset: {npz_file.name}...")
    ds = np.load(npz_file)
    x = ds["x"]              # (192,)
    y_span = ds["y_span"]    # (64,)
    z_cross = ds["z_cross"]  # (96,)

    # Replace NaNs inside solid cylinder
    u = np.nan_to_num(ds["u"], nan=0.0)          # (96, 64, 192) -> (zc, ys, x)
    v_s = np.nan_to_num(ds["v_span"], nan=0.0)
    w_c = np.nan_to_num(ds["w_cross"], nan=0.0)
    p = np.nan_to_num(ds["p"], nan=0.0)

    dx = float(x[1] - x[0])
    dys = float(y_span[1] - y_span[0])
    dzc = float(z_cross[1] - z_cross[0])

    print("[3D] Computing 3D velocity gradients and Q-criterion...")
    du_dx = np.gradient(u, dx, axis=2)
    du_dys = np.gradient(u, dys, axis=1)
    du_dzc = np.gradient(u, dzc, axis=0)

    dw_dx = np.gradient(w_c, dx, axis=2)
    dw_dys = np.gradient(w_c, dys, axis=1)
    dw_dzc = np.gradient(w_c, dzc, axis=0)

    dv_dx = np.gradient(v_s, dx, axis=2)
    dv_dys = np.gradient(v_s, dys, axis=1)
    dv_dzc = np.gradient(v_s, dzc, axis=0)

    # Vorticity vector
    omega_x = dw_dys - dv_dzc
    omega_span = dw_dx - du_dzc  # spanwise vorticity
    omega_cross = dv_dx - du_dys
    vort_mag = np.sqrt(omega_x**2 + omega_span**2 + omega_cross**2)

    # Q-criterion: Q = 0.5*(||Omega||^2 - ||S||^2)
    Q = -0.5 * (
        du_dx**2 + dw_dzc**2 + dv_dys**2
        + 2 * du_dzc * dw_dx
        + 2 * du_dys * dv_dx
        + 2 * dw_dys * dv_dzc
    )

    # Mask solid cylinder
    X_grid, ZC_grid = np.meshgrid(x, z_cross)
    solid_mask = np.sqrt(X_grid**2 + ZC_grid**2) <= (CYLINDER_RADIUS + 0.005)

    for j in range(len(y_span)):
        Q[:, j, :][solid_mask] = -100.0
        vort_mag[:, j, :][solid_mask] = 0.0

    return {
        "x": x, "y_span": y_span, "z_cross": z_cross,
        "u": u, "v_s": v_s, "w_c": w_c, "p": p,
        "omega_x": omega_x, "omega_span": omega_span, "omega_cross": omega_cross,
        "vort_mag": vort_mag, "Q": Q,
        "dx": dx, "dys": dys, "dzc": dzc,
        "solid_mask": solid_mask
    }


def extract_3d_vortex_isosurface(fields, q_level=0.85):
    """Extract Q-criterion isosurface with Marching Cubes and interpolate field values."""
    print(f"[3D] Extracting 3D isosurface at Q = {q_level:.2f} s^-2...")
    Q = fields["Q"]
    dzc = fields["dzc"]
    dys = fields["dys"]
    dx = fields["dx"]
    z_cross = fields["z_cross"]
    y_span = fields["y_span"]
    x = fields["x"]

    # Marching cubes on (zc, ys, x)
    verts, faces, normals, values = marching_cubes(Q, level=q_level, spacing=(dzc, dys, dx))

    # Physical coordinates
    zc_v = z_cross[0] + verts[:, 0]
    ys_v = y_span[0] + verts[:, 1]
    x_v = x[0] + verts[:, 2]

    # Convert coordinates to normalized indices for interpolation
    zc_idx = (zc_v - z_cross[0]) / dzc
    ys_idx = (ys_v - y_span[0]) / dys
    x_idx = (x_v - x[0]) / dx
    coords = np.vstack([zc_idx, ys_idx, x_idx])

    # Interpolate spanwise vorticity and streamwise velocity on vertices
    omega_span_v = map_coordinates(fields["omega_span"], coords, order=1, mode="nearest")
    u_v = map_coordinates(fields["u"], coords, order=1, mode="nearest")

    print(f"[3D] Isosurface extracted: {len(x_v)} vertices, {len(faces)} triangular faces.")
    return {
        "x": x_v, "y": ys_v, "z": zc_v,
        "faces": faces,
        "normals": normals,
        "omega_span": omega_span_v,
        "u": u_v
    }


def generate_interactive_html(fields, iso):
    """Generate full interactive 3D WebGL visualization with Plotly."""
    print("[3D] Building interactive 3D HTML dashboard...")
    x_v = iso["x"]
    y_v = iso["y"]
    z_v = iso["z"]
    faces = iso["faces"]
    omega_v = iso["omega_span"]

    # In canonical presentation:
    # X: Streamwise [m]
    # Y: Spanwise [m] (center around 0 for clean display)
    # Z: Crossflow [m]
    y_centered = y_v - np.mean(fields["y_span"])

    # 1. 3D Vortex Core Tubes (Mesh3d)
    vortex_mesh = go.Mesh3d(
        x=x_v,
        y=y_centered,
        z=z_v,
        i=faces[:, 0],
        j=faces[:, 1],
        k=faces[:, 2],
        intensity=np.clip(omega_v, -15.0, 15.0),
        colorscale="RdBu_r",
        cmin=-12.0,
        cmax=12.0,
        colorbar=dict(
            title=dict(text="Spanwise Vorticity &omega;<sub>z</sub> [s<sup>-1</sup>]", font=dict(color="#ffffff", size=12)),
            tickfont=dict(color="#ffffff", size=10),
            len=0.6,
            thickness=18,
            x=0.92,
            y=0.5
        ),
        opacity=0.92,
        lighting=dict(ambient=0.45, diffuse=0.75, specular=0.4, roughness=0.35, fresnel=0.2),
        name="3D Vortex Tubes (Q-Criterion)",
        hovertemplate="x: %{x:.2f} m<br>y: %{y:.2f} m<br>z: %{z:.2f} m<br>&omega;<sub>z</sub>: %{intensity:.2f} s⁻¹<extra></extra>"
    )

    # 2. 3D Solid Cylinder Tube
    theta = np.linspace(0, 2 * np.pi, 45)
    y_cyl_1d = np.linspace(y_centered.min(), y_centered.max(), 15)
    theta_grid, y_cyl_grid = np.meshgrid(theta, y_cyl_1d)
    x_cyl = CYLINDER_RADIUS * np.cos(theta_grid)
    z_cyl = CYLINDER_RADIUS * np.sin(theta_grid)

    cylinder_surf = go.Surface(
        x=x_cyl,
        y=y_cyl_grid,
        z=z_cyl,
        colorscale=[[0, "#2c303d"], [1, "#1a1d26"]],
        showscale=False,
        lighting=dict(ambient=0.6, diffuse=0.6, specular=0.8, roughness=0.2),
        name="Cylinder Body (D=0.30m)",
        hoverinfo="skip"
    )

    # 3. 2D Mid-Span Vorticity Cut Plane (z = 0 m in span)
    mid_j = len(fields["y_span"]) // 2
    wz_slice = fields["omega_span"][:, mid_j, :] # (96, 192) -> (zc, x)
    x_grid_2d, zc_grid_2d = np.meshgrid(fields["x"], fields["z_cross"])
    y_plane = np.zeros_like(x_grid_2d)

    slice_surf = go.Surface(
        x=x_grid_2d,
        y=y_plane,
        z=zc_grid_2d,
        surfacecolor=np.clip(wz_slice, -15.0, 15.0),
        colorscale="RdBu_r",
        cmin=-15.0,
        cmax=15.0,
        showscale=False,
        opacity=0.45,
        name="Mid-Span Slice (y=0)",
        hoverinfo="skip"
    )

    fig = go.Figure(data=[vortex_mesh, cylinder_surf, slice_surf])

    # Layout configuration
    fig.update_layout(
        title=dict(
            text="<b>3D C04 Seawater Cylinder Flow (Re = 126,167) — Vortex Shedding Cores (Q-Criterion)</b><br>"
                 "<span style='font-size:12px;color:#30d158'>Time t = 50.04 s (t* = 83.4) | Phase &Phi; = 0&deg; (Max Lift Peak) | Cores Colored by &omega;<sub>z</sub></span>",
            font=dict(color="#ffffff", size=15),
            x=0.05, y=0.96
        ),
        paper_bgcolor="#0a0b10",
        plot_bgcolor="#0a0b10",
        scene=dict(
            xaxis=dict(
                title=dict(text="Streamwise x [m]", font=dict(color="#8e8e93")),
                tickfont=dict(color="#8e8e93"),
                backgroundcolor="#0e1017",
                gridcolor="#2c303e",
                range=[-0.6, 2.8]
            ),
            yaxis=dict(
                title=dict(text="Spanwise y [m]", font=dict(color="#8e8e93")),
                tickfont=dict(color="#8e8e93"),
                backgroundcolor="#0e1017",
                gridcolor="#2c303e",
                range=[-0.65, 0.65]
            ),
            zaxis=dict(
                title=dict(text="Crossflow z [m]", font=dict(color="#8e8e93")),
                tickfont=dict(color="#8e8e93"),
                backgroundcolor="#0e1017",
                gridcolor="#2c303e",
                range=[-0.7, 0.7]
            ),
            aspectratio=dict(x=3.4, y=1.3, z=1.4),
            camera=dict(
                eye=dict(x=1.8, y=-1.6, z=1.1),
                center=dict(x=0.3, y=0.0, z=0.0)
            )
        ),
        margin=dict(l=0, r=0, t=60, b=0),
        legend=dict(
            font=dict(color="#ffffff", size=11),
            x=0.03, y=0.88,
            bgcolor="rgba(20,22,30,0.7)"
        )
    )

    html_path = OUT_DIR / "c04_3d_vortex_interactive.html"
    fig.write_html(str(html_path), include_plotlyjs="cdn")
    print(f"[3D] Saved interactive HTML: {html_path} ({html_path.stat().st_size / 1024:.1f} KB)")
    return html_path


def render_multiview_png(fields, iso):
    """Generate 4-perspective static publication gallery (DPI 200)."""
    print("[3D] Rendering publication-grade 4-view gallery PNG...")
    x_v = iso["x"]
    y_v = iso["y"] - np.mean(fields["y_span"])
    z_v = iso["z"]
    faces = iso["faces"]
    omega_v = iso["omega_span"]

    # Subsample faces slightly for clean Matplotlib 3D rasterization
    step = 2
    sub_faces = faces[::step]
    sub_verts_idx = np.unique(sub_faces)

    # Normalized colors from RdBu_r
    norm = plt.Normalize(vmin=-12.0, vmax=12.0)
    cmap = plt.get_cmap("RdBu_r")

    # Face colors
    face_omega = np.mean(omega_v[sub_faces], axis=1)
    face_colors = cmap(norm(face_omega))

    views = [
        ("3D Isometric Perspective (Azimuth 45°, Elevation 25°)", 25, -45),
        ("Top View X-Y (Spanwise Vortex Dislocations & Ribs)", 88, -90),
        ("Side View X-Z (Crossflow Alternating Kármán Shedding)", 0, -90),
        ("Oblique Downstream View (Wakeside Cores)", 20, -135)
    ]

    fig = plt.figure(figsize=(16, 12), dpi=180, facecolor="#08080c")
    plt.subplots_adjust(left=0.02, right=0.98, top=0.92, bottom=0.03, hspace=0.15, wspace=0.08)

    for i, (title, elev, azim) in enumerate(views):
        ax = fig.add_subplot(2, 2, i + 1, projection="3d")
        ax.set_facecolor("#08080c")

        # Triangles
        mesh_verts = np.column_stack([x_v, y_v, z_v])
        tri_polys = [mesh_verts[f] for f in sub_faces]
        poly_col = Poly3DCollection(tri_polys, facecolors=face_colors, edgecolors="none", alpha=0.85)
        ax.add_collection3d(poly_col)

        # Draw cylinder tube
        theta = np.linspace(0, 2 * np.pi, 30)
        y_cyl = np.linspace(-0.6, 0.6, 10)
        theta_g, y_cyl_g = np.meshgrid(theta, y_cyl)
        x_cyl = CYLINDER_RADIUS * np.cos(theta_g)
        z_cyl = CYLINDER_RADIUS * np.sin(theta_g)
        ax.plot_surface(x_cyl, y_cyl_g, z_cyl, color="#2c303e", edgecolor="#4a5068", lw=0.4, alpha=0.95)

        ax.set_xlim(-0.6, 2.6)
        ax.set_ylim(-0.6, 0.6)
        ax.set_zlim(-0.6, 0.6)
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(title, fontsize=11, fontweight="bold", color="#30d158", pad=8)
        ax.set_xlabel("Streamwise x [m]", fontsize=8, color="#8e8e93", labelpad=2)
        ax.set_ylabel("Spanwise y [m]", fontsize=8, color="#8e8e93", labelpad=2)
        ax.set_zlabel("Crossflow z [m]", fontsize=8, color="#8e8e93", labelpad=2)
        ax.tick_params(colors="#8e8e93", labelsize=7)
        ax.xaxis.pane.fill = False
        ax.yaxis.pane.fill = False
        ax.zaxis.pane.fill = False
        ax.xaxis.pane.set_edgecolor("#1f2330")
        ax.yaxis.pane.set_edgecolor("#1f2330")
        ax.zaxis.pane.set_edgecolor("#1f2330")

    fig.suptitle(
        "C04 Re=126,167 Seawater Cylinder Flow — 3D Vortex Shedding Structures (Q = 0.85 s⁻²)\n"
        "Time t = 50.04 s | Phase Φ = 0° (Max Positive Lift Peak) | Cores Colored by Spanwise Vorticity ω_z",
        fontsize=14, fontweight="bold", color="#ffffff", y=0.98
    )

    out_png = OUT_DIR / "c04_3d_vortex_multiview.png"
    fig.savefig(out_png, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    print(f"[3D] Saved 4-view gallery PNG: {out_png} ({out_png.stat().st_size / 1024:.1f} KB)")
    return out_png


def render_orbit_video(fields, iso):
    """Render 360-degree orbital fly-around animation (MP4 and GIF)."""
    print("[3D] Rendering 360° orbital animation (36 frames, 10° step)...")
    x_v = iso["x"]
    y_v = iso["y"] - np.mean(fields["y_span"])
    z_v = iso["z"]
    faces = iso["faces"]
    omega_v = iso["omega_span"]

    # Subsample for smooth video rendering
    step = 3
    sub_faces = faces[::step]
    norm = plt.Normalize(vmin=-12.0, vmax=12.0)
    cmap = plt.get_cmap("RdBu_r")
    face_omega = np.mean(omega_v[sub_faces], axis=1)
    face_colors = cmap(norm(face_omega))

    # Pre-build figure (1000 x 650)
    fig = plt.figure(figsize=(10, 6.5), dpi=100, facecolor="#08080c")
    ax = fig.add_subplot(1, 1, 1, projection="3d")
    ax.set_facecolor("#08080c")

    mesh_verts = np.column_stack([x_v, y_v, z_v])
    tri_polys = [mesh_verts[f] for f in sub_faces]
    poly_col = Poly3DCollection(tri_polys, facecolors=face_colors, edgecolors="none", alpha=0.88)
    ax.add_collection3d(poly_col)

    # Cylinder
    theta = np.linspace(0, 2 * np.pi, 25)
    y_cyl = np.linspace(-0.6, 0.6, 8)
    theta_g, y_cyl_g = np.meshgrid(theta, y_cyl)
    x_cyl = CYLINDER_RADIUS * np.cos(theta_g)
    z_cyl = CYLINDER_RADIUS * np.sin(theta_g)
    ax.plot_surface(x_cyl, y_cyl_g, z_cyl, color="#2c303e", edgecolor="#4a5068", lw=0.3, alpha=0.95)

    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(-0.6, 0.6)
    ax.set_zlim(-0.6, 0.6)
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    ax.xaxis.pane.set_edgecolor("#1f2330")
    ax.yaxis.pane.set_edgecolor("#1f2330")
    ax.zaxis.pane.set_edgecolor("#1f2330")
    ax.tick_params(colors="#8e8e93", labelsize=8)

    title_txt = ax.set_title("", fontsize=11, fontweight="bold", color="#30d158", pad=8)

    hud_banner = fig.text(
        0.05, 0.94,
        "C04 Re=126,167 | 3D VORTEX SHEDDING DYNAMICS (Q = 0.85 s⁻²) | 360° ORBIT",
        fontsize=12, fontweight="bold", color="#ffffff"
    )

    orbit_frames_dir = OUT_DIR / "orbit_frames"
    orbit_frames_dir.mkdir(parents=True, exist_ok=True)

    n_angles = 36
    azims = np.linspace(0, 360, n_angles, endpoint=False)
    frame_images = []

    for idx, az in enumerate(azims):
        # Vary elevation slightly for dynamic helicopter view
        elev = 22.0 + 8.0 * np.sin(np.radians(az * 2.0))
        ax.view_init(elev=elev, azim=az)
        title_txt.set_text(f"Camera View: Azimuth = {az:.0f}° | Elevation = {elev:.1f}° | Time t = 50.04 s")

        fig.canvas.draw()
        rgba = np.asarray(fig.canvas.buffer_rgba())
        frame_rgb = rgba[:, :, :3].copy()

        frame_file = orbit_frames_dir / f"orbit_{idx:02d}.png"
        img = Image.fromarray(frame_rgb)
        img.save(frame_file)
        frame_images.append(img)

    plt.close(fig)

    # 1. Compile MP4
    mp4_path = OUT_DIR / "c04_3d_vortex_orbit.mp4"
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-framerate", "12",
            "-i", str(orbit_frames_dir / "orbit_%02d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            str(mp4_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and mp4_path.is_file():
            print(f"[3D] Saved 3D Orbit MP4: {mp4_path} ({mp4_path.stat().st_size / 1024:.1f} KB)")
    except Exception as e:
        print(f"[3D] Warning: Could not encode orbit MP4: {e}")

    # 2. Compile Animated GIF
    gif_path = OUT_DIR / "c04_3d_vortex_orbit.gif"
    resample_mode = getattr(Image, "Resampling", Image).BILINEAR
    gif_frames = [f.resize((800, 520), resample_mode) for f in frame_images]
    gif_frames[0].save(
        gif_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=83,  # ~12 fps
        loop=0,
        optimize=True
    )
    print(f"[3D] Saved 3D Orbit GIF: {gif_path} ({gif_path.stat().st_size / 1024 / 1024:.2f} MB)")

    return mp4_path, gif_path


def main():
    t_start = time.time()
    fields = load_and_compute_vortex_fields(NPZ_PATH)
    iso = extract_3d_vortex_isosurface(fields, q_level=0.85)

    html = generate_interactive_html(fields, iso)
    png = render_multiview_png(fields, iso)
    mp4, gif = render_orbit_video(fields, iso)

    print(f"\n[DONE] All 3D vortex visualizations completed in {time.time() - t_start:.1f}s!")
    print(f"1. Interactive 3D HTML: {html}")
    print(f"2. Multi-view Gallery: {png}")
    print(f"3. 360° Orbit MP4:     {mp4}")
    print(f"4. 360° Orbit GIF:     {gif}")


if __name__ == "__main__":
    main()
