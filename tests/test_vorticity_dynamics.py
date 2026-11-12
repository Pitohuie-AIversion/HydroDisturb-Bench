"""Unit tests for vorticity dynamics, Q-criterion, enstrophy, and incompressibility diagnostics."""

import numpy as np

from hydro_disturb_bench.vorticity_dynamics import (
    compute_divergence_2d,
    compute_divergence_3d,
    compute_enstrophy,
    compute_q_criterion_2d,
    compute_vorticity_2d,
    compute_vorticity_3d,
    evaluate_incompressibility_metrics,
)


def test_taylor_green_vortex_2d_analytical():
    """Verify 2D vorticity and divergence against exact Taylor-Green vortex solution."""
    Nx, Ny = 64, 64
    x = np.linspace(0, 2 * np.pi, Nx)
    y = np.linspace(0, 2 * np.pi, Ny)
    X, Y = np.meshgrid(x, y)  # shapes (Ny, Nx)

    # Analytical Taylor-Green flow
    u = -np.cos(X) * np.sin(Y)
    v = np.sin(X) * np.cos(Y)

    # Divergence should be identically zero: div = du/dx + dv/dy = sin(X)sin(Y) - sin(X)sin(Y) = 0
    div = compute_divergence_2d(u, v, x, y)
    assert np.max(np.abs(div)) < 0.05  # Discrete finite difference error on 64x64

    # Vorticity: omega_z = dv/dx - du/dy = cos(X)cos(Y) - (-cos(X)cos(Y)) = 2 cos(X) cos(Y)
    omega_analytical = 2.0 * np.cos(X) * np.cos(Y)
    omega_computed = compute_vorticity_2d(u, v, x, y)

    rel_l2 = np.linalg.norm(omega_computed - omega_analytical) / np.linalg.norm(omega_analytical)
    assert rel_l2 < 0.01  # Less than 1% relative error for 64 grid points


def test_solid_mask_handling_2d():
    """Verify that solid mask properly isolates obstacle interior with NaNs."""
    Nx, Ny = 40, 40
    x = np.linspace(-1, 1, Nx)
    y = np.linspace(-1, 1, Ny)
    X, Y = np.meshgrid(x, y)

    u = -Y
    v = X

    # Circular mask at origin with radius 0.3
    mask = (X**2 + Y**2) <= (0.3**2)

    vort = compute_vorticity_2d(u, v, x, y, solid_mask=mask)
    div = compute_divergence_2d(u, v, x, y, solid_mask=mask)
    q = compute_q_criterion_2d(u, v, x, y, solid_mask=mask)

    assert np.all(np.isnan(vort[mask]))
    assert np.all(np.isfinite(vort[~mask]))
    assert np.all(np.isnan(div[mask]))
    assert np.all(np.isfinite(div[~mask]))
    assert np.all(np.isnan(q[mask]))
    assert np.all(np.isfinite(q[~mask]))


def test_q_criterion_2d_vortex_vs_shear():
    """Verify that Q > 0 for solid body rotation and Q < 0 for pure strain flow."""
    x = np.linspace(-1, 1, 30)
    y = np.linspace(-1, 1, 30)
    X, Y = np.meshgrid(x, y)

    # 1. Pure rotation: u = -omega * y, v = omega * x
    # Rotation dominates strain, Q should be positive everywhere
    u_rot = -2.0 * Y
    v_rot = 2.0 * X
    q_rot = compute_q_criterion_2d(u_rot, v_rot, x, y)
    assert np.all(q_rot > 0.0)

    # 2. Pure strain / saddle flow: u = a * x, v = -a * y
    # Strain dominates rotation, Q should be negative everywhere
    u_strain = 2.0 * X
    v_strain = -2.0 * Y
    q_strain = compute_q_criterion_2d(u_strain, v_strain, x, y)
    assert np.all(q_strain < 0.0)


def test_vorticity_and_divergence_3d():
    """Verify 3D vorticity, divergence, Q-criterion, and enstrophy."""
    Nx, Ny, Nz = 20, 20, 20
    x = np.linspace(0, 1, Nx)
    y = np.linspace(0, 1, Ny)
    z = np.linspace(0, 1, Nz)

    Z, Y, X = np.meshgrid(z, y, x, indexing="ij")  # (Nz, Ny, Nx)

    # Incompressible 3D linear field: u = y, v = z, w = x
    # div(u) = du/dx + dv/dy + dw/dz = 0 + 0 + 0 = 0
    u = Y
    v = Z
    w = X

    div = compute_divergence_3d(u, v, w, x, y, z)
    assert np.max(np.abs(div)) < 1e-12

    # omega = curl(u):
    # omega_x = dw/dy - dv/dz = 0 - 1 = -1
    # omega_y = du/dz - dw/dx = 0 - 1 = -1
    # omega_z = dv/dx - du/dy = 0 - 1 = -1
    wx, wy, wz = compute_vorticity_3d(u, v, w, x, y, z)
    np.testing.assert_allclose(wx, -1.0, atol=1e-10)
    np.testing.assert_allclose(wy, -1.0, atol=1e-10)
    np.testing.assert_allclose(wz, -1.0, atol=1e-10)

    # Enstrophy: E = 0.5 * ((-1)^2 + (-1)^2 + (-1)^2) = 1.5
    enstrophy = compute_enstrophy((wx, wy, wz))
    np.testing.assert_allclose(enstrophy, 1.5, atol=1e-10)

    # Q-criterion: ||Omega||^2 = 1.5, ||S||^2 = 1.5 => Q = 0.0
    from hydro_disturb_bench.vorticity_dynamics import compute_q_criterion_3d

    q3d = compute_q_criterion_3d(u, v, w, x, y, z)
    np.testing.assert_allclose(q3d, 0.0, atol=1e-10)


def test_incompressibility_metrics():
    """Verify evaluation of mass conservation continuity residual."""
    Nx, Ny = 30, 30
    x = np.linspace(0, 1, Nx)
    y = np.linspace(0, 1, Ny)
    X, Y = np.meshgrid(x, y)

    # Perfect incompressible flow
    u = -Y
    v = X
    div_zero = compute_divergence_2d(u, v, x, y)
    metrics_zero = evaluate_incompressibility_metrics(
        div_zero, characteristic_velocity=0.50, characteristic_length=0.30
    )
    assert metrics_zero["max_abs_div"] < 1e-10
    assert metrics_zero["normalized_rms_div"] < 1e-10

    # Flow with deliberate artificial non-zero divergence
    u_comp = 2.0 * X
    v_comp = 3.0 * Y
    div_non_zero = compute_divergence_2d(u_comp, v_comp, x, y)
    metrics_nz = evaluate_incompressibility_metrics(
        div_non_zero, characteristic_velocity=0.50, characteristic_length=0.30
    )
    # div = 2 + 3 = 5.0
    np.testing.assert_allclose(metrics_nz["max_abs_div"], 5.0, atol=1e-5)
    assert metrics_nz["normalized_rms_div"] > 0.0
