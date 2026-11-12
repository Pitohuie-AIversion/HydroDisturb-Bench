"""3D Hydrodynamic disturbance injection protocol for C04 high-Re benchmark.

Defines localized 3D Gaussian body acceleration perturbation contracts,
strict geometric admission checks, COMSOL VolumeForce expression generation,
and analytical disturbance field evaluations.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from typing import Any

import numpy as np

from .comsol_java.errors import InvalidParameterError
from .sampling_3d import C04GridCoordinates, C04SliceCoordinates

# Physical domain limits for C04 CFD computation (D = 0.30 m)
# x in [-3.0, 6.0], y in [-3.0, 3.0], z in [0.0, 3.0]
C04_CFD_BOUNDS_M = (-3.0, 6.0, -3.0, 3.0, 0.0, 3.0)
C04_CYLINDER_RADIUS_M = 0.15  # D / 2
C04_DENSITY_KG_M3 = 1025.8944  # ITTC 2024 seawater density


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidParameterError(message)


def _number(value: Any, label: str) -> Decimal:
    _require(
        type(value) in (int, float),
        f"{label} must be a real JSON number, not bool or an expression",
    )
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, f"{label} must be finite")
    return Decimal(str(value))


@dataclass(frozen=True)
class C04DisturbanceConfig:
    """Configuration for a localized 3D Gaussian body acceleration disturbance."""

    center_m: tuple[float, float, float]  # (xc, yc, zc) in meters
    direction: tuple[float, float, float]  # Unit vector (ex, ey, ez)
    acceleration_m_s2: float  # Peak acceleration amplitude A
    sigma_m: float  # Gaussian spatial width sigma
    on_s: float  # Start time of pulse in seconds
    off_s: float  # Stop time of pulse in seconds
    velocity_m_s: tuple[float, float, float] = (0.0, 0.0, 0.0)  # Source translation velocity
    mechanism: str = "gaussian_body_acceleration"
    event_id: str = "c04_pulse_01"

    def center_at_time(self, time_s: float) -> tuple[float, float, float]:
        """Returns the instantaneous spatial center (xc, yc, zc) at physical time t."""
        if self.velocity_m_s == (0.0, 0.0, 0.0) or time_s <= self.on_s:
            return self.center_m
        dt = min(float(time_s), self.off_s) - self.on_s
        xc0, yc0, zc0 = self.center_m
        vx, vy, vz = self.velocity_m_s
        return (xc0 + vx * dt, yc0 + vy * dt, zc0 + vz * dt)

    def __post_init__(self) -> None:
        _require(
            len(self.center_m) == 3, "center_m must be a 3-element tuple (xc, yc, zc)"
        )
        _require(
            len(self.direction) == 3, "direction must be a 3-element tuple (ex, ey, ez)"
        )
        _require(
            len(self.velocity_m_s) == 3, "velocity_m_s must be a 3-element tuple (vx, vy, vz)"
        )

        xc, yc, zc = (_number(v, f"center_m[{i}]") for i, v in enumerate(self.center_m))
        ex, ey, ez = (_number(v, f"direction[{i}]") for i, v in enumerate(self.direction))
        vx, vy, vz = (_number(v, f"velocity_m_s[{i}]") for i, v in enumerate(self.velocity_m_s))
        amplitude = _number(self.acceleration_m_s2, "acceleration_m_s2")
        sigma = _number(self.sigma_m, "sigma_m")
        t_on = _number(self.on_s, "on_s")
        t_off = _number(self.off_s, "off_s")

        _require(amplitude >= 0, "acceleration_m_s2 must be >= 0")
        _require(
            amplitude <= Decimal("1.0"),
            "acceleration_m_s2 must be <= 1.0 m/s^2 for physical stability",
        )
        _require(
            Decimal("0.02") <= sigma <= Decimal("0.20"),
            "sigma_m must be in [0.02, 0.20] m",
        )
        _require(t_on < t_off, "Pulse timing must satisfy on_s < off_s")

        dir_norm_sq = float(ex**2 + ey**2 + ez**2)
        _require(
            dir_norm_sq > 1e-6,
            "direction vector must be non-zero",
        )

        with localcontext() as context:
            context.prec = 60
            radius = 3 * sigma  # 3-sigma support sphere
            duration = t_off - t_on

            # Validate endpoints of trajectory
            for alpha in (Decimal("0"), Decimal("1")):
                cur_xc = xc + vx * duration * alpha
                cur_yc = yc + vy * duration * alpha
                cur_zc = zc + vz * duration * alpha

                # Geometric containment check: 3-sigma sphere inside CFD domain
                _require(
                    Decimal("-3.0") + radius <= cur_xc <= Decimal("6.0") - radius,
                    "The complete 3-sigma sphere must lie within CFD streamwise bounds [-3.0, 6.0] m",
                )
                _require(
                    Decimal("-3.0") + radius <= cur_yc <= Decimal("3.0") - radius,
                    "The complete 3-sigma sphere must lie within CFD crossflow bounds [-3.0, 3.0] m",
                )
                _require(
                    Decimal("0.0") + radius <= cur_zc <= Decimal("3.0") - radius,
                    "The complete 3-sigma sphere must lie within CFD spanwise bounds [0.0, 3.0] m",
                )

                # Obstacle clearance check: cylinder is along z-axis at (x, y) = (0, 0) with radius 0.15 m
                dist_xy_sq = cur_xc**2 + cur_yc**2
                min_dist = radius + Decimal(str(C04_CYLINDER_RADIUS_M))
                _require(
                    dist_xy_sq >= min_dist**2,
                    "The 3-sigma sphere must not intersect or touch the cylinder obstacle",
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_c04_comsol_force_expressions(config: C04DisturbanceConfig) -> dict[str, str]:
    """Generate exact COMSOL VolumeForce formula expressions for Fx, Fy, Fz.

    Formulas use COMSOL syntax, supporting both stationary and moving Gaussian pulse sources.
    """
    xc0, yc0, zc0 = config.center_m
    vx, vy, vz = config.velocity_m_s
    norm = math.sqrt(sum(d**2 for d in config.direction))
    ex, ey, ez = [d / norm for d in config.direction]

    t_rel = f"(t-{config.on_s:.4f}[s])"
    xc_expr = f"({xc0:.6f}[m]+{vx:.6f}[m/s]*{t_rel})" if vx != 0.0 else f"{xc0:.6f}[m]"
    yc_expr = f"({yc0:.6f}[m]+{vy:.6f}[m/s]*{t_rel})" if vy != 0.0 else f"{yc0:.6f}[m]"
    zc_expr = f"({zc0:.6f}[m]+{vz:.6f}[m/s]*{t_rel})" if vz != 0.0 else f"{zc0:.6f}[m]"

    spatial_gauss = (
        f"exp(-((x-{xc_expr})^2+(y-{yc_expr})^2+(z-{zc_expr})^2)"
        f"/(2*{config.sigma_m:.6f}[m]^2))"
    )
    time_env = (
        f"if((t>{config.on_s:.4f}[s])&&(t<{config.off_s:.4f}[s]),"
        f"sin(pi*(t-{config.on_s:.4f}[s])/({config.off_s - config.on_s:.4f}[s]))^2,0)"
    )

    base_f = f"{C04_DENSITY_KG_M3:.4f}[kg/m^3]*{config.acceleration_m_s2:.6f}[m/s^2]*{spatial_gauss}*{time_env}"

    return {
        "Fx": f"{ex:.6f}*({base_f})",
        "Fy": f"{ey:.6f}*({base_f})",
        "Fz": f"{ez:.6f}*({base_f})",
    }


def generate_c04_comsol_extruded_xz_force_expressions(
    config: C04DisturbanceConfig,
    *,
    span_offset_m: float = 3.0,
) -> dict[str, str]:
    """Generate COMSOL VolumeForce expressions mapped to the extruded-xz 3D C04 model axes.

    In the 3D C04 COMSOL geometry (extruded from the xz workplane along -y):
    - X_canonical = x_comsol
    - Y_canonical (crossflow) = z_comsol
    - Z_canonical (spanwise [0, 3] m) = y_comsol + span_offset_m (y_comsol in [-3, 0] m)
    Therefore, (xc, yc, zc) maps to COMSOL (xc, zc - span_offset_m, yc) and
    direction (ex, ey, ez) maps to COMSOL VolumeForce (Fx=ex, Fy=ez, Fz=ey).
    """
    xc, yc, zc = config.center_m
    y_comsol = zc - span_offset_m
    z_comsol = yc

    norm = math.sqrt(sum(d**2 for d in config.direction))
    ex, ey, ez = [d / norm for d in config.direction]

    spatial_gauss = (
        f"exp(-((x-{xc:.6f}[m])^2+(y-({y_comsol:.6f}[m]))^2+(z-({z_comsol:.6f}[m]))^2)"
        f"/(2*{config.sigma_m:.6f}[m]^2))"
    )
    time_env = (
        f"if((t>{config.on_s:.4f}[s])&&(t<{config.off_s:.4f}[s]),"
        f"sin(pi*(t-{config.on_s:.4f}[s])/({config.off_s - config.on_s:.4f}[s]))^2,0)"
    )

    base_f = f"{C04_DENSITY_KG_M3:.4f}[kg/m^3]*{config.acceleration_m_s2:.6f}[m/s^2]*{spatial_gauss}*{time_env}"

    return {
        "Fx": f"{ex:.6f}*({base_f})",
        "Fy": f"{ez:.6f}*({base_f})",
        "Fz": f"{ey:.6f}*({base_f})",
    }


def evaluate_c04_disturbance_field(
    config: C04DisturbanceConfig,
    grid: C04GridCoordinates | C04SliceCoordinates,
    time_s: float,
) -> np.ndarray:
    """Analytically evaluate the 3D or 2D disturbance acceleration field (m/s^2).

    Parameters
    ----------
    config : C04DisturbanceConfig
    grid : C04GridCoordinates or C04SliceCoordinates
    time_s : float
        Physical time in seconds.

    Returns
    -------
    np.ndarray
        Array shaped (3, ...) containing (ax, ay, az) acceleration components in m/s^2.
    """
    t = float(time_s)
    if t <= config.on_s or t >= config.off_s:
        # Outside pulse active window, acceleration is identically zero
        return np.zeros((3, *grid.shape), dtype=np.float32)

    # Time envelope
    t_pulse = config.off_s - config.on_s
    t_factor = float(math.sin(math.pi * (t - config.on_s) / t_pulse) ** 2)

    xc, yc, zc = config.center_at_time(t)
    sigma = config.sigma_m

    norm = math.sqrt(sum(d**2 for d in config.direction))
    ex, ey, ez = (d / norm for d in config.direction)

    if isinstance(grid, C04GridCoordinates):
        dist_sq = (grid.X - xc) ** 2 + (grid.Y - yc) ** 2 + (grid.Z - zc) ** 2
    else:
        dist_sq = (grid.X - xc) ** 2 + (grid.Y - yc) ** 2 + (grid.z_fixed - zc) ** 2

    spatial_factor = np.exp(-dist_sq / (2.0 * sigma**2), dtype=np.float32)
    accel_mag = (config.acceleration_m_s2 * t_factor * spatial_factor).astype(np.float32)

    ax = (ex * accel_mag).astype(np.float32)
    ay = (ey * accel_mag).astype(np.float32)
    az = (ez * accel_mag).astype(np.float32)

    return np.stack([ax, ay, az], axis=0)
