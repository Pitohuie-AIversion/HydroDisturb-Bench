"""Unit tests for C04 3D Hydrodynamic Disturbance Injection Protocol."""

import numpy as np
import pytest

from hydro_disturb_bench.c04_disturbance import (
    C04DisturbanceConfig,
    evaluate_c04_disturbance_field,
    generate_c04_comsol_force_expressions,
)
from hydro_disturb_bench.comsol_java.errors import InvalidParameterError
from hydro_disturb_bench.sampling_3d import create_c04_sampling_grid


def test_c04_disturbance_config_valid():
    """Verify a valid 3D disturbance configuration passes admission."""
    # Cylinder radius is 0.15 m. Wake center at x = 0.6 m, y = 0.0 m, z = 1.5 m (5D)
    # sigma = 0.05 m -> 3*sigma = 0.15 m. Distance to cylinder center (0,0) is 0.6 m > 0.15 + 0.15 = 0.3 m
    cfg = C04DisturbanceConfig(
        center_m=(0.60, 0.0, 1.50),
        direction=(0.0, 1.0, 0.0),  # Pure crossflow y-direction
        acceleration_m_s2=0.05,
        sigma_m=0.05,
        on_s=1.0,
        off_s=1.5,
    )
    assert cfg.acceleration_m_s2 == 0.05
    assert cfg.on_s == 1.0
    assert cfg.off_s == 1.5


def test_c04_disturbance_config_rejects_cylinder_collision():
    """Verify disturbance center too close to cylinder is strictly rejected."""
    # Cylinder radius 0.15 m. Place center at x = 0.20 m with sigma = 0.05 m (3*sigma = 0.15 m)
    # Required distance > 0.15 + 0.15 = 0.30 m. 0.20 m < 0.30 m -> collision!
    with pytest.raises(InvalidParameterError, match="intersect or touch the cylinder"):
        C04DisturbanceConfig(
            center_m=(0.20, 0.0, 1.50),
            direction=(0.0, 1.0, 0.0),
            acceleration_m_s2=0.05,
            sigma_m=0.05,
            on_s=1.0,
            off_s=1.5,
        )


def test_c04_disturbance_config_rejects_out_of_bounds():
    """Verify disturbance with 3-sigma sphere outside CFD boundary is rejected."""
    # Streamwise max is 6.0 m. Center at 5.95 m with sigma = 0.05 m (3*sigma = 0.15 m -> reaches 6.10 m > 6.0 m)
    with pytest.raises(InvalidParameterError, match="streamwise bounds"):
        C04DisturbanceConfig(
            center_m=(5.95, 0.0, 1.50),
            direction=(0.0, 1.0, 0.0),
            acceleration_m_s2=0.05,
            sigma_m=0.05,
            on_s=1.0,
            off_s=1.5,
        )


def test_c04_disturbance_comsol_expressions():
    """Verify generated COMSOL force expressions have proper syntax and units."""
    cfg = C04DisturbanceConfig(
        center_m=(0.60, 0.0, 1.50),
        direction=(0.0, 1.0, 0.0),
        acceleration_m_s2=0.05,
        sigma_m=0.05,
        on_s=1.0,
        off_s=1.5,
    )
    exprs = generate_c04_comsol_force_expressions(cfg)
    assert "Fx" in exprs and "Fy" in exprs and "Fz" in exprs
    assert "1025.8944[kg/m^3]" in exprs["Fy"]
    assert "0.050000[m/s^2]" in exprs["Fy"]
    assert "0.600000[m]" in exprs["Fy"]
    assert "1.0000[s]" in exprs["Fy"]
    assert "1.5000[s]" in exprs["Fy"]


def test_c04_disturbance_field_evaluation():
    """Verify analytical field evaluation inside and outside pulse active window."""
    cfg = C04DisturbanceConfig(
        center_m=(0.60, 0.0, 1.50),
        direction=(0.0, 1.0, 0.0),  # y direction
        acceleration_m_s2=0.10,
        sigma_m=0.05,
        on_s=1.0,
        off_s=2.0,
    )
    grid = create_c04_sampling_grid(D=0.30)

    # 1. Outside pulse window: exactly zero
    field_before = evaluate_c04_disturbance_field(cfg, grid, time_s=0.5)
    assert np.allclose(field_before, 0.0)

    field_after = evaluate_c04_disturbance_field(cfg, grid, time_s=2.5)
    assert np.allclose(field_after, 0.0)

    # 2. At peak time t = 1.5 s: sin^2(pi * 0.5 / 1.0) = sin^2(pi/2) = 1.0
    field_peak = evaluate_c04_disturbance_field(cfg, grid, time_s=1.5)
    assert field_peak.shape == (3, 64, 96, 192)
    # x and z acceleration components are zero
    assert np.allclose(field_peak[0], 0.0)
    assert np.allclose(field_peak[2], 0.0)

    # Max acceleration occurs near the center (0.6, 0.0, 1.5)
    max_ay = float(np.max(field_peak[1]))
    assert 0.08 < max_ay <= 0.10  # Discrete cell center close to analytical center
