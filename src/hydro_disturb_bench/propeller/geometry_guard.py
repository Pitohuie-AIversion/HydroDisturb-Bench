"""Geometry admission and hard mounting envelope guard for ROV ducted propeller.

Enforces fail-closed validation of:
1. Physical diameter lock (D_prop = 0.320 m +/- 0.0002 m).
2. Hub mounting cylinder lock (D_hub = 0.084 m, x in [0.065, 0.130] m).
3. Duct outer envelope lock (D_duct_out_max <= 0.388856 m, x in [0.009966, 0.202606] m).
4. Tip clearance bounds (delta_tip in [0.0015, 0.0060] m).
5. Anti-entanglement skew monotonicity (skew_tip_deg >= 0 deg, smooth fillet).
6. Non-self-intersection and finite trailing edge thickness (>= 0.3 mm).
"""
from __future__ import annotations

import math
from typing import Any

from .schema import (
    HARD_INVARIANTS,
    PropellerDesignVector,
    PropellerGeometryAudit,
)


class GeometryGuard:
    """Fail-closed geometry admission guard."""

    @classmethod
    def validate(
        cls,
        design: PropellerDesignVector | dict[str, Any],
        measured_bbox: dict[str, Any] | None = None,
    ) -> PropellerGeometryAudit:
        """Validate candidate design and optional measured bounding box against all hard invariants."""
        reasons: list[str] = []

        # Convert / coerce design
        d: dict[str, Any]
        if isinstance(design, PropellerDesignVector):
            d = design.to_dict()
        elif isinstance(design, dict):
            d = dict(design)
        else:
            d = {}
            reasons.append(f"Invalid design object type: {type(design).__name__}")

        # Pre-solve checks from design parameters
        tip_clearance = float(d.get("tip_clearance_m", 0.0030))
        if tip_clearance < HARD_INVARIANTS["tip_clearance_min_m"] - 1e-6:
            reasons.append(
                f"tip_clearance_m={tip_clearance:.6f} < min {HARD_INVARIANTS['tip_clearance_min_m']:.6f}"
            )
        if tip_clearance > HARD_INVARIANTS["tip_clearance_max_m"] + 1e-6:
            reasons.append(
                f"tip_clearance_m={tip_clearance:.6f} > max {HARD_INVARIANTS['tip_clearance_max_m']:.6f}"
            )

        skew_tip = float(d.get("skew_tip_deg", 14.0))
        if skew_tip < 0.0:
            reasons.append(
                f"skew_tip_deg={skew_tip:.2f} < 0 (violates anti-entanglement weed/cable shedding)"
            )

        chord_root = float(d.get("chord_r04_m", 0.058))
        # Max theoretical circumferential spacing on hub cylinder: 2 * pi * R_hub / blade_count
        blade_count_input = int(d.get("blade_count", HARD_INVARIANTS["blade_count"]))
        max_root_chord = 2.0 * math.pi * HARD_INVARIANTS["R_hub_m"] / blade_count_input
        if chord_root > max_root_chord + 0.005:
            reasons.append(
                f"chord_r04_m={chord_root:.4f} > interblade root clearance limit {max_root_chord:.4f}"
            )

        rake_tip = float(d.get("rake_tip_m", 0.004))
        # Axial root overhang estimation
        pitch_root_rad = math.radians(float(d.get("pitch_r04_deg", 28.0)))
        axial_chord_proj = chord_root * math.sin(pitch_root_rad)
        root_center_x = 0.0975 + rake_tip * 0.3  # Nominal hub center is (0.065 + 0.130)/2 = 0.0975
        root_x_min = root_center_x - axial_chord_proj / 2.0
        root_x_max = root_center_x + axial_chord_proj / 2.0

        if root_x_min < HARD_INVARIANTS["hub_x_min_m"] - 0.002 or root_x_max > HARD_INVARIANTS["hub_x_max_m"] + 0.002:
            reasons.append(
                f"Estimated blade root axial extent [{root_x_min:.4f}, {root_x_max:.4f}] breaches hub span [{HARD_INVARIANTS['hub_x_min_m']}, {HARD_INVARIANTS['hub_x_max_m']}]"
            )

        # Post-build / measured bounding box validation
        d_prop_m = HARD_INVARIANTS["D_prop_m"]
        r_prop_m = HARD_INVARIANTS["R_prop_m"]
        d_hub_m = HARD_INVARIANTS["D_hub_m"]
        hub_x_span = [HARD_INVARIANTS["hub_x_min_m"], HARD_INVARIANTS["hub_x_max_m"]]
        duct_outer_max_d_m = HARD_INVARIANTS["duct_outer_max_d_m"]
        duct_x_span = [HARD_INVARIANTS["duct_x_min_m"], HARD_INVARIANTS["duct_x_max_m"]]
        min_te_thickness_m = HARD_INVARIANTS["min_te_thickness_m"]

        if measured_bbox:
            # 1. Propeller diameter / radius
            if "d_prop_m" in measured_bbox:
                raw_d = measured_bbox["d_prop_m"]
                if raw_d is None or not isinstance(raw_d, (int, float)) or math.isnan(raw_d) or raw_d <= 0.0:
                    reasons.append(f"Invalid non-positive or NaN d_prop_m: {raw_d!r}")
                else:
                    d_prop_m = float(raw_d)
                    if abs(d_prop_m - HARD_INVARIANTS["D_prop_m"]) > HARD_INVARIANTS["diameter_tol_m"] + 1e-6:
                        reasons.append(
                            f"d_prop_m={d_prop_m:.6f} exceeds tolerance of {HARD_INVARIANTS['D_prop_m']} +/- {HARD_INVARIANTS['diameter_tol_m']}"
                        )

            if "r_prop_m" in measured_bbox:
                raw_r = measured_bbox["r_prop_m"]
                if raw_r is None or not isinstance(raw_r, (int, float)) or math.isnan(raw_r) or raw_r <= 0.0:
                    reasons.append(f"Invalid non-positive or NaN r_prop_m: {raw_r!r}")
                else:
                    r_prop_m = float(raw_r)
                    if abs(r_prop_m - HARD_INVARIANTS["R_prop_m"]) > HARD_INVARIANTS["radius_tol_m"] + 1e-6:
                        reasons.append(
                            f"r_prop_m={r_prop_m:.6f} exceeds tolerance of {HARD_INVARIANTS['R_prop_m']} +/- {HARD_INVARIANTS['radius_tol_m']}"
                        )

            if "blade_tip_radii_m" in measured_bbox:
                radii = measured_bbox["blade_tip_radii_m"]
                if any(r > HARD_INVARIANTS["R_prop_m"] + HARD_INVARIANTS["radius_tol_m"] + 1e-6 for r in radii):
                    reasons.append(
                        f"blade_tip_radii_m={radii} exceeds tip radius limit {HARD_INVARIANTS['R_prop_m'] + HARD_INVARIANTS['radius_tol_m']}"
                    )

            # 2. Hub diameter & span
            if "d_hub_m" in measured_bbox:
                raw_dh = measured_bbox["d_hub_m"]
                if raw_dh is None or not isinstance(raw_dh, (int, float)) or math.isnan(raw_dh) or raw_dh <= 0.0:
                    reasons.append(f"Invalid d_hub_m: {raw_dh!r}")
                else:
                    d_hub_m = float(raw_dh)
                    if abs(d_hub_m - HARD_INVARIANTS["D_hub_m"]) > HARD_INVARIANTS["diameter_tol_m"] + 1e-6:
                        reasons.append(
                            f"d_hub_m={d_hub_m:.6f} exceeds tolerance of {HARD_INVARIANTS['D_hub_m']} +/- {HARD_INVARIANTS['diameter_tol_m']}"
                        )

            if "hub_x_min_m" in measured_bbox:
                hx_min = float(measured_bbox["hub_x_min_m"])
                hub_x_span[0] = hx_min
                if hx_min < HARD_INVARIANTS["hub_x_min_m"] - 1e-4:
                    reasons.append(
                        f"hub_x_min_m={hx_min:.6f} < physical interface limit {HARD_INVARIANTS['hub_x_min_m']}"
                    )

            if "hub_x_max_m" in measured_bbox:
                hx_max = float(measured_bbox["hub_x_max_m"])
                hub_x_span[1] = hx_max
                if hx_max > HARD_INVARIANTS["hub_x_max_m"] + 1e-4:
                    reasons.append(
                        f"hub_x_max_m={hx_max:.6f} > physical interface limit {HARD_INVARIANTS['hub_x_max_m']}"
                    )

            # 3. Blade root interface
            if "blade_root_x_min_m" in measured_bbox:
                br_min = float(measured_bbox["blade_root_x_min_m"])
                if br_min < HARD_INVARIANTS["hub_x_min_m"] - 1e-4:
                    reasons.append(
                        f"blade_root_x_min_m={br_min:.6f} overhangs hub upstream limit {HARD_INVARIANTS['hub_x_min_m']}"
                    )

            if "blade_root_x_max_m" in measured_bbox:
                br_max = float(measured_bbox["blade_root_x_max_m"])
                if br_max > HARD_INVARIANTS["hub_x_max_m"] + 1e-4:
                    reasons.append(
                        f"blade_root_x_max_m={br_max:.6f} overhangs hub downstream limit {HARD_INVARIANTS['hub_x_max_m']}"
                    )

            if "blade_root_r_min_m" in measured_bbox:
                br_r = float(measured_bbox["blade_root_r_min_m"])
                if br_r > HARD_INVARIANTS["R_hub_m"] + 1e-4:
                    reasons.append(
                        f"blade_root_r_min_m={br_r:.6f} > hub radius {HARD_INVARIANTS['R_hub_m']} (detached root)"
                    )

            # 4. Duct outer envelope
            if "duct_outer_max_d_m" in measured_bbox:
                duct_outer_max_d_m = float(measured_bbox["duct_outer_max_d_m"])
                if duct_outer_max_d_m > HARD_INVARIANTS["duct_outer_max_d_m"] + 1e-5:
                    reasons.append(
                        f"duct_outer_max_d_m={duct_outer_max_d_m:.6f} > vehicle envelope limit {HARD_INVARIANTS['duct_outer_max_d_m']}"
                    )

            if "duct_x_min_m" in measured_bbox:
                dx_min = float(measured_bbox["duct_x_min_m"])
                duct_x_span[0] = dx_min
                if dx_min < HARD_INVARIANTS["duct_x_min_m"] - 1e-4:
                    reasons.append(
                        f"duct_x_min_m={dx_min:.6f} breaches duct upstream limit {HARD_INVARIANTS['duct_x_min_m']}"
                    )

            if "duct_x_max_m" in measured_bbox:
                dx_max = float(measured_bbox["duct_x_max_m"])
                duct_x_span[1] = dx_max
                if dx_max > HARD_INVARIANTS["duct_x_max_m"] + 1e-4:
                    reasons.append(
                        f"duct_x_max_m={dx_max:.6f} breaches duct downstream limit {HARD_INVARIANTS['duct_x_max_m']}"
                    )

            # 5. Rotorflow domain collision & blade axial span
            if "r_prop_m" in measured_bbox:
                if float(measured_bbox["r_prop_m"]) >= HARD_INVARIANTS["rotorflow_r_m"] - 1e-5:
                    reasons.append(
                        f"r_prop_m breaches rotorflow domain boundary {HARD_INVARIANTS['rotorflow_r_m']}"
                    )

            if "blade_x_min_m" in measured_bbox:
                bx_min = float(measured_bbox["blade_x_min_m"])
                if bx_min < HARD_INVARIANTS["blade_x_min_m"] - 1e-4:
                    reasons.append(
                        f"blade_x_min_m={bx_min:.6f} breaches blade clearance {HARD_INVARIANTS['blade_x_min_m']}"
                    )

            if "blade_x_max_m" in measured_bbox:
                bx_max = float(measured_bbox["blade_x_max_m"])
                if bx_max > HARD_INVARIANTS["blade_x_max_m"] + 1e-4:
                    reasons.append(
                        f"blade_x_max_m={bx_max:.6f} breaches blade clearance {HARD_INVARIANTS['blade_x_max_m']}"
                    )

            # 6. Trailing edge thickness
            if "te_thickness_m" in measured_bbox:
                min_te_thickness_m = float(measured_bbox["te_thickness_m"])
                if min_te_thickness_m < HARD_INVARIANTS["min_te_thickness_m"] - 1e-6:
                    reasons.append(
                        f"te_thickness_m={min_te_thickness_m:.6f} < minimum required {HARD_INVARIANTS['min_te_thickness_m']}"
                    )

            # Blade count invariant check
            if "blade_count" in measured_bbox:
                bc = int(measured_bbox["blade_count"])
                allowed_bc = HARD_INVARIANTS.get("allowed_blade_counts", {HARD_INVARIANTS["blade_count"]})
                if bc not in allowed_bc:
                    reasons.append(f"blade_count={bc} not in allowed {allowed_bc}")

            # 7. Self-intersection & anti-entanglement smoothness
            if measured_bbox.get("self_intersecting") is True:
                reasons.append("Blade section or duct surface self-intersection detected")

            if measured_bbox.get("anti_entanglement_smooth") is False:
                reasons.append("Discontinuous fillet or sharp kink violates anti-entanglement smoothness")

        is_valid = (len(reasons) == 0)

        blade_count_res = int(measured_bbox.get("blade_count", HARD_INVARIANTS["blade_count"])) if measured_bbox else HARD_INVARIANTS["blade_count"]
        if blade_count_res not in HARD_INVARIANTS.get("allowed_blade_counts", {3, 4, 5}):
            if not any("blade_count" in r for r in reasons):
                reasons.append(f"blade_count={blade_count_res} not in allowed {HARD_INVARIANTS.get('allowed_blade_counts', {3, 4, 5})}")
            blade_count_res = HARD_INVARIANTS["blade_count"]
            is_valid = False

        return PropellerGeometryAudit(
            is_valid=is_valid,
            d_prop_m=d_prop_m,
            r_prop_m=r_prop_m,
            d_hub_m=d_hub_m,
            hub_x_span=hub_x_span,
            duct_outer_max_d_m=duct_outer_max_d_m,
            duct_x_span=duct_x_span,
            tip_clearance_m=tip_clearance,
            blade_count=blade_count_res,
            min_te_thickness_m=min_te_thickness_m,
            reasons=reasons,
        )
