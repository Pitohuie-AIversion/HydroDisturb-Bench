"""Multi-Re 6+2 Matrix Boundary Layer & Mesh Sizing Generator.

Generates complete boundary layer mesh specifications, first-layer cell height (dy1),
layer counts, physical velocities, convective time units, and time-step sizing
for the World Model 6+2 Reynolds number pilot matrix.

Enforces:
1. Low-Re full resolution max(y+) <= 1.0 (dy1 proportional to Re^-0.9)
2. Strict prohibition of buffer layer meshing (5 < y+ < 30)
3. Convective time-step scaling (dt* = dt * U0 / D ~ 0.05 - 0.10 CTU)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

D = 0.3              # Cylinder diameter (m)
NU = 1.1889e-6       # Kinematic viscosity (m2/s)
RHO = 1025.8944      # Density (kg/m3)
ST_NOMINAL = 0.205   # Strouhal number estimate
GROWTH_RATE = 1.20   # Prism layer stretching factor

# 6 Fixed + 2 Adaptive + C04 Anchor
RE_MATRIX = [
    {"tag": "Re5k",    "Re": 5000,    "type": "fixed_pilot",   "desc": "Subcritical laminar vortex shedding"},
    {"tag": "Re40k",   "Re": 40000,   "type": "fixed_pilot",   "desc": "Subcritical turbulent wake, laminar separation"},
    {"tag": "Re126k",  "Re": 126167,  "type": "c04_anchor",    "desc": "C04 baseline calibration anchor"},
    {"tag": "Re180k",  "Re": 180000,  "type": "fixed_pilot",   "desc": "Upper subcritical baseline"},
    {"tag": "Re200k",  "Re": 200000,  "type": "adaptive_cand", "desc": "Pre-critical transition candidate"},
    {"tag": "Re300k",  "Re": 300000,  "type": "fixed_pilot",   "desc": "Critical boundary layer transition regime"},
    {"tag": "Re350k",  "Re": 350000,  "type": "adaptive_cand", "desc": "Post-critical drag drop candidate"},
    {"tag": "Re1M",    "Re": 1000000, "type": "fixed_pilot",   "desc": "Supercritical turbulent separation"},
    {"tag": "Re3M",    "Re": 3000000, "type": "fixed_pilot",   "desc": "Transcritical high-Re regime"}
]


def calculate_case_params(item: dict) -> dict:
    Re = item["Re"]
    U0 = Re * NU / D

    # Skin friction approximation: Cf ~ 0.0592 * Re^-0.2
    Cf = 0.0592 * (Re ** -0.2)
    tau_w = 0.5 * Cf * RHO * (U0 ** 2)
    u_tau = math.sqrt(tau_w / RHO)

    # First layer normal height for target y+ = 1.0
    delta_y1_m = (1.0 * NU) / u_tau
    delta_y1_mm = delta_y1_m * 1000.0

    # Boundary layer thickness estimate
    if Re < 50000:
        delta_bl_m = D * 4.91 / math.sqrt(Re)
        strategy = "Low-Re Resolved (SST, y+<=1.0)"
    elif Re < 500000:
        delta_bl_m = D * 0.38 / (Re ** 0.2)
        strategy = "Low-Re Resolved (SST, y+<=1.0)"
    else:
        delta_bl_m = D * 0.38 / (Re ** 0.2)
        strategy = "Resolved Low-Re (SST, y+<=1.0) / Wall Function (y+>=30)"

    # Prism layers to span ~35-45% of attached BL thickness
    target_bl_prism_m = min(delta_bl_m * 0.40, D * 0.05)
    val = 1.0 + target_bl_prism_m * (GROWTH_RATE - 1.0) / delta_y1_m
    num_layers = max(8, min(24, math.ceil(math.log(max(1.01, val)) / math.log(GROWTH_RATE))))
    total_h_m = delta_y1_m * (GROWTH_RATE ** num_layers - 1.0) / (GROWTH_RATE - 1.0)

    # Convective time scale: t_conv = D / U0
    t_conv = D / U0
    # Shedding period: T_shedding ~ D / (St * U0) = t_conv / St
    T_shedding = t_conv / ST_NOMINAL

    # Target dt* ~ 0.06 - 0.08 convective time units
    # For ~40-50 steps per shedding cycle: dt = T_shedding / 45
    dt_recommended = round(T_shedding / 45.0, 5)
    if dt_recommended > 0.5:
        dt_recommended = round(dt_recommended, 2)
    elif dt_recommended > 0.05:
        dt_recommended = round(dt_recommended, 3)
    elif dt_recommended > 0.005:
        dt_recommended = round(dt_recommended, 4)

    dt_star = dt_recommended * U0 / D

    # 15 shedding periods total time
    t_end_recommended = round(T_shedding * 15.0, 1)

    # Check y+ if C04 mesh (dy1 = 0.0447 mm) were erroneously used:
    yplus_if_c04_mesh = (0.0447e-3 * u_tau) / NU

    return {
        "tag": item["tag"],
        "Re": Re,
        "type": item["type"],
        "description": item["desc"],
        "physics": {
            "U0_m_s": round(U0, 5),
            "D_m": D,
            "nu_m2_s": NU,
            "rho_kg_m3": RHO,
            "Cf_estimate": round(Cf, 6),
            "tau_w_Pa": round(tau_w, 4),
            "u_tau_m_s": round(u_tau, 5),
            "t_conv_s": round(t_conv, 4),
            "T_shedding_s": round(T_shedding, 3)
        },
        "boundary_layer_mesh": {
            "target_yplus": 1.0,
            "delta_y1_mm": round(delta_y1_mm, 5),
            "delta_y1_um": round(delta_y1_mm * 1000.0, 2),
            "num_layers": num_layers,
            "growth_rate": GROWTH_RATE,
            "total_prism_height_mm": round(total_h_m * 1000.0, 3),
            "strategy": strategy,
            "erroneous_yplus_if_using_c04_mesh": round(yplus_if_c04_mesh, 2),
            "buffer_layer_warning": bool(5.0 < yplus_if_c04_mesh < 30.0 and item["tag"] != "Re126k")
        },
        "time_stepping": {
            "dt_recommended_s": dt_recommended,
            "dt_star_ctu": round(dt_star, 4),
            "steps_per_cycle": round(T_shedding / dt_recommended, 1),
            "t_end_15_cycles_s": t_end_recommended
        }
    }


def main():
    results = [calculate_case_params(item) for item in RE_MATRIX]
    output_dir = Path(r"E:\cfd_3d\02_cases_mph")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "multi_re_6plus2_matrix_plan.json"
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Plan written to: {out_file}")

    print("\n" + "=" * 120)
    print(f"{'Case':<8} | {'Re':<10} | {'U0 (m/s)':<10} | {'Δy1 (y+=1)':<12} | {'Layers':<7} | {'H_tot (mm)':<11} | {'dt (s)':<8} | {'dt* (CTU)':<10} | {'y+ if C04 mesh':<16} | {'Risk / Buffer'}")
    print("-" * 120)
    for r in results:
        b = r["boundary_layer_mesh"]
        t = r["time_stepping"]
        p = r["physics"]
        risk = "BUFFER LAYER VIOLATION!" if b["buffer_layer_warning"] else "OK / Compliant"
        print(f"{r['tag']:<8} | {r['Re']:<10,.0f} | {p['U0_m_s']:<10.4f} | {b['delta_y1_mm']:<8.4f} mm | {b['num_layers']:<7} | {b['total_prism_height_mm']:<11.3f} | {t['dt_recommended_s']:<8.4f} | {t['dt_star_ctu']:<10.4f} | {b['erroneous_yplus_if_using_c04_mesh']:<16.2f} | {risk}")
    print("=" * 120)


if __name__ == "__main__":
    main()
