"""3D and 2D-slice world model grid generation and sampling for C04 high-Re benchmark."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .comsol_java.file_transactions import publish_staging_file

C04_CHANNELS = ("u", "v", "w", "p")
C04_UNITS = ("m/s", "m/s", "m/s", "Pa")
C04_LAYOUT_3D = "(T, 4, Nz, Ny, Nx)"
C04_LAYOUT_2D = "(T, 4, Ny, Nx)"


@dataclass(frozen=True)
class C04GridCoordinates:
    """Cell-centered coordinates and geometry masks for 3D sampling domain."""

    x_1d: np.ndarray  # Shape: (nx,)
    y_1d: np.ndarray  # Shape: (ny,)
    z_1d: np.ndarray  # Shape: (nz,)
    X: np.ndarray  # Shape: (nz, ny, nx) or meshgrid
    Y: np.ndarray
    Z: np.ndarray
    solid_mask: np.ndarray  # Shape: (nz, ny, nx), bool (True inside cylinder)
    sdf: np.ndarray  # Shape: (nz, ny, nx), float32 distance to cylinder surface (<=0 inside)
    cell_spacing: float  # Δ = D / 16
    cylinder_diameter: float
    cylinder_radius: float

    @property
    def shape(self) -> tuple[int, int, int]:
        """Returns (nz, ny, nx)."""
        return (len(self.z_1d), len(self.y_1d), len(self.x_1d))

    @property
    def total_points(self) -> int:
        """Total sampling points."""
        return len(self.z_1d) * len(self.y_1d) * len(self.x_1d)


@dataclass(frozen=True)
class C04SliceCoordinates:
    """Cell-centered 2D slice coordinates at fixed z."""

    x_1d: np.ndarray  # Shape: (nx,)
    y_1d: np.ndarray  # Shape: (ny,)
    z_fixed: float
    X: np.ndarray  # Shape: (ny, nx)
    Y: np.ndarray
    solid_mask: np.ndarray  # Shape: (ny, nx), bool
    sdf: np.ndarray  # Shape: (ny, nx), float32
    cell_spacing: float
    cylinder_diameter: float

    @property
    def shape(self) -> tuple[int, int]:
        """Returns (ny, nx)."""
        return (len(self.y_1d), len(self.x_1d))

    @property
    def total_points(self) -> int:
        """Total 2D slice points."""
        return len(self.y_1d) * len(self.x_1d)


def create_c04_sampling_grid(
    D: float = 0.30,
    x_range_D: tuple[float, float] = (-2.0, 10.0),
    y_range_D: tuple[float, float] = (-3.0, 3.0),
    z_range_D: tuple[float, float] = (3.0, 7.0),
    cells_per_D: int = 16,
) -> C04GridCoordinates:
    """Generate cell-centered 3D regular grid according to C04 specification.

    Default specifications:
    - x: [-2D, 10D] = 12D length -> 192 cells with Δ = D/16 = 0.01875 m
    - y: [-3D, 3D]  = 6D width  -> 96 cells
    - z: [3D, 7D]   = 4D depth  -> 64 cells
    - Centering: Cell-centered, coordinates = x_min + (i + 0.5) * Δ
    """
    R = D / 2.0
    delta = D / float(cells_per_D)

    nx = int(round((x_range_D[1] - x_range_D[0]) * cells_per_D))
    ny = int(round((y_range_D[1] - y_range_D[0]) * cells_per_D))
    nz = int(round((z_range_D[1] - z_range_D[0]) * cells_per_D))

    x_min = x_range_D[0] * D
    y_min = y_range_D[0] * D
    z_min = z_range_D[0] * D

    x_1d = (x_min + (np.arange(nx, dtype=np.float32) + 0.5) * delta).astype(np.float32)
    y_1d = (y_min + (np.arange(ny, dtype=np.float32) + 0.5) * delta).astype(np.float32)
    z_1d = (z_min + (np.arange(nz, dtype=np.float32) + 0.5) * delta).astype(np.float32)

    # 3D meshgrid with indexing 'ij' on (z, y, x)
    Z, Y, X = np.meshgrid(z_1d, y_1d, x_1d, indexing="ij")

    # Cylinder axis is along z at (x, y) = (0, 0)
    radial_dist = np.sqrt(X**2 + Y**2).astype(np.float32)
    sdf = (radial_dist - R).astype(np.float32)
    solid_mask = radial_dist <= R

    return C04GridCoordinates(
        x_1d=x_1d,
        y_1d=y_1d,
        z_1d=z_1d,
        X=X,
        Y=Y,
        Z=Z,
        solid_mask=solid_mask,
        sdf=sdf,
        cell_spacing=delta,
        cylinder_diameter=D,
        cylinder_radius=R,
    )


def create_c04_2d_slice_grid(
    D: float = 0.30,
    z_fixed: float | None = None,
    x_range_D: tuple[float, float] = (-2.0, 10.0),
    y_range_D: tuple[float, float] = (-3.0, 3.0),
    cells_per_D: int = 16,
) -> C04SliceCoordinates:
    """Generate 2D slice cell-centered grid at fixed z (default: z = 5D = 1.5 m)."""
    R = D / 2.0
    delta = D / float(cells_per_D)

    if z_fixed is None:
        z_fixed = 5.0 * D

    nx = int(round((x_range_D[1] - x_range_D[0]) * cells_per_D))
    ny = int(round((y_range_D[1] - y_range_D[0]) * cells_per_D))

    x_min = x_range_D[0] * D
    y_min = y_range_D[0] * D

    x_1d = (x_min + (np.arange(nx, dtype=np.float32) + 0.5) * delta).astype(np.float32)
    y_1d = (y_min + (np.arange(ny, dtype=np.float32) + 0.5) * delta).astype(np.float32)

    Y, X = np.meshgrid(y_1d, x_1d, indexing="ij")

    radial_dist = np.sqrt(X**2 + Y**2).astype(np.float32)
    sdf = (radial_dist - R).astype(np.float32)
    solid_mask = radial_dist <= R

    return C04SliceCoordinates(
        x_1d=x_1d,
        y_1d=y_1d,
        z_fixed=float(z_fixed),
        X=X,
        Y=Y,
        solid_mask=solid_mask,
        sdf=sdf,
        cell_spacing=delta,
        cylinder_diameter=D,
    )


def export_sampling_coordinates_csv(
    grid: C04GridCoordinates | C04SliceCoordinates,
    output_path: Path | str,
) -> Path:
    """Export coordinates to CSV format for COMSOL Data Export interpolation."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(grid, C04GridCoordinates):
        x_flat = grid.X.ravel()
        y_flat = grid.Y.ravel()
        z_flat = grid.Z.ravel()
        coords = np.column_stack([x_flat, y_flat, z_flat])
        header = "% x [m], y [m], z [m]"
    else:
        x_flat = grid.X.ravel()
        y_flat = grid.Y.ravel()
        z_flat = np.full_like(x_flat, grid.z_fixed)
        coords = np.column_stack([x_flat, y_flat, z_flat])
        header = "% x [m], y [m], z [m]"

    np.savetxt(out, coords, fmt="%.6f", delimiter=",", header=header, comments="")
    return out


def save_c04_dataset(
    output_path: Path | str,
    fields: np.ndarray,
    grid: C04GridCoordinates | C04SliceCoordinates,
    times: np.ndarray,
    metadata: dict[str, Any] | None = None,
    allow_overwrite: bool = True,
) -> Path:
    """Save C04 dataset to validated NPZ archive with atomic staging and publish protection."""
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = np.asarray(fields, dtype=np.float32)
    times = np.asarray(times, dtype=np.float32)

    if not np.isfinite(fields).all():
        raise ValueError("Fields array contains non-finite (NaN or Inf) values")
    if not np.isfinite(times).all():
        raise ValueError("Times array contains non-finite (NaN or Inf) values")

    is_3d = isinstance(grid, C04GridCoordinates)
    expected_spatial_shape = grid.shape
    expected_shape = (len(times), 4, *expected_spatial_shape)

    if fields.shape != expected_shape:
        raise ValueError(f"Fields shape {fields.shape} does not match expected {expected_shape}")

    meta = {
        "case_id": "C04",
        "is_3d": is_3d,
        "channels": list(C04_CHANNELS),
        "units": list(C04_UNITS),
        "layout": C04_LAYOUT_3D if is_3d else C04_LAYOUT_2D,
        "spatial_shape": list(expected_spatial_shape),
        "num_frames": int(len(times)),
        "cell_spacing_m": float(grid.cell_spacing),
        "cylinder_diameter_m": float(grid.cylinder_diameter),
        "time_start_s": float(times[0]) if len(times) > 0 else 0.0,
        "time_end_s": float(times[-1]) if len(times) > 0 else 0.0,
    }
    if metadata:
        meta.update(metadata)

    staging_dir = out.parent / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / f".tmp_{uuid.uuid4().hex[:8]}_{out.name}"

    published_or_handed_off = False
    try:
        np.savez_compressed(
            staging_file,
            fields=fields,
            times=times,
            solid_mask=grid.solid_mask,
            sdf=grid.sdf,
            metadata_json=np.array(json.dumps(meta, indent=2, ensure_ascii=False)),
        )

        if not staging_file.is_file() or staging_file.stat().st_size == 0:
            raise ValueError(f"Staging file generation failed: {staging_file}")

        with np.load(staging_file, allow_pickle=False) as check_data:
            if "fields" not in check_data or "metadata_json" not in check_data:
                raise ValueError("Corrupted staging NPZ archive")

        published_or_handed_off = True
        publish_staging_file(
            staging_path=staging_file,
            output_path=out,
            stage_id="c04_npz",
            allow_overwrite=allow_overwrite,
        )
        return out
    finally:
        if not published_or_handed_off:
            staging_file.unlink(missing_ok=True)


def load_c04_dataset(archive_path: Path | str) -> dict[str, Any]:
    """Load and validate C04 NPZ dataset archive."""
    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError(f"Dataset archive not found: {path}")

    with np.load(path, allow_pickle=False) as data:
        fields = data["fields"]
        times = data["times"]
        solid_mask = data["solid_mask"]
        sdf = data["sdf"]
        meta_json_raw = str(data["metadata_json"])
        meta = json.loads(meta_json_raw)

    return {
        "fields": fields,
        "times": times,
        "solid_mask": solid_mask,
        "sdf": sdf,
        "metadata": meta,
    }
