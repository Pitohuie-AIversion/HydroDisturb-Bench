"""Propeller design vector schema, geometric invariants, and evaluation result models.

Enforces strict physical constraints for ROV integration:
- Propeller diameter D_prop = 0.320 m (R_prop = 0.160 m +/- 1e-4 m) is NEVER a decision variable.
- Hub interface D_hub = 0.084 m, x in [0.065, 0.130] m is fixed.
- Duct outer envelope D_duct_out_max <= 0.388856 m, x in [0.009966, 0.202606] m is fixed.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Hard non-negotiable geometric and mounting invariants (PROJECT.md § Interface Contracts 1)
P_rated_max_kw: float = 13.0
max_shaft_torque_nm: float = 88.672
ALLOWED_BLADE_COUNTS: set[int] = {3, 4, 5}

HARD_INVARIANTS: dict[str, Any] = {
    "D_prop_m": 0.320,
    "R_prop_m": 0.160,
    "radius_tol_m": 1e-4,
    "diameter_tol_m": 2e-4,
    "D_hub_m": 0.084,
    "R_hub_m": 0.042,
    "hub_x_min_m": 0.065,
    "hub_x_max_m": 0.130,
    "duct_x_min_m": 0.009966,
    "duct_x_max_m": 0.202606,
    "duct_outer_max_d_m": 0.388856,
    "duct_outer_max_r_m": 0.194428,
    "rotorflow_r_m": 0.1625,
    "rotorflow_x_min_m": 0.055,
    "rotorflow_x_max_m": 0.140,
    "blade_x_min_m": 0.056,
    "blade_x_max_m": 0.139,
    "tip_clearance_min_m": 0.0015,
    "tip_clearance_max_m": 0.0060,
    "blade_count": 3,
    "allowed_blade_counts": {3, 4, 5},
    "blade_counts": (3, 4, 5),
    "P_rated_max_kw": 13.0,
    "max_shaft_torque_nm": 88.672,
    "forbidden_dof_names": ("diameter", "D_prop", "d_prop", "prop_diameter", "D_hub", "hub_diameter"),
    "min_te_thickness_m": 0.0003,
    "eta_sym_floor": 0.60,
    "modal_bpf_sep_min": 0.15,
    "stress_safety_factor_min": 2.0,
}

FORBIDDEN_DOF_NAMES: tuple[str, ...] = (
    "diameter",
    "D_prop",
    "d_prop_m",
    "D_prop_m",
    "r_prop_m",
    "R_prop",
    "R_prop_m",
    "hub_radius",
    "d_hub_m",
    "D_hub_m",
    "hub_x_min",
    "hub_x_max",
    "duct_outer_max_r",
    "duct_outer_max_d_m",
    "duct_x_min",
    "duct_x_max",
)

FREE_DOF_NAMES: tuple[str, ...] = (
    "pitch_r04_deg",
    "pitch_r07_deg",
    "pitch_r10_deg",
    "chord_r04_m",
    "chord_r07_m",
    "chord_r10_m",
    "max_camber_ratio",
    "max_thickness_ratio",
    "skew_tip_deg",
    "rake_tip_m",
    "tip_clearance_m",
    "duct_inlet_lip_ratio",
    "duct_diffuser_angle_deg",
)

DEFAULT_BOUNDS: dict[str, list[float]] = {
    "pitch_r04_deg": [15.0, 45.0],
    "pitch_r07_deg": [15.0, 45.0],
    "pitch_r10_deg": [15.0, 45.0],
    "chord_r04_m": [0.035, 0.085],
    "chord_r07_m": [0.035, 0.085],
    "chord_r10_m": [0.025, 0.075],
    "max_camber_ratio": [0.0, 0.060],
    "max_thickness_ratio": [0.060, 0.160],
    "skew_tip_deg": [0.0, 40.0],
    "rake_tip_m": [-0.010, 0.015],
    "tip_clearance_m": [0.0015, 0.0060],
    "duct_inlet_lip_ratio": [0.90, 1.35],
    "duct_diffuser_angle_deg": [-4.0, 10.0],
}

NOMINAL_DESIGN_DICT: dict[str, float] = {
    "pitch_r04_deg": 28.0,
    "pitch_r07_deg": 22.0,
    "pitch_r10_deg": 17.5,
    "chord_r04_m": 0.058,
    "chord_r07_m": 0.072,
    "chord_r10_m": 0.048,
    "max_camber_ratio": 0.012,
    "max_thickness_ratio": 0.085,
    "skew_tip_deg": 14.0,
    "rake_tip_m": 0.004,
    "tip_clearance_m": 0.0030,
    "duct_inlet_lip_ratio": 1.12,
    "duct_diffuser_angle_deg": 6.5,
}


class PropellerDesignVector(BaseModel):
    """13-DOF decision vector for ducted propeller optimization.

    Diameter, hub envelope, and duct outer envelope are strictly excluded from free DOFs.
    """
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    pitch_r04_deg: float = Field(default=28.0, description="Pitch angle at r/R=0.4 in deg")
    pitch_r07_deg: float = Field(default=22.0, description="Pitch angle at r/R=0.7 in deg")
    pitch_r10_deg: float = Field(default=17.5, description="Pitch angle at r/R=1.0 in deg")
    chord_r04_m: float = Field(default=0.058, gt=0.0, description="Chord length at r/R=0.4 in m")
    chord_r07_m: float = Field(default=0.072, gt=0.0, description="Chord length at r/R=0.7 in m")
    chord_r10_m: float = Field(default=0.048, gt=0.0, description="Chord length at r/R=1.0 in m")
    max_camber_ratio: float = Field(default=0.012, ge=0.0, description="Max camber-to-chord ratio f_max/c >= 0")
    max_thickness_ratio: float = Field(default=0.085, gt=0.010, lt=0.40, description="Max thickness-to-chord ratio t_max/c in (0.01, 0.40)")
    skew_tip_deg: float = Field(default=14.0, ge=0.0, description="Blade tip skew in deg (>=0 for anti-entanglement)")
    rake_tip_m: float = Field(default=0.004, description="Blade tip axial rake in m")
    tip_clearance_m: float = Field(default=0.0030, ge=0.0015, le=0.0060, description="Tip clearance in m [0.0015, 0.0060]")
    duct_inlet_lip_ratio: float = Field(default=1.12, gt=0.0, description="Duct inlet contraction lip ratio")
    duct_diffuser_angle_deg: float = Field(default=6.5, description="Duct diffuser expansion half-angle in deg")

    @field_validator("*", mode="before")
    @classmethod
    def _validate_finite(cls, v: Any) -> Any:
        if isinstance(v, (int, float)):
            if math.isnan(v) or math.isinf(v):
                raise ValueError("Design vector parameters must be finite real numbers")
        return v

    def to_dict(self) -> dict[str, float]:
        return self.model_dump()


def validate_design_space_bounds(bounds: dict[str, Any]) -> dict[str, list[float]]:
    """Validate that bounds dictionary contains no forbidden invariant DOFs."""
    clean_bounds: dict[str, list[float]] = {}
    for key, val in bounds.items():
        if key in FORBIDDEN_DOF_NAMES or any(f in key.lower() for f in ("diameter", "d_prop", "hub_rad")):
            raise ValueError(f"Forbidden geometric invariant in design bounds: {key!r}")
        if not isinstance(val, (list, tuple)) or len(val) != 2:
            raise ValueError(f"Bounds for {key} must be a 2-element sequence [min, max]")
        v_min, v_max = float(val[0]), float(val[1])
        if not (math.isfinite(v_min) and math.isfinite(v_max)) or v_min >= v_max:
            raise ValueError(f"Invalid bound range for {key}: [{v_min}, {v_max}]")
        clean_bounds[key] = [v_min, v_max]
    return clean_bounds


class DesignSpaceConfig(BaseModel):
    """Configuration for optimization design space bounds."""
    model_config = ConfigDict(extra="forbid")
    bounds: dict[str, list[float]] = Field(default_factory=lambda: dict(DEFAULT_BOUNDS))

    @model_validator(mode="before")
    @classmethod
    def _check_bounds(cls, data: Any) -> Any:
        if isinstance(data, dict) and "bounds" in data:
            data["bounds"] = validate_design_space_bounds(data["bounds"])
        elif isinstance(data, dict):
            # If bounds passed as top-level dict
            if any(k in FREE_DOF_NAMES or k in FORBIDDEN_DOF_NAMES for k in data):
                return {"bounds": validate_design_space_bounds(data)}
        return data


PropellerOptConfig = DesignSpaceConfig


class PropellerGeometryAudit(BaseModel):
    """Geometry audit result matching PROJECT.md interface contract."""
    model_config = ConfigDict(extra="ignore")

    is_valid: bool = True
    d_prop_m: float = HARD_INVARIANTS["D_prop_m"]
    r_prop_m: float = HARD_INVARIANTS["R_prop_m"]
    d_hub_m: float = HARD_INVARIANTS["D_hub_m"]
    hub_x_span: list[float] = Field(default_factory=lambda: [HARD_INVARIANTS["hub_x_min_m"], HARD_INVARIANTS["hub_x_max_m"]])
    duct_outer_max_d_m: float = HARD_INVARIANTS["duct_outer_max_d_m"]
    duct_x_span: list[float] = Field(default_factory=lambda: [HARD_INVARIANTS["duct_x_min_m"], HARD_INVARIANTS["duct_x_max_m"]])
    tip_clearance_m: float = 0.0030
    blade_count: int = HARD_INVARIANTS["blade_count"]
    min_te_thickness_m: float = HARD_INVARIANTS["min_te_thickness_m"]
    reasons: list[str] = Field(default_factory=list)

    @field_validator("blade_count")
    @classmethod
    def _validate_blade_count(cls, v: int) -> int:
        allowed = HARD_INVARIANTS.get("allowed_blade_counts", {3, 4, 5})
        if v not in allowed:
            raise ValueError(f"blade_count must be in {allowed}, got {v}")
        return v


GeometryAudit = PropellerGeometryAudit


class PropellerEvalResult(BaseModel):
    """Complete multi-physics evaluation contract for a candidate propeller."""
    model_config = ConfigDict(extra="ignore")

    case_id: str
    status: str = "SUCCESS"
    geometry_audit: dict[str, Any] = Field(default_factory=dict)
    cfd_ccw: dict[str, Any] = Field(default_factory=dict)
    cfd_cw: dict[str, Any] = Field(default_factory=dict)
    t_mean_n: float | None = None
    t_over_p_mean: float | None = None
    eta_sym: float | None = None
    dp_maneuver: dict[str, Any] = Field(default_factory=dict)
    hover_disturbance: dict[str, Any] = Field(default_factory=dict)
    cavitation: dict[str, Any] = Field(default_factory=dict)
    structural_modal: dict[str, Any] = Field(default_factory=dict)
    fidelity: str = "calibrated_analytical"
    wall_time_s: float = 0.0
    error_code: str | None = None
