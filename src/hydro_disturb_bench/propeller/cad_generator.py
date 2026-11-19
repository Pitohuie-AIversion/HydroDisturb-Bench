"""CAD and parametric geometry generator for ROV ducted propeller.

Provides:
- 3D spatial boundary entity classification (far-field, nozzle, impeller, moving domain).
- Bidirectional BC swapping based on rotation sign (+rpm CW vs -rpm CCW).
- Parametric blade loft, chord/pitch distribution, and duct geometry descriptors.
- IGES / coordinate table export fallback.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .schema import HARD_INVARIANTS, PropellerDesignVector


def classify_spatial_entities(faces: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Classify 3D boundary faces by spatial coordinates and radial extent."""
    if not faces:
        raise ValueError("faces list cannot be empty")

    result: dict[str, list[int]] = {
        "far": [],
        "far_neg_x_cap": [],
        "far_pos_x_cap": [],
        "far_lateral": [],
        "nozzle": [],
        "impeller": [],
        "moving": [],
    }

    for f in faces:
        fid = int(f.get("id", f.get("tag", 0)))
        x_min = float(f.get("x_min", 0.0))
        x_max = float(f.get("x_max", 0.0))
        r_max = float(f.get("r_max", 0.0))

        # Check far-field domain: axial extent spans approx [-3.39, 1.54] m
        is_far_neg_x = abs(x_min - (-3.39)) < 0.2 and abs(x_max - (-3.39)) < 0.2
        is_far_pos_x = abs(x_min - 1.54) < 0.2 and abs(x_max - 1.54) < 0.2
        is_far_lateral = (x_max - x_min > 2.0) or (r_max >= 1.4)

        if is_far_neg_x:
            result["far"].append(fid)
            result["far_neg_x_cap"].append(fid)
        elif is_far_pos_x:
            result["far"].append(fid)
            result["far_pos_x_cap"].append(fid)
        elif is_far_lateral:
            result["far"].append(fid)
            result["far_lateral"].append(fid)
        # Check impeller: x in [0.0555, 0.1395], r_max <= 0.1605 m
        elif 0.0555 <= x_min and x_max <= 0.1395 and r_max <= 0.1605:
            result["impeller"].append(fid)
        # Check moving rotorflow interface: x in [0.050, 0.145], r_max inside tip gap [0.1602, 0.1675]
        elif 0.050 <= x_min and x_max <= 0.145 and 0.1602 <= r_max <= 0.1675:
            result["moving"].append(fid)
        # Check nozzle: x in [0.005, 0.210], r_max >= 0.1612 (captures inner throat bore + outer shroud)
        elif 0.005 <= x_min and x_max <= 0.210 and r_max >= 0.1612:
            result["nozzle"].append(fid)

    # Fail closed on degenerate inputs where no recognized entities exist
    if not result["far"] and not result["impeller"] and not result["nozzle"]:
        raise ValueError("Degenerate spatial entities: missing far-field caps or recognized physical faces")

    return result


classify_boundaries = classify_spatial_entities


def select_rotation_bcs(speed_rpm: float, far_faces: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Determine upstream and open boundary IDs based on rotational velocity sign.
    
    - CW (+rpm): Flow pushes toward -X; inflow/upstream is +X cap (e.g. [29]), open is [-X cap, lateral] ([1, 2, 3]).
    - CCW (-rpm): Flow pushes toward +X; inflow/upstream is -X cap (e.g. [1]), open is [lateral, +X cap] ([2, 3, 29]).
    """
    if speed_rpm == 0.0:
        raise ValueError("speed_rpm cannot be zero (zero rotation does not define an inflow direction)")
    if not math.isfinite(speed_rpm):
        raise ValueError(f"speed_rpm must be a finite real number, got {speed_rpm!r}")
    if not far_faces or len(far_faces) < 2:
        raise ValueError(f"far_faces must contain at least 2 distinct boundary faces, got {len(far_faces) if far_faces else 0}")

    classified = classify_spatial_entities(far_faces)
    neg_x_caps = classified["far_neg_x_cap"]
    pos_x_caps = classified["far_pos_x_cap"]
    laterals = classified["far_lateral"]

    # Fallback if classify didn't catch via exact bounds
    if not neg_x_caps or not pos_x_caps:
        sorted_by_x = sorted(far_faces, key=lambda f: float(f.get("x_min", 0.0)))
        neg_x_caps = [int(sorted_by_x[0]["id"])]
        pos_x_caps = [int(sorted_by_x[-1]["id"])]
        laterals = [int(f["id"]) for f in sorted_by_x[1:-1]]

    if speed_rpm > 0:
        # CW
        upstream = sorted(pos_x_caps)
        open_bcs = sorted(list(set(neg_x_caps + laterals)))
    else:
        # CCW
        upstream = sorted(neg_x_caps)
        open_bcs = sorted(list(set(laterals + pos_x_caps)))

    return {"upstream": upstream, "open": open_bcs}


assign_rotation_bcs = select_rotation_bcs
swap_rotation_bcs = select_rotation_bcs


def validate_rotation_bcs(
    speed_rpm: float,
    upstream: list[int],
    open_faces: list[int],
    far_faces: list[dict[str, Any]],
) -> None:
    """Validate consistency of assigned upstream and open BCs with rotational velocity sign."""
    expected = select_rotation_bcs(speed_rpm, far_faces)
    if sorted(upstream) != sorted(expected["upstream"]):
        raise ValueError(
            f"Mismatched rpm sign ({speed_rpm}) and upstream BCs: got {upstream}, expected {expected['upstream']}"
        )
    if sorted(open_faces) != sorted(expected["open"]):
        raise ValueError(
            f"Mismatched rpm sign ({speed_rpm}) and open BCs: got {open_faces}, expected {expected['open']}"
        )


check_rotation_bcs = validate_rotation_bcs


def parse_speed_rpm(raw: str | float) -> float:
    """Parse and validate speed RPM string (e.g. '1400[rpm]', '-1400[rpm]')."""
    if isinstance(raw, (int, float)):
        val = float(raw)
        if not math.isfinite(val) or val == 0.0:
            raise ValueError(f"Invalid non-finite or zero RPM value: {raw}")
        return val
    s = str(raw).strip()
    if "[" in s and "]" in s:
        val_str = s[:s.index("[")].strip()
        unit_str = s[s.index("[") + 1:s.index("]")].strip().lower()
        if unit_str != "rpm":
            raise ValueError(f"Invalid unit {unit_str!r} in RPM string (expected [rpm])")
    else:
        val_str = s
    try:
        val = float(val_str)
    except ValueError as err:
        raise ValueError(f"Failed to parse speed_rpm string {raw!r}: {err}") from err
    if not math.isfinite(val) or val == 0.0:
        raise ValueError(f"Invalid non-finite or zero RPM value: {val}")
    return val


format_speed_rpm = parse_speed_rpm


def build_geometry_descriptor(design: PropellerDesignVector | dict[str, Any]) -> dict[str, Any]:
    """Generate 3D geometric specification and sectional coordinates for parametric CAD/COMSOL build."""
    if isinstance(design, PropellerDesignVector):
        d = design.to_dict()
    else:
        d = dict(design)

    d_prop_m = HARD_INVARIANTS["D_prop_m"]
    r_prop_m = HARD_INVARIANTS["R_prop_m"]
    tip_clearance = float(d.get("tip_clearance_m", 0.0030))
    duct_throat_r_m = r_prop_m + tip_clearance
    duct_outer_max_r_m = HARD_INVARIANTS["duct_outer_max_r_m"]
    duct_outer_max_d_m = HARD_INVARIANTS["duct_outer_max_d_m"]
    blade_count = HARD_INVARIANTS["blade_count"]
    min_te = HARD_INVARIANTS["min_te_thickness_m"]

    # Trailing edge thickness modeled from tip chord and thickness ratio, bounded strictly >= min_te
    chord_tip = float(d.get("chord_r10_m", 0.048))
    thick_ratio = float(d.get("max_thickness_ratio", 0.085))
    te_thickness_m = max(min_te, 0.0008, chord_tip * thick_ratio * 0.09)

    # Span stations matching r/R = [0.4, 0.7, 1.0]
    radii = [0.4 * r_prop_m, 0.7 * r_prop_m, r_prop_m]
    pitches = [
        float(d.get("pitch_r04_deg", 28.0)),
        float(d.get("pitch_r07_deg", 22.0)),
        float(d.get("pitch_r10_deg", 17.5)),
    ]
    chords = [
        float(d.get("chord_r04_m", 0.058)),
        float(d.get("chord_r07_m", 0.072)),
        float(d.get("chord_r10_m", 0.048)),
    ]
    skew_tip_deg = float(d.get("skew_tip_deg", 14.0))
    rake_tip_m = float(d.get("rake_tip_m", 0.004))
    camber_ratio = float(d.get("max_camber_ratio", 0.012))
    lip_ratio = float(d.get("duct_inlet_lip_ratio", 1.12))
    diff_angle_deg = float(d.get("duct_diffuser_angle_deg", 6.5))
    r_rotorflow_m = r_prop_m + 0.5 * tip_clearance

    sections: list[dict[str, Any]] = []
    for r, pitch, chord in zip(radii, pitches, chords):
        s_span = max(0.0, (r - HARD_INVARIANTS["R_hub_m"]) / (r_prop_m - HARD_INVARIANTS["R_hub_m"]))
        sections.append({
            "radius_m": r,
            "r_over_rprop": r / r_prop_m,
            "pitch_deg": pitch,
            "chord_m": chord,
            "camber_ratio": camber_ratio,
            "thickness_ratio": thick_ratio,
            "skew_deg": skew_tip_deg * (s_span**1.4),
            "rake_m": rake_tip_m * s_span,
        })

    return {
        "blade_count": blade_count,
        "d_prop_m": d_prop_m,
        "r_prop_m": r_prop_m,
        "d_hub_m": HARD_INVARIANTS["D_hub_m"],
        "r_hub_m": HARD_INVARIANTS["R_hub_m"],
        "r_rotorflow_m": r_rotorflow_m,
        "duct_throat_r_m": duct_throat_r_m,
        "duct_outer_max_r_m": duct_outer_max_r_m,
        "duct_outer_max_d_m": duct_outer_max_d_m,
        "tip_clearance_m": tip_clearance,
        "duct_inlet_lip_ratio": lip_ratio,
        "duct_diffuser_angle_deg": diff_angle_deg,
        "te_thickness_m": te_thickness_m,
        "sections": sections,
    }


generate_geometry_spec = build_geometry_descriptor
build_blade_duct_spec = build_geometry_descriptor


def export_iges(design: PropellerDesignVector | dict[str, Any], output_path: str | Path) -> str:
    """Export standard IGES Type 106/126 parametric sectional coordinate fallback artifact."""
    spec = build_geometry_descriptor(design)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build real 3D sectional points across root-to-tip airfoil stations + duct meridional envelope
    pts: list[tuple[float, float, float]] = [
        (HARD_INVARIANTS["hub_x_min_m"], HARD_INVARIANTS["R_hub_m"], 0.0),
        (HARD_INVARIANTS["hub_x_max_m"], HARD_INVARIANTS["R_hub_m"], 0.0),
    ]
    for sec in spec["sections"]:
        r = float(sec["radius_m"])
        c = float(sec["chord_m"])
        beta = math.radians(float(sec["pitch_deg"]))
        skew = math.radians(float(sec.get("skew_deg", 0.0)))
        x_c = 0.0975 + float(sec.get("rake_m", 0.0))
        camber = float(sec["camber_ratio"])
        thick = float(sec["thickness_ratio"])
        for k in range(17):
            s = k / 16.0
            yc = 4.0 * camber * c * s * (1.0 - s)
            yt = 5.0 * thick * c * (0.2969 * math.sqrt(max(0.0, s)) - 0.1260 * s - 0.3516 * s**2 + 0.2843 * s**3 - 0.1036 * s**4)
            u = (0.5 - s) * c
            for v in (yc + yt, yc - yt):
                xv = x_c + u * math.sin(beta) + v * math.cos(beta)
                zl = u * math.cos(beta) - v * math.sin(beta)
                yv = r * math.cos(skew) - zl * math.sin(skew)
                zv = r * math.sin(skew) + zl * math.cos(skew)
                pts.append((xv, yv, zv))
    pts.extend([
        (HARD_INVARIANTS["duct_x_min_m"], float(spec["duct_throat_r_m"]), 0.0),
        (HARD_INVARIANTS["duct_x_max_m"], float(spec["duct_outer_max_r_m"]), 0.0),
    ])

    flat_coords = ",".join(f"{x:.6f},{y:.6f},{z:.6f}" for x, y, z in pts)
    param_payload = f"106,1,{len(pts)},{flat_coords};"
    p_lines: list[str] = []
    idx = 1
    for off in range(0, len(param_payload), 64):
        chunk = param_payload[off : off + 64].ljust(64)
        p_lines.append(f"{chunk}       1P{idx:>7d}")
        idx += 1

    lines: list[str] = [
        f"{'HydroDisturb-Bench Parametric Propeller IGES':<72}S      1",
        f"{'1H,,1H;,4HNONE,4HNONE,16HHydroDisturb-Bench,16HPropeller CAD,32,38,6,308':<72}G      1",
        f"{',15,4HNONE,1.0,6,1HM,1,0.001;':<72}G      2",
        f"{'     106       1       0       1       0       0       0       000010001':<72}D      1",
        f"{'     106       0       0':<24}{len(p_lines):>8}{'       1                                ':>40}D      2",
        *p_lines,
        f"S      1G      2D      2P{len(p_lines):>7d}                                        T      1",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


write_iges_fallback = export_iges
export_geometry_coordinates = export_iges
