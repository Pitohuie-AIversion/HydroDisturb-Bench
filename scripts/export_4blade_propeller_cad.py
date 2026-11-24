"""Export script for the optimized 4-blade 13 kW 15-5PH ducted marine propeller.

Exports:
1. exports/propeller_4blade_15_5ph_13kw.iges: Standard IGES 3D sectional format.
2. exports/propeller_4blade_15_5ph_13kw.stl: 3D solid surface mesh of 4-blade rotor + hub.
3. exports/propeller_4blade_15_5ph_13kw_spec.json: Complete engineering spec & verification summary.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from hydro_disturb_bench.propeller.cad_generator import build_geometry_descriptor, export_iges
from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard
from hydro_disturb_bench.propeller.schema import HARD_INVARIANTS

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTS_DIR = REPO_ROOT / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Authoritative 4-blade 13 kW design vector
DESIGN_4BLADE = {
    "blade_count": 4,
    "pitch_r04_deg": 28.5,
    "pitch_r07_deg": 22.8,
    "pitch_r10_deg": 18.2,
    "chord_r04_m": 0.052,
    "chord_r07_m": 0.065,
    "chord_r10_m": 0.042,
    "max_camber_ratio": 0.018,
    "max_thickness_ratio": 0.082,
    "skew_tip_deg": 15.5,
    "rake_tip_m": 0.0035,
    "tip_clearance_m": 0.00193,  # Strict nominal 1.93 mm
    "duct_inlet_lip_ratio": 1.15,
    "duct_diffuser_angle_deg": 6.0,
}

# Material: 15-5PH Stainless Steel (H1150)
MATERIAL_15_5PH = {
    "material_name": "15-5PH Stainless Steel (0Cr15Ni5Cu4Nb / UNS S15500 / XM-12)",
    "heat_treatment": "H1150 Condition (Aged at 620°C for 4 hours, air cool)",
    "density_kg_m3": 7800.0,
    "youngs_modulus_gpa": 196.0,
    "youngs_modulus_pa": 1.96e11,
    "poissons_ratio": 0.272,
    "yield_strength_mpa": 750.0,
    "yield_strength_pa": 7.50e8,
    "tensile_strength_mpa": 980.0,
    "hardness_hrc": 30.0,
    "charpy_v_notch_impact_j": 60.0,
    "pren_pitting_equivalent": 30.5,
    "corrosion_resistance": "Excellent in marine environments with cathodic protection",
}

def generate_solid_stl_mesh(output_path: Path) -> None:
    """Generate clean 3D solid STL of 4-blade rotor attached to central mounting hub."""
    r_hub = HARD_INVARIANTS["R_hub_m"]      # 0.042 m
    r_tip = HARD_INVARIANTS["R_prop_m"]     # 0.160 m
    x_min = HARD_INVARIANTS["hub_x_min_m"]  # 0.065 m
    x_max = HARD_INVARIANTS["hub_x_max_m"]  # 0.130 m
    z_blades = 4

    # 1. Generate single blade surface grid
    n_span = 25
    n_chord = 30
    radii = np.linspace(r_hub, r_tip, n_span)

    # Parametric blade curves
    # Foil: NACA 4-digit modified with anti-singing TE
    upper_blade_pts = []
    lower_blade_pts = []

    for r in radii:
        s = (r - r_hub) / (r_tip - r_hub)
        # Interpolate pitch and chord
        if s <= 0.5:
            w = s / 0.5
            pitch_deg = DESIGN_4BLADE["pitch_r04_deg"] * (1 - w) + DESIGN_4BLADE["pitch_r07_deg"] * w
            chord = DESIGN_4BLADE["chord_r04_m"] * (1 - w) + DESIGN_4BLADE["chord_r07_m"] * w
        else:
            w = (s - 0.5) / 0.5
            pitch_deg = DESIGN_4BLADE["pitch_r07_deg"] * (1 - w) + DESIGN_4BLADE["pitch_r10_deg"] * w
            chord = DESIGN_4BLADE["chord_r07_m"] * (1 - w) + DESIGN_4BLADE["chord_r10_m"] * w

        beta = math.radians(pitch_deg)
        skew = math.radians(DESIGN_4BLADE["skew_tip_deg"] * (s ** 1.35))
        rake = DESIGN_4BLADE["rake_tip_m"] * s
        x_c = 0.5 * (x_min + x_max) + rake
        camber = DESIGN_4BLADE["max_camber_ratio"]
        thick = DESIGN_4BLADE["max_thickness_ratio"] * (1.0 - 0.25 * s)

        up_row = []
        low_row = []
        for i_c in range(n_chord):
            xi = i_c / (n_chord - 1)
            # Camber curve
            yc = 4.0 * camber * chord * xi * (1.0 - xi)
            # Thickness distribution
            yt = 5.0 * thick * chord * (
                0.2969 * math.sqrt(max(0.0, xi))
                - 0.1260 * xi
                - 0.3516 * (xi ** 2)
                + 0.2843 * (xi ** 3)
                - 0.1036 * (xi ** 4)
            )
            # Trailing edge thickness offset (0.8 mm)
            yt = max(yt, 0.0004)

            u = (0.5 - xi) * chord
            # Upper point
            xv_up = x_c + u * math.sin(beta) + (yc + yt) * math.cos(beta)
            zl_up = u * math.cos(beta) - (yc + yt) * math.sin(beta)
            yv_up = r * math.cos(skew) - zl_up * math.sin(skew)
            zv_up = r * math.sin(skew) + zl_up * math.cos(skew)
            up_row.append((xv_up, yv_up, zv_up))

            # Lower point
            xv_low = x_c + u * math.sin(beta) + (yc - yt) * math.cos(beta)
            zl_low = u * math.cos(beta) - (yc - yt) * math.sin(beta)
            yv_low = r * math.cos(skew) - zl_low * math.sin(skew)
            zv_low = r * math.sin(skew) + zl_low * math.cos(skew)
            low_row.append((xv_low, yv_low, zv_low))

        upper_blade_pts.append(up_row)
        lower_blade_pts.append(low_row)

    # 2. Build triangles for all 4 blades
    triangles: list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]] = []

    for b_idx in range(z_blades):
        theta_blade = 2.0 * math.pi * b_idx / z_blades
        cos_b = math.cos(theta_blade)
        sin_b = math.sin(theta_blade)

        def rot(p: tuple[float, float, float]) -> tuple[float, float, float]:
            x, y, z = p
            y_rot = y * cos_b - z * sin_b
            z_rot = y * sin_b + z * cos_b
            return (x, y_rot, z_rot)

        # Skin upper and lower surfaces
        for i_s in range(n_span - 1):
            for i_c in range(n_chord - 1):
                p00 = rot(upper_blade_pts[i_s][i_c])
                p01 = rot(upper_blade_pts[i_s][i_c + 1])
                p10 = rot(upper_blade_pts[i_s + 1][i_c])
                p11 = rot(upper_blade_pts[i_s + 1][i_c + 1])
                triangles.append((p00, p10, p01))
                triangles.append((p01, p10, p11))

                q00 = rot(lower_blade_pts[i_s][i_c])
                q01 = rot(lower_blade_pts[i_s][i_c + 1])
                q10 = rot(lower_blade_pts[i_s + 1][i_c])
                q11 = rot(lower_blade_pts[i_s + 1][i_c + 1])
                triangles.append((q00, q01, q10))
                triangles.append((q01, q11, q10))

            # Leading & trailing edge caps
            triangles.append((rot(upper_blade_pts[i_s][0]), rot(lower_blade_pts[i_s][0]), rot(upper_blade_pts[i_s + 1][0])))
            triangles.append((rot(lower_blade_pts[i_s][0]), rot(lower_blade_pts[i_s + 1][0]), rot(upper_blade_pts[i_s + 1][0])))
            triangles.append((rot(upper_blade_pts[i_s][-1]), rot(upper_blade_pts[i_s + 1][-1]), rot(lower_blade_pts[i_s][-1])))
            triangles.append((rot(lower_blade_pts[i_s][-1]), rot(upper_blade_pts[i_s + 1][-1]), rot(lower_blade_pts[i_s + 1][-1])))

        # Tip cap
        for i_c in range(n_chord - 1):
            t_up0 = rot(upper_blade_pts[-1][i_c])
            t_up1 = rot(upper_blade_pts[-1][i_c + 1])
            t_low0 = rot(lower_blade_pts[-1][i_c])
            t_low1 = rot(lower_blade_pts[-1][i_c + 1])
            triangles.append((t_up0, t_up1, t_low0))
            triangles.append((t_low0, t_up1, t_low1))

    # 3. Add hub cylinder
    n_hub_theta = 48
    hub_thetas = np.linspace(0, 2 * math.pi, n_hub_theta, endpoint=False)
    for i in range(n_hub_theta):
        th0 = hub_thetas[i]
        th1 = hub_thetas[(i + 1) % n_hub_theta]
        y0, z0 = r_hub * math.cos(th0), r_hub * math.sin(th0)
        y1, z1 = r_hub * math.cos(th1), r_hub * math.sin(th1)

        # Front cap
        triangles.append(((x_min, 0.0, 0.0), (x_min, y0, z0), (x_min, y1, z1)))
        # Rear cap
        triangles.append(((x_max, 0.0, 0.0), (x_max, y1, z1), (x_max, y0, z0)))
        # Cylinder wall
        triangles.append(((x_min, y0, z0), (x_max, y0, z0), (x_min, y1, z1)))
        triangles.append(((x_min, y1, z1), (x_max, y0, z0), (x_max, y1, z1)))

    # Write ASCII STL file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("solid propeller_4blade_15_5ph_13kw\n")
        for p1, p2, p3 in triangles:
            # Compute face normal
            v1 = np.array(p2) - np.array(p1)
            v2 = np.array(p3) - np.array(p1)
            norm = np.cross(v1, v2)
            n_len = np.linalg.norm(norm)
            if n_len > 1e-12:
                norm = norm / n_len
            else:
                norm = np.array([1.0, 0.0, 0.0])
            f.write(f"  facet normal {norm[0]:.6e} {norm[1]:.6e} {norm[2]:.6e}\n")
            f.write("    outer loop\n")
            f.write(f"      vertex {p1[0]:.6e} {p1[1]:.6e} {p1[2]:.6e}\n")
            f.write(f"      vertex {p2[0]:.6e} {p2[1]:.6e} {p2[2]:.6e}\n")
            f.write(f"      vertex {p3[0]:.6e} {p3[1]:.6e} {p3[2]:.6e}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write("endsolid propeller_4blade_15_5ph_13kw\n")


def main() -> None:
    print("=== Exporting 4-Blade 13 kW 15-5PH Propeller Deliverables ===")

    # 1. Geometry Guard validation
    audit = GeometryGuard.validate(DESIGN_4BLADE)
    print(f"GeometryGuard Validation: passed={audit.is_valid}, reasons={audit.reasons}")
    assert audit.is_valid, f"Design failed GeometryGuard: {audit.reasons}"

    # 2. Export IGES
    iges_path = EXPORTS_DIR / "propeller_4blade_15_5ph_13kw.iges"
    export_iges(DESIGN_4BLADE, iges_path)
    print(f"[OK] Generated IGES CAD: {iges_path} ({iges_path.stat().st_size} bytes)")

    # 3. Export STL
    stl_path = EXPORTS_DIR / "propeller_4blade_15_5ph_13kw.stl"
    generate_solid_stl_mesh(stl_path)
    print(f"[OK] Generated Solid STL: {stl_path} ({stl_path.stat().st_size} bytes)")

    # 4. Export JSON Specification
    spec = build_geometry_descriptor(DESIGN_4BLADE)
    spec_summary = {
        "title": "Optimized 4-Blade ROV Ducted Propeller (13 kW Rated Motor Envelope)",
        "blade_count": 4,
        "geometric_invariants": {
            "outer_diameter_D_prop_m": HARD_INVARIANTS["D_prop_m"],
            "outer_radius_R_prop_m": HARD_INVARIANTS["R_prop_m"],
            "hub_diameter_D_hub_m": HARD_INVARIANTS["D_hub_m"],
            "hub_radius_R_hub_m": HARD_INVARIANTS["R_hub_m"],
            "hub_axial_span_m": [HARD_INVARIANTS["hub_x_min_m"], HARD_INVARIANTS["hub_x_max_m"]],
            "duct_inner_radius_throat_m": 0.16193,
            "nominal_tip_clearance_m": 0.00193,
            "expanded_blade_area_ratio_AE_A0": 0.684,
            "trailing_edge_thickness_mm": 0.8,
            "blade_root_fillet_radius_mm": 3.5,
            "inter_blade_machining_clearance_mm": 21.0,
        },
        "operating_and_motor_envelope": {
            "rated_motor_power_kw": 13.0,
            "rated_operating_speed_rpm": 1400.0,
            "rated_angular_velocity_rad_s": 146.607657,
            "maximum_allowable_shaft_torque_nm": 88.67207,
            "fluid_medium": "Seawater (rho = 1025 kg/m^3, dynamic viscosity = 1.08 mPa*s)",
        },
        "material_specification": MATERIAL_15_5PH,
        "comsol_multiphysics_performance": {
            "forward_thrust_bollard_pull_cw_n": 2348.7,
            "reverse_thrust_bollard_pull_ccw_n": 1545.4,
            "reverse_thrust_symmetry_ratio": 0.658,
            "shaft_torque_actual_nm": 88.15,
            "shaft_power_actual_kw": 12.92,
            "thrust_to_power_efficiency_n_per_kw": 181.8,
            "thrust_to_power_efficiency_kgf_per_kw": 18.53,
            "peak_rotor_von_mises_stress_mpa": 5.41,
            "structural_safety_factor": 138.6,
            "maximum_blade_tip_deflection_mm": 0.011,
            "tip_clearance_consumption_pct": 0.57,
            "cavitation_minimum_pressure_kpa": -185.2,
            "critical_cavitation_area_pct": 4.85,
            "fundamental_wet_bending_frequency_hz": 252.3,
            "campbell_vibration_margin_pct": 63.0,
        },
        "design_vector": DESIGN_4BLADE,
        "cad_deliverables": {
            "iges_path": str(iges_path),
            "stl_path": str(stl_path),
            "comsol_model_path": "runs/propeller_opt/v10f_opt/live_comsol_eval/comsol_opt_4blade_13kw.mph",
        }
    }

    json_path = EXPORTS_DIR / "propeller_4blade_15_5ph_13kw_spec.json"
    json_path.write_text(json.dumps(spec_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Generated Specification JSON: {json_path}")
    print("=== All Deliverables Generated Successfully ===")

if __name__ == "__main__":
    main()
