"""Observation Operators, Sparse Sensor Layouts, and Sensory Inversion Benchmarking.

Provides standard underwater sensing layouts (downstream rakes, obstacle surface arrays,
moving AUV patrol paths), spatial interpolation from Eulerian grids, and realistic noise/dropout models.
Used to benchmark AI world models on state estimation and wake reconstruction under partial observability.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy.interpolate import RegularGridInterpolator


@dataclasses.dataclass(frozen=True)
class SensorProbe:
    """A single spatial flow sensor definition."""

    probe_id: str
    coords: tuple[float, ...]  # (x, y) for 2D or (x, y, z) for 3D in meters
    channels: tuple[str, ...] = ("u", "v", "w", "p")
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "coords": list(self.coords),
            "channels": list(self.channels),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorProbe:
        return cls(
            probe_id=data["probe_id"],
            coords=tuple(float(c) for c in data["coords"]),
            channels=tuple(data.get("channels", ("u", "v", "w", "p"))),
            description=data.get("description", ""),
        )


@dataclasses.dataclass(frozen=True)
class SensorLayout:
    """A collection of sensor probes forming an observation network."""

    name: str
    dim: Literal[2, 3]
    probes: tuple[SensorProbe, ...]
    layout_type: str = "custom"
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def num_probes(self) -> int:
        return len(self.probes)

    def probe_coordinates(self) -> np.ndarray:
        """Returns array of shape (num_probes, dim) with probe coordinates."""
        return np.array([p.coords for p in self.probes], dtype=np.float64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dim": self.dim,
            "layout_type": self.layout_type,
            "num_probes": self.num_probes,
            "metadata": self.metadata,
            "probes": [p.to_dict() for p in self.probes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorLayout:
        return cls(
            name=data["name"],
            dim=data["dim"],
            probes=tuple(SensorProbe.from_dict(p) for p in data["probes"]),
            layout_type=data.get("layout_type", "custom"),
            metadata=data.get("metadata", {}),
        )

    def save_json(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path | str) -> SensorLayout:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ============================================================================
# Standard Sensory Layout Generators
# ============================================================================


def make_downstream_rake_2d(
    x_stations: Sequence[float],
    y_min: float,
    y_max: float,
    n_probes_per_station: int = 17,
    name: str = "downstream_rake_2d",
) -> SensorLayout:
    """Creates a 2D downstream vertical sensor rake array (e.g. ADV / Pitot stations)."""
    probes: list[SensorProbe] = []
    idx = 0
    y_coords = np.linspace(y_min, y_max, n_probes_per_station)
    for x in x_stations:
        for y in y_coords:
            probes.append(
                SensorProbe(
                    probe_id=f"rake_x{x:.2f}_y{y:.3f}_{idx:03d}",
                    coords=(float(x), float(y)),
                    channels=("u", "v", "p"),
                    description=f"Station at x={x:.2f} m, y={y:.3f} m",
                )
            )
            idx += 1

    return SensorLayout(
        name=name,
        dim=2,
        probes=tuple(probes),
        layout_type="downstream_rake",
        metadata={
            "x_stations": list(x_stations),
            "y_min": float(y_min),
            "y_max": float(y_max),
            "n_probes_per_station": n_probes_per_station,
        },
    )


def make_downstream_rake_3d(
    x_stations: Sequence[float],
    y_min: float,
    y_max: float,
    z_stations: Sequence[float],
    n_probes_y: int = 9,
    name: str = "downstream_rake_3d",
) -> SensorLayout:
    """Creates a 3D cross-wake sensor rake grid at multiple downstream x and z spans."""
    probes: list[SensorProbe] = []
    idx = 0
    y_coords = np.linspace(y_min, y_max, n_probes_y)
    for x in x_stations:
        for z in z_stations:
            for y in y_coords:
                probes.append(
                    SensorProbe(
                        probe_id=f"p3d_x{x:.2f}_y{y:.2f}_z{z:.2f}_{idx:04d}",
                        coords=(float(x), float(y), float(z)),
                        channels=("u", "v", "w", "p"),
                        description=f"Probe at x={x:.2f}, y={y:.2f}, z={z:.2f}",
                    )
                )
                idx += 1

    return SensorLayout(
        name=name,
        dim=3,
        probes=tuple(probes),
        layout_type="downstream_rake_3d",
        metadata={
            "x_stations": list(x_stations),
            "z_stations": list(z_stations),
            "y_min": float(y_min),
            "y_max": float(y_max),
            "n_probes_y": n_probes_y,
        },
    )


def make_cylinder_surface_probes_2d(
    center_x: float,
    center_y: float,
    radius: float,
    n_sensors: int = 16,
    standoff_distance: float = 0.001,
    name: str = "cylinder_surface_probes_2d",
) -> SensorLayout:
    """Creates boundary-adjacent sensors around the cylinder circumference.

    standoff_distance places sensors slightly outside the solid boundary into the fluid domain.
    """
    probes: list[SensorProbe] = []
    r_probe = radius + standoff_distance
    angles = np.linspace(0, 2 * math.pi, n_sensors, endpoint=False)
    for i, theta in enumerate(angles):
        deg = math.degrees(theta)
        px = center_x + r_probe * math.cos(theta)
        py = center_y + r_probe * math.sin(theta)
        probes.append(
            SensorProbe(
                probe_id=f"surf_deg{deg:05.1f}_{i:02d}",
                coords=(float(px), float(py)),
                channels=("u", "v", "p"),
                description=f"Surface probe at theta={deg:.1f} deg",
            )
        )

    return SensorLayout(
        name=name,
        dim=2,
        probes=tuple(probes),
        layout_type="surface_pressure_ring",
        metadata={
            "center": [center_x, center_y],
            "radius": radius,
            "standoff": standoff_distance,
            "n_sensors": n_sensors,
        },
    )


def make_auv_patrol_path(
    start_coords: tuple[float, ...],
    end_coords: tuple[float, ...],
    speed_m_s: float,
    t_start: float,
    sample_dt: float = 0.2,
    name: str = "auv_patrol_trajectory",
) -> tuple[SensorLayout, np.ndarray]:
    """Generates a mobile AUV sensor trajectory across the wake.

    Returns (SensorLayout, timestamps).
    """
    p0 = np.array(start_coords, dtype=np.float64)
    p1 = np.array(end_coords, dtype=np.float64)
    dist = float(np.linalg.norm(p1 - p0))
    if speed_m_s <= 0 or dist == 0:
        raise ValueError("speed_m_s must be positive and start/end distinct")

    duration = dist / speed_m_s
    n_steps = max(2, int(math.ceil(duration / sample_dt)))
    t_samples = np.linspace(t_start, t_start + duration, n_steps)
    alphas = np.linspace(0.0, 1.0, n_steps)

    dim = len(start_coords)
    probes: list[SensorProbe] = []
    for i, (t, a) in enumerate(zip(t_samples, alphas, strict=True)):
        pt = p0 + a * (p1 - p0)
        probes.append(
            SensorProbe(
                probe_id=f"auv_t{t:.2f}_{i:04d}",
                coords=tuple(float(c) for c in pt),
                channels=("u", "v", "p") if dim == 2 else ("u", "v", "w", "p"),
                description=f"AUV position at t={t:.2f} s",
            )
        )

    layout = SensorLayout(
        name=name,
        dim=dim,  # type: ignore[arg-type]
        probes=tuple(probes),
        layout_type="mobile_auv_patrol",
        metadata={
            "start": list(start_coords),
            "end": list(end_coords),
            "speed_m_s": speed_m_s,
            "t_start": t_start,
            "duration": duration,
        },
    )
    return layout, t_samples


# ============================================================================
# Observation Operator Core
# ============================================================================


class ObservationOperator:
    """Extracts sparse sensory observations from Eulerian flow field grids."""

    def __init__(self, layout: SensorLayout) -> None:
        self.layout = layout

    def sample_2d_field(
        self,
        grid_data: np.ndarray,
        x_coords: np.ndarray,
        y_coords: np.ndarray,
        channel_names: Sequence[str] = ("u", "v", "p"),
    ) -> np.ndarray:
        """Samples the 2D field at all probe locations.

        Args:
            grid_data: Shape (..., C, Ny, Nx) where C == len(channel_names).
            x_coords: 1D array of length Nx (monotonic).
            y_coords: 1D array of length Ny (monotonic).
            channel_names: Channel ordering corresponding to C.

        Returns:
            sampled: Shape (..., num_probes, C) with interpolated values.
        """
        if self.layout.dim != 2:
            raise ValueError(f"Layout dim {self.layout.dim} does not match 2D grid")

        coords = self.layout.probe_coordinates()  # (N_p, 2)
        n_probes = len(coords)
        lead_shape = grid_data.shape[:-3]
        n_channels = grid_data.shape[-3]

        flat_leads = 1
        for s in lead_shape:
            flat_leads *= s
        reshaped = grid_data.reshape((flat_leads, n_channels, len(y_coords), len(x_coords)))

        # Output shape: (flat_leads, n_probes, n_channels)
        out = np.zeros((flat_leads, n_probes, n_channels), dtype=np.float64)

        # Scipy RegularGridInterpolator expects points as (y, x) matching matrix axis 0, 1
        interp_points = np.column_stack([coords[:, 1], coords[:, 0]])

        for l_idx in range(flat_leads):
            for c_idx in range(n_channels):
                field_slice = reshaped[l_idx, c_idx]
                rgi = RegularGridInterpolator(
                    (y_coords, x_coords),
                    field_slice,
                    bounds_error=False,
                    fill_value=np.nan,
                )
                out[l_idx, :, c_idx] = rgi(interp_points)

        final_shape = lead_shape + (n_probes, n_channels)
        return out.reshape(final_shape)

    def sample_3d_field(
        self,
        grid_data: np.ndarray,
        x_coords: np.ndarray,
        y_coords: np.ndarray,
        z_coords: np.ndarray,
        channel_names: Sequence[str] = ("u", "v", "w", "p"),
    ) -> np.ndarray:
        """Samples the 3D field at all probe locations.

        Args:
            grid_data: Shape (..., C, Nz, Ny, Nx).
            x_coords: 1D array of length Nx.
            y_coords: 1D array of length Ny.
            z_coords: 1D array of length Nz.

        Returns:
            sampled: Shape (..., num_probes, C).
        """
        if self.layout.dim != 3:
            raise ValueError(f"Layout dim {self.layout.dim} does not match 3D grid")

        coords = self.layout.probe_coordinates()  # (N_p, 3) (x, y, z)
        n_probes = len(coords)
        lead_shape = grid_data.shape[:-4]
        n_channels = grid_data.shape[-4]

        flat_leads = 1
        for s in lead_shape:
            flat_leads *= s
        reshaped = grid_data.reshape((flat_leads, n_channels, len(z_coords), len(y_coords), len(x_coords)))

        out = np.zeros((flat_leads, n_probes, n_channels), dtype=np.float64)
        # Interpolator points order: (z, y, x)
        interp_points = np.column_stack([coords[:, 2], coords[:, 1], coords[:, 0]])

        for l_idx in range(flat_leads):
            for c_idx in range(n_channels):
                field_slice = reshaped[l_idx, c_idx]
                rgi = RegularGridInterpolator(
                    (z_coords, y_coords, x_coords),
                    field_slice,
                    bounds_error=False,
                    fill_value=np.nan,
                )
                out[l_idx, :, c_idx] = rgi(interp_points)

        final_shape = lead_shape + (n_probes, n_channels)
        return out.reshape(final_shape)

    @staticmethod
    def add_sensor_noise(
        observations: np.ndarray,
        snr_db: float | None = None,
        relative_sigma: float | None = None,
        drift_bias: float = 0.0,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """Applies realistic underwater measurement noise to probe observations.

        Supports either SNR (in dB) or relative Gaussian noise sigma * channel_std.
        """
        if rng is None:
            rng = np.random.default_rng(42)

        noisy = observations.copy()
        if snr_db is not None:
            signal_power = np.nanmean(observations**2, axis=-2, keepdims=True)
            snr_linear = 10.0 ** (snr_db / 10.0)
            noise_power = np.maximum(signal_power / snr_linear, 1e-12)
            noise = rng.normal(0.0, np.sqrt(noise_power), size=observations.shape)
            noisy += noise
        elif relative_sigma is not None:
            channel_std = np.nanstd(observations, axis=-2, keepdims=True)
            scale = np.maximum(relative_sigma * channel_std, 1e-6)
            noise = rng.normal(0.0, scale, size=observations.shape)
            noisy += noise

        if drift_bias != 0.0:
            noisy += drift_bias

        return noisy
