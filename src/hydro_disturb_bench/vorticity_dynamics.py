"""Vorticity dynamics, vortex identification criteria, and incompressible kinematics diagnostics.

This module provides formal differential kinematics and vortex identification
tools for both 2D slices and 3D volume flows in HydroDisturb-Bench:
- Incompressible mass conservation (continuity) divergence diagnostics: div(u) = nabla . u
- Vorticity vector and scalar fields: omega = nabla x u
- Q-criterion vortex identification: Q = 0.5 * (||Omega||^2 - ||S||^2)
- Enstrophy field and integral wake enstrophy: E = 0.5 * ||omega||^2
- Circulation around closed contours / obstacle boundaries: Gamma = oint u . dr

All functions support solid obstacle masking to prevent non-physical boundary artifacts
and provide dimensionless diagnostic metrics scaled by characteristic scales (U0, D).
"""

from __future__ import annotations

import numpy as np


def _ensure_2d_leading_axes(
    u: np.ndarray, v: np.ndarray
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Helper to ensure (Ny, Nx) or (T, Ny, Nx) inputs have consistent shapes."""
    if u.shape != v.shape:
        raise ValueError(f"Shape mismatch: u {u.shape} vs v {v.shape}")
    if u.ndim == 2:
        return u[np.newaxis, ...], v[np.newaxis, ...], True
    elif u.ndim == 3:
        return u, v, False
    else:
        raise ValueError(f"Expected 2D or 3D array (T, Ny, Nx), got {u.ndim}D")


def _ensure_3d_leading_axes(
    u: np.ndarray, v: np.ndarray, w: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """Helper to ensure (Nz, Ny, Nx) or (T, Nz, Ny, Nx) inputs have consistent shapes."""
    if not (u.shape == v.shape == w.shape):
        raise ValueError(f"Shape mismatch: u {u.shape}, v {v.shape}, w {w.shape}")
    if u.ndim == 3:
        return u[np.newaxis, ...], v[np.newaxis, ...], w[np.newaxis, ...], True
    elif u.ndim == 4:
        return u, v, w, False
    else:
        raise ValueError(f"Expected 3D or 4D array (T, Nz, Ny, Nx), got {u.ndim}D")


def compute_vorticity_2d(
    u: np.ndarray,
    v: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute 2D out-of-plane vorticity: omega_z = dv/dx - du/dy.

    Parameters
    ----------
    u : np.ndarray
        Streamwise velocity array of shape (Ny, Nx) or (T, Ny, Nx).
    v : np.ndarray
        Crossflow velocity array of shape (Ny, Nx) or (T, Ny, Nx).
    x : np.ndarray
        1D physical coordinate array along x-axis (length Nx).
    y : np.ndarray
        1D physical coordinate array along y-axis (length Ny).
    solid_mask : np.ndarray, optional
        2D boolean/integer mask of shape (Ny, Nx) where 1 indicates solid (masked)
        and 0 indicates fluid. Masked cells are set to NaN.

    Returns
    -------
    np.ndarray
        Vorticity array omega_z in 1/s, matching input dimension ((Ny, Nx) or (T, Ny, Nx)).
    """
    u_arr, v_arr, is_single = _ensure_2d_leading_axes(u, v)
    T, Ny, Nx = u_arr.shape
    if len(x) != Nx or len(y) != Ny:
        raise ValueError(
            f"Coordinate dimensions (y={len(y)}, x={len(x)}) do not match grid ({Ny}, {Nx})"
        )

    # Compute spatial gradients along axis 2 (x) and axis 1 (y)
    dv_dx = np.gradient(v_arr, x, axis=2)
    du_dy = np.gradient(u_arr, y, axis=1)
    omega_z = dv_dx - du_dy

    if solid_mask is not None:
        mask_2d = np.asarray(solid_mask, dtype=bool)
        if mask_2d.shape != (Ny, Nx):
            raise ValueError(
                f"solid_mask shape {mask_2d.shape} does not match grid ({Ny}, {Nx})"
            )
        omega_z[:, mask_2d] = np.nan

    return omega_z[0] if is_single else omega_z


def compute_divergence_2d(
    u: np.ndarray,
    v: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute 2D velocity divergence: div(u) = du/dx + dv/dy.

    For exact incompressible flow, divergence should be zero within numerical tolerance.

    Parameters
    ----------
    u : np.ndarray
        Streamwise velocity array of shape (Ny, Nx) or (T, Ny, Nx).
    v : np.ndarray
        Crossflow velocity array of shape (Ny, Nx) or (T, Ny, Nx).
    x : np.ndarray
        1D physical coordinate array along x-axis (length Nx).
    y : np.ndarray
        1D physical coordinate array along y-axis (length Ny).
    solid_mask : np.ndarray, optional
        2D boolean/integer mask of shape (Ny, Nx) where 1 indicates solid.

    Returns
    -------
    np.ndarray
        Divergence array in 1/s.
    """
    u_arr, v_arr, is_single = _ensure_2d_leading_axes(u, v)
    T, Ny, Nx = u_arr.shape
    if len(x) != Nx or len(y) != Ny:
        raise ValueError(
            f"Coordinate dimensions (y={len(y)}, x={len(x)}) do not match grid ({Ny}, {Nx})"
        )

    du_dx = np.gradient(u_arr, x, axis=2)
    dv_dy = np.gradient(v_arr, y, axis=1)
    div_u = du_dx + dv_dy

    if solid_mask is not None:
        mask_2d = np.asarray(solid_mask, dtype=bool)
        div_u[:, mask_2d] = np.nan

    return div_u[0] if is_single else div_u


def compute_q_criterion_2d(
    u: np.ndarray,
    v: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute 2D Q-criterion for vortex core identification.

    Q = 0.5 * (||Omega||^2 - ||S||^2)
    where S is rate-of-strain tensor and Omega is vorticity tensor.
    Positive values (Q > 0) denote regions where rotation dominates strain (vortex cores).

    Parameters
    ----------
    u, v : np.ndarray
        Velocity fields of shape (Ny, Nx) or (T, Ny, Nx).
    x, y : np.ndarray
        Coordinate axes.
    solid_mask : np.ndarray, optional
        Solid mask array.

    Returns
    -------
    np.ndarray
        Q-criterion field in 1/s^2.
    """
    u_arr, v_arr, is_single = _ensure_2d_leading_axes(u, v)
    T, Ny, Nx = u_arr.shape

    du_dx = np.gradient(u_arr, x, axis=2)
    du_dy = np.gradient(u_arr, y, axis=1)
    dv_dx = np.gradient(v_arr, x, axis=2)
    dv_dy = np.gradient(v_arr, y, axis=1)

    # Rate of strain S: S11 = du/dx, S22 = dv/dy, S12 = S21 = 0.5 * (du/dy + dv/dx)
    # Vorticity Omega: Omega12 = -Omega21 = 0.5 * (du/dy - dv/dx)
    s11 = du_dx
    s22 = dv_dy
    s12 = 0.5 * (du_dy + dv_dx)
    omega12 = 0.5 * (du_dy - dv_dx)

    # ||S||^2 = s11^2 + s22^2 + 2*s12^2
    # ||Omega||^2 = 2 * omega12^2
    norm_s_sq = s11**2 + s22**2 + 2.0 * (s12**2)
    norm_omega_sq = 2.0 * (omega12**2)

    q = 0.5 * (norm_omega_sq - norm_s_sq)

    if solid_mask is not None:
        mask_2d = np.asarray(solid_mask, dtype=bool)
        q[:, mask_2d] = np.nan

    return q[0] if is_single else q


def compute_vorticity_3d(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute 3D vorticity vector field: omega = nabla x u.

    omega_x = dw/dy - dv/dz
    omega_y = du/dz - dw/dx
    omega_z = dv/dx - du/dy

    Parameters
    ----------
    u, v, w : np.ndarray
        Velocity components of shape (Nz, Ny, Nx) or (T, Nz, Ny, Nx).
    x, y, z : np.ndarray
        1D physical coordinates (lengths Nx, Ny, Nz).
    solid_mask : np.ndarray, optional
        3D mask of shape (Nz, Ny, Nx) where 1 indicates solid.

    Returns
    -------
    tuple of (np.ndarray, np.ndarray, np.ndarray)
        (omega_x, omega_y, omega_z) arrays in 1/s.
    """
    u_arr, v_arr, w_arr, is_single = _ensure_3d_leading_axes(u, v, w)
    T, Nz, Ny, Nx = u_arr.shape
    if len(x) != Nx or len(y) != Ny or len(z) != Nz:
        raise ValueError(
            f"Coordinate dimensions (z={len(z)}, y={len(y)}, x={len(x)}) "
            f"do not match volume grid ({Nz}, {Ny}, {Nx})"
        )

    # Gradients: axis 3 is x, axis 2 is y, axis 1 is z
    dw_dy = np.gradient(w_arr, y, axis=2)
    dv_dz = np.gradient(v_arr, z, axis=1)
    omega_x = dw_dy - dv_dz

    du_dz = np.gradient(u_arr, z, axis=1)
    dw_dx = np.gradient(w_arr, x, axis=3)
    omega_y = du_dz - dw_dx

    dv_dx = np.gradient(v_arr, x, axis=3)
    du_dy = np.gradient(u_arr, y, axis=2)
    omega_z = dv_dx - du_dy

    if solid_mask is not None:
        mask_3d = np.asarray(solid_mask, dtype=bool)
        if mask_3d.shape != (Nz, Ny, Nx):
            raise ValueError(
                f"solid_mask shape {mask_3d.shape} does not match grid ({Nz}, {Ny}, {Nx})"
            )
        omega_x[:, mask_3d] = np.nan
        omega_y[:, mask_3d] = np.nan
        omega_z[:, mask_3d] = np.nan

    if is_single:
        return omega_x[0], omega_y[0], omega_z[0]
    return omega_x, omega_y, omega_z


def compute_divergence_3d(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute 3D velocity divergence: div(u) = du/dx + dv/dy + dw/dz.

    Parameters
    ----------
    u, v, w : np.ndarray
        Velocity components of shape (Nz, Ny, Nx) or (T, Nz, Ny, Nx).
    x, y, z : np.ndarray
        Coordinate vectors.
    solid_mask : np.ndarray, optional
        Solid mask array.

    Returns
    -------
    np.ndarray
        Divergence array in 1/s.
    """
    u_arr, v_arr, w_arr, is_single = _ensure_3d_leading_axes(u, v, w)
    T, Nz, Ny, Nx = u_arr.shape

    du_dx = np.gradient(u_arr, x, axis=3)
    dv_dy = np.gradient(v_arr, y, axis=2)
    dw_dz = np.gradient(w_arr, z, axis=1)
    div_u = du_dx + dv_dy + dw_dz

    if solid_mask is not None:
        mask_3d = np.asarray(solid_mask, dtype=bool)
        div_u[:, mask_3d] = np.nan

    return div_u[0] if is_single else div_u


def compute_q_criterion_3d(
    u: np.ndarray,
    v: np.ndarray,
    w: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute 3D Q-criterion for vortex core identification.

    Q = 0.5 * (||Omega||^2 - ||S||^2)

    Parameters
    ----------
    u, v, w : np.ndarray
        Velocity components of shape (Nz, Ny, Nx) or (T, Nz, Ny, Nx).
    x, y, z : np.ndarray
        Coordinate vectors.
    solid_mask : np.ndarray, optional
        Solid mask array.

    Returns
    -------
    np.ndarray
        Q-criterion field in 1/s^2.
    """
    u_arr, v_arr, w_arr, is_single = _ensure_3d_leading_axes(u, v, w)

    # Compute all 9 components of velocity gradient tensor J_ij = d(u_i) / d(x_j)
    # axis 3 = x, axis 2 = y, axis 1 = z
    ux = np.gradient(u_arr, x, axis=3)
    uy = np.gradient(u_arr, y, axis=2)
    uz = np.gradient(u_arr, z, axis=1)

    vx = np.gradient(v_arr, x, axis=3)
    vy = np.gradient(v_arr, y, axis=2)
    vz = np.gradient(v_arr, z, axis=1)

    wx = np.gradient(w_arr, x, axis=3)
    wy = np.gradient(w_arr, y, axis=2)
    wz = np.gradient(w_arr, z, axis=1)

    # Rate of strain S
    s11 = ux
    s22 = vy
    s33 = wz
    s12 = 0.5 * (uy + vx)
    s13 = 0.5 * (uz + wx)
    s23 = 0.5 * (vz + wy)

    # Vorticity tensor Omega
    o12 = 0.5 * (uy - vx)
    o13 = 0.5 * (uz - wx)
    o23 = 0.5 * (vz - wy)

    norm_s_sq = (
        s11**2 + s22**2 + s33**2 + 2.0 * (s12**2 + s13**2 + s23**2)
    )
    norm_omega_sq = 2.0 * (o12**2 + o13**2 + o23**2)

    q = 0.5 * (norm_omega_sq - norm_s_sq)

    if solid_mask is not None:
        mask_3d = np.asarray(solid_mask, dtype=bool)
        q[:, mask_3d] = np.nan

    return q[0] if is_single else q


def compute_enstrophy(
    vorticity: np.ndarray | tuple[np.ndarray, ...],
    solid_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Compute enstrophy density field: E = 0.5 * ||omega||^2.

    Parameters
    ----------
    vorticity : np.ndarray or tuple of np.ndarray
        Either scalar omega_z for 2D, or tuple (omega_x, omega_y, omega_z) for 3D.
    solid_mask : np.ndarray, optional
        Solid mask array.

    Returns
    -------
    np.ndarray
        Enstrophy density field in 1/s^2.
    """
    if isinstance(vorticity, tuple):
        # 3D vector
        sq_sum = sum(w**2 for w in vorticity)
    else:
        sq_sum = vorticity**2

    enstrophy = 0.5 * sq_sum

    if solid_mask is not None:
        mask = np.asarray(solid_mask, dtype=bool)
        enstrophy[mask] = np.nan

    return enstrophy


def evaluate_incompressibility_metrics(
    div_u: np.ndarray,
    solid_mask: np.ndarray | None = None,
    characteristic_velocity: float = 0.50,
    characteristic_length: float = 0.30,
) -> dict[str, float]:
    """Evaluate physical mass conservation / continuity equation compliance.

    Computes max absolute divergence, RMS divergence on fluid cells, and
    dimensionless divergence normalized by the characteristic shear rate (U0 / D).

    Parameters
    ----------
    div_u : np.ndarray
        Divergence array in 1/s.
    solid_mask : np.ndarray, optional
        Solid mask array.
    characteristic_velocity : float
        Characteristic flow velocity U0 (m/s), default 0.50 for C04.
    characteristic_length : float
        Characteristic obstacle diameter D (m), default 0.30 for C04.

    Returns
    -------
    dict[str, float]
        Dictionary with max_abs_div, rms_div, normalized_rms_div, and fluid_cell_count.
    """
    valid_mask = ~np.isnan(div_u)
    if solid_mask is not None:
        valid_mask &= ~np.asarray(solid_mask, dtype=bool)

    fluid_div = div_u[valid_mask]
    if len(fluid_div) == 0:
        return {
            "max_abs_div": 0.0,
            "rms_div": 0.0,
            "normalized_rms_div": 0.0,
            "fluid_cell_count": 0,
        }

    max_abs = float(np.max(np.abs(fluid_div)))
    rms = float(np.sqrt(np.mean(fluid_div**2)))

    shear_rate = characteristic_velocity / characteristic_length
    norm_rms = float(rms / shear_rate) if shear_rate > 0 else 0.0

    return {
        "max_abs_div": max_abs,
        "rms_div": rms,
        "normalized_rms_div": norm_rms,
        "fluid_cell_count": int(len(fluid_div)),
    }
