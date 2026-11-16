"""Cylinder Boundary Layer Mesh & y+ Sizing Calculator for Multi-Re Campaigns.

Calculates boundary layer properties, skin friction Cf, friction velocity u_tau,
first cell height delta_y1 for target y+ (e.g. y+=1 for Low-Re resolved SST),
number of boundary layer prism layers, and total prism layer thickness across
the entire World Model Reynolds number matrix.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass


@dataclass
class BoundaryLayerSizing:
    Re: float
    U0_m_s: float
    D_m: float
    rho_kg_m3: float
    nu_m2_s: float
    target_yplus: float
    Cf_est: float
    tau_wall_pa: float
    u_tau_m_s: float
    delta_y1_m: float
    delta_y1_mm: float
    bl_thickness_est_mm: float
    num_layers_recommended: int
    growth_rate: float
    total_bl_height_mm: float
    mesh_strategy: str


def compute_bl_sizing(
    Re: float,
    D: float = 0.3,
    nu: float = 1.1889e-6,
    rho: float = 1025.8944,
    target_yplus: float = 1.0,
    growth_rate: float = 1.20,
) -> BoundaryLayerSizing:
    """Compute theoretical boundary layer scaling and prism mesh parameters."""
    U0 = Re * nu / D

    # Circular cylinder boundary layer scaling:
    # Before boundary layer separation (theta < 80 deg), Cf is governed by
    # attached boundary layer theory. We use transitional/turbulent flat plate
    # approximation as a conservative upper bound for peak shear stress:
    # Cf ~ 0.0592 * Re^(-0.2)
    Cf = 0.0592 * (Re ** -0.2)
    tau_w = 0.5 * Cf * rho * (U0 ** 2)
    u_tau = math.sqrt(tau_w / rho)

    # First layer normal height for target y+
    delta_y1 = (target_yplus * nu) / u_tau

    # Nominal 99% boundary layer thickness at separation estimate:
    # delta / D ~ 0.38 / (Re**0.2) (turbulent) or ~ 5.0 / sqrt(Re) (laminar)
    if Re < 50000:
        delta_bl_m = D * 4.91 / math.sqrt(Re)
        strategy = "Laminar/Transition Low-Re (SST/Transition SST, y+<=1)"
    elif Re < 500000:
        delta_bl_m = D * 0.38 / (Re ** 0.2)
        strategy = "Subcritical Low-Re Resolved (SST, y+<=1)"
    else:
        delta_bl_m = D * 0.38 / (Re ** 0.2)
        strategy = "Supercritical/Transcritical Resolved Low-Re (y+<=1)"

    # Determine recommended layer count to span ~30-50% of attached BL thickness
    target_total_m = min(delta_bl_m * 0.40, D * 0.05)
    # Sum of geometric series: h_tot = delta_y1 * (r^N - 1) / (r - 1)
    # r^N = 1 + h_tot * (r - 1) / delta_y1
    # N = ceil( ln(...) / ln(r) )
    val = 1.0 + target_total_m * (growth_rate - 1.0) / delta_y1
    num_layers = max(8, min(24, math.ceil(math.log(max(1.01, val)) / math.log(growth_rate))))
    total_h = delta_y1 * (growth_rate ** num_layers - 1.0) / (growth_rate - 1.0)

    return BoundaryLayerSizing(
        Re=Re,
        U0_m_s=round(U0, 5),
        D_m=D,
        rho_kg_m3=rho,
        nu_m2_s=nu,
        target_yplus=target_yplus,
        Cf_est=round(Cf, 6),
        tau_wall_pa=round(tau_w, 4),
        u_tau_m_s=round(u_tau, 5),
        delta_y1_m=delta_y1,
        delta_y1_mm=round(delta_y1 * 1000.0, 5),
        bl_thickness_est_mm=round(delta_bl_m * 1000.0, 3),
        num_layers_recommended=num_layers,
        growth_rate=growth_rate,
        total_bl_height_mm=round(total_h * 1000.0, 3),
        mesh_strategy=strategy,
    )


def evaluate_pilot_matrix(
    Res: list[float] | None = None,
    D: float = 0.3,
    nu: float = 1.1889e-6,
    rho: float = 1025.8944,
    target_yplus: float = 1.0,
) -> list[BoundaryLayerSizing]:
    if Res is None:
        Res = [5000, 40000, 126167, 180000, 200000, 300000, 350000, 1000000, 3000000]
    return [compute_bl_sizing(r, D, nu, rho, target_yplus) for r in Res]


def main():
    parser = argparse.ArgumentParser(description="Calculate boundary layer mesh and y+ for cylinder CFD")
    parser.add_argument("--Re", type=float, default=None, help="Single Reynolds number to evaluate")
    parser.add_argument("--D", type=float, default=0.3, help="Cylinder diameter (m)")
    parser.add_argument("--nu", type=float, default=1.1889e-6, help="Kinematic viscosity (m2/s)")
    parser.add_argument("--rho", type=float, default=1025.8944, help="Density (kg/m3)")
    parser.add_argument("--target-yplus", type=float, default=1.0, help="Target y+ at wall (default: 1.0)")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    args = parser.parse_args()

    if args.Re is not None:
        res = [compute_bl_sizing(args.Re, args.D, args.nu, args.rho, args.target_yplus)]
    else:
        res = evaluate_pilot_matrix(D=args.D, nu=args.nu, rho=args.rho, target_yplus=args.target_yplus)

    if args.json:
        print(json.dumps([asdict(r) for r in res], indent=2))
        return

    print("=" * 105)
    print(f"{'Re':<10} | {'U0 (m/s)':<10} | {'u_tau (m/s)':<12} | {'Δy1 (y+=1)':<14} | {'Layers':<7} | {'Total H (mm)':<12} | {'Strategy'}")
    print("-" * 105)
    for r in res:
        print(f"{r.Re:<10,.0f} | {r.U0_m_s:<10.4f} | {r.u_tau_m_s:<12.4f} | {r.delta_y1_mm:<10.4f} mm  | {r.num_layers_recommended:<7} | {r.total_bl_height_mm:<10.3f} mm | {r.mesh_strategy}")
    print("=" * 105)


if __name__ == "__main__":
    main()
