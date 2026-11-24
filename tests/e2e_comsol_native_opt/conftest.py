"""Shared fixtures, environment detection, physical invariants, and opaque-box verification helpers.

This module provides the independent testing infrastructure for the COMSOL 6.4
Native Optimization & Hydro-Structural Co-Optimization E2E test suite.
All tests validate strictly against ORIGINAL_REQUEST.md, PROJECT.md, and TEST_INFRA.md.
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Repository root and path constants
REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"
PROP_TEST2_DIR = MODELS_DIR / "prop test2"
CW_BASELINE_MPH = PROP_TEST2_DIR / "COMSOL_CW_1400rpm" / "V10f-StaticFine-1400rpm.mph"
CCW_BASELINE_MPH = PROP_TEST2_DIR / "COMSOL_CCW_1400rpm" / "V10f-CCWFine-1400rpm.mph"
BASELINE_XT = PROP_TEST2_DIR / "3_320kgf_v10f.x_t"

# Rated Motor Power and Torque Limit (User Clarification 2026-11-18)
MOTOR_POWER_ENVELOPE: dict[str, Any] = {
    "P_rated_max_kw": 13.0,
    "P_rated_max_w": 13000.0,
    "operating_rpm": 1400.0,
    "omega_rad_s": 2.0 * math.pi * 1400.0 / 60.0,  # 146.607657 rad/s
    "max_shaft_torque_nm": 13000.0 / (2.0 * math.pi * 1400.0 / 60.0),  # 88.67207 N*m
    "max_shaft_torque_rounded_nm": 88.67,
}

# Flexible Blade Count Topology Exploration (User Clarification 2026-11-18)
BLADE_COUNT_SPECS: dict[str, Any] = {
    "allowed_blade_counts": [3, 4, 5],
    "default_blade_count": 3,
    "objective": "maximize thrust per kW within 13 kW envelope",
}

# Non-negotiable physical invariants (ORIGINAL_REQUEST §R2, PROJECT §Interface Contracts)
PHYSICAL_INVARIANTS: dict[str, Any] = {
    "D_prop_m": 0.320,
    "R_prop_m": 0.160,
    "radius_tol_m": 0.0001,       # ±0.1 mm radius tolerance
    "diameter_tol_m": 0.0002,    # ±0.2 mm diameter tolerance
    "D_hub_m": 0.084,
    "R_hub_m": 0.042,
    "hub_span_x_min_m": 0.065,
    "hub_span_x_max_m": 0.130,
    "hub_axial_length_m": 0.065,  # 0.130 - 0.065
    "duct_inner_radius_m": 0.16193,
    "radial_tip_clearance_m": 0.00193,  # 0.16193 - 0.160 = 1.93 mm
    "radial_tip_clearance_tol_m": 1e-5,
    "blade_count": 3,
    "operating_rpm": 1400.0,
    "reverse_rpm": -1400.0,
    "fluid_density_kg_m3": 1025.0,     # Seawater
    "fluid_dynamic_viscosity_pa_s": 0.00108,
}

# Material: Aluminum 6061-T6 (ORIGINAL_REQUEST §R2)
AL_6061_T6: dict[str, Any] = {
    "name": "Aluminum 6061-T6",
    "yield_strength_mpa": 276.0,
    "yield_strength_pa": 276e6,
    "youngs_modulus_gpa": 69.0,
    "youngs_modulus_pa": 69e9,
    "poissons_ratio": 0.33,
    "density_kg_m3": 2700.0,
    "stress_limit_mpa": 50.0,       # peak von Mises stress <= 50 MPa
    "stress_limit_pa": 50e6,
    "tip_deflection_limit_m": 0.0001,  # tip deflection <= 0.1 mm
    "tip_deflection_limit_mm": 0.1,
    "min_safety_factor": 276.0 / 50.0,  # 5.52 > 2.0
}

# Baseline quantitative benchmarks from reference fine-mesh simulation (models/prop test2)
BASELINE_BENCHMARKS: dict[str, Any] = {
    "cw_thrust_total_n": 1262.36,
    "cw_rotor_fx_n": 679.77,
    "cw_duct_fx_n": 582.58,
    "cw_torque_nm": 55.11,
    "cw_power_kw": 8.08,
    "cw_efficiency_n_kw": 156.25,
    "cw_pmin_kpa": -214.4,
    "cw_cavitation_area_pct": 0.519,
    "ccw_thrust_total_n": 2475.37,
    "ccw_rotor_fx_n": -1387.10,
    "ccw_duct_fx_n": -1088.27,
    "ccw_torque_nm": -84.07,
    "ccw_power_kw": 12.33,
    "ccw_efficiency_n_kw": 200.84,
    "ccw_pmin_kpa": -84.8,
    "ccw_cavitation_area_pct": 0.0,
    "baseline_symmetry_ratio": 1262.36 / 2475.37,  # 0.509966 (~51.0%)
    "target_symmetry_ratio_min": 0.60,              # Requirement: T_CCW >= 0.60 * T_CW
    "cw_sha256": "C8968A46A4BE1398AAAB377CCA21CE1BF6E581CFACE1710774B676E5D04E41D4",
    "ccw_sha256": "194072E12EF86E433B3F857257A8EAD598BEF82E17E1403D547BE247178B1205",
    "cad_xt_sha256": "0340421D166328C83B7961346867F98F728ADABDD3832F9632A8D2AD8CB48A99",
}

# COMSOL 6.4 Native EGO & GaussianProcess Specification Table
COMSOL_NATIVE_SPEC: dict[str, Any] = {
    "ego_properties": {
        "optmethod": "ego",
        "covfunction_allowed": ["matern32", "matern52", "se", "nn"],
        "covfunction_default": "matern32",
        "meanfunction_allowed": ["const", "linear", "quadratic"],
        "meanfunction_default": "const",
        "acquisitionmaxmethod_allowed": ["direct", "montecarlo"],
        "acquisitionmaxmethod_default": "direct",
        "ninitsample_default": 15,
        "maxgpevalsacquisition_default": 10000,
        "maxgpitersacquisition_default": 500,
        "restol_default": 1e-16,
        "globalgpconstrfunction_allowed": ["new", "existing"],
    },
    "gp_function_properties": {
        "tag": "gp1",
        "type": "GaussianProcess",
        "definestddev": True,
        "stddevsuffix_default": "_stddev",
        "source_allowed": ["file", "resultTable"],
        "source_invalid": ["table"],  # Invalid property value in COMSOL 6.4
        "maxmatsize_default": 2000,
    },
    "pareto_uq_properties": {
        "multiobjective_allowed": ["sum", "min", "max"],
        "pareto_methods": ["weighted_sum", "epsilon_constraint"],
        "study_types": ["SurrogateModelTraining", "UncertaintyPropagation"],
        "egra_adaptive_gp": True,
        "sobol_indices": ["first_order", "total_order"],
    },
}


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for the 4-tier E2E test hierarchy."""
    config.addinivalue_line("markers", "tier1: Tier 1 Feature Coverage (Category-Partition, F1-F9)")
    config.addinivalue_line("markers", "tier2: Tier 2 Boundary & Corner Cases (BVA & Invariants)")
    config.addinivalue_line("markers", "tier3: Tier 3 Cross-Feature Combinations (Pairwise Multi-Physics)")
    config.addinivalue_line("markers", "tier4: Tier 4 Real-World Application Scenarios (1400 rpm & Native EGO)")


def compute_file_sha256(path: Path | str) -> str:
    """Compute uppercase SHA-256 hash of a file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def get_comsol_environment() -> dict[str, Any]:
    """Detect and return COMSOL installation paths and tools."""
    env_root = os.environ.get("COMSOL_ROOT")
    if not env_root:
        # Check standard default installation path
        default_path = Path("D:/Program Files/COMSOL64/Multiphysics")
        if default_path.is_dir():
            env_root = str(default_path)

    if env_root and Path(env_root).is_dir():
        root_path = Path(env_root)
        bin_dir = root_path / "bin" / "win64"
        java_bin = root_path / "java" / "win64" / "jre" / "bin" / "java.exe"
        compile_bin = bin_dir / "comsolcompile.exe"
        batch_bin = bin_dir / "comsolbatch.exe"
        return {
            "available": True,
            "comsol_root": str(root_path),
            "java_exe": str(java_bin) if java_bin.is_file() else None,
            "comsolcompile": str(compile_bin) if compile_bin.is_file() else None,
            "comsolbatch": str(batch_bin) if batch_bin.is_file() else None,
            "version": "6.4",
            "build": "429",
        }
    return {
        "available": False,
        "comsol_root": None,
        "java_exe": None,
        "comsolcompile": None,
        "comsolbatch": None,
        "version": None,
        "build": None,
    }


@dataclass
class AnalyticalHydroStructuralValidator:
    """Analytical verification oracle for ducted marine propeller hydro-structural mechanics.

    Provides physics-based validation formulas for:
    - Cantilever beam blade bending stress and tip deflection under CFD hydrodynamic loads.
    - Centrifugal stress and frequency calculations at 1400 rpm.
    - Expected Improvement (EI) calculation for native EGO optimization.
    - Reverse thrust symmetry ratio and cavitation pressure limits.
    """

    e_modulus: float = AL_6061_T6["youngs_modulus_pa"]
    density: float = AL_6061_T6["density_kg_m3"]
    yield_strength: float = AL_6061_T6["yield_strength_pa"]
    nu: float = AL_6061_T6["poissons_ratio"]
    r_prop: float = PHYSICAL_INVARIANTS["R_prop_m"]
    r_hub: float = PHYSICAL_INVARIANTS["R_hub_m"]

    @property
    def blade_span(self) -> float:
        """Active aerodynamic/hydrodynamic blade span (R_prop - R_hub)."""
        return self.r_prop - self.r_hub  # 0.160 - 0.042 = 0.118 m

    def calculate_blade_bending(
        self,
        thrust_rotor_n: float = 679.77,
        blade_count: int = 3,
        chord_root_m: float = 0.075,
        thickness_root_m: float = 0.012,
    ) -> dict[str, float]:
        """Calculate peak bending stress and blade tip deflection under hydrodynamic thrust load.

        Using Euler-Bernoulli beam theory with distributed hydrodynamic loading:
        - Total thrust per blade: F_blade = F_rotor / blade_count
        - Area moment of inertia for root section: I_xx = (chord * thickness^3) / 12
        - Center of pressure acting at ~70% span: L_cp = 0.70 * span
        - Bending moment at root: M_b = F_blade * L_cp
        - Peak bending stress: sigma_b = (M_b * (thickness / 2)) / I_xx = 6 * M_b / (chord * thickness^2)
        - Tip deflection: delta_tip = (F_blade * span^3) / (3 * E * I_xx) * (load factor)
        """
        if not isinstance(blade_count, int) or isinstance(blade_count, bool):
            raise TypeError(f"Blade count Z must be an integer, got {type(blade_count).__name__}")
        if blade_count not in BLADE_COUNT_SPECS["allowed_blade_counts"]:
            raise ValueError(f"Blade count Z={blade_count} outside allowed domain {BLADE_COUNT_SPECS['allowed_blade_counts']}")
        if thrust_rotor_n < 0.0 or not math.isfinite(thrust_rotor_n):
            raise ValueError(f"Hydrodynamic thrust must be non-negative and finite: {thrust_rotor_n}")

        f_blade = thrust_rotor_n / blade_count
        span = self.blade_span
        l_cp = 0.70 * span
        m_b = f_blade * l_cp

        # Second moment of area
        i_xx = (chord_root_m * (thickness_root_m ** 3)) / 12.0

        # Peak stress at root surface
        sigma_peak_pa = (m_b * (thickness_root_m / 2.0)) / i_xx
        sigma_peak_mpa = sigma_peak_pa / 1e6

        # Tip deflection under concentrated equivalent load at 0.70 span
        # For cantilever: delta(L) with point load at a: delta = (F * a^2) / (6 * E * I) * (3L - a)
        a = l_cp
        delta_tip_m = (f_blade * (a ** 2) * (3 * span - a)) / (6.0 * self.e_modulus * i_xx)
        delta_tip_mm = delta_tip_m * 1000.0

        return {
            "f_per_blade_n": f_blade,
            "bending_moment_nm": m_b,
            "peak_stress_pa": sigma_peak_pa,
            "peak_stress_mpa": sigma_peak_mpa,
            "tip_deflection_m": delta_tip_m,
            "tip_deflection_mm": delta_tip_mm,
            "stress_ok": sigma_peak_mpa <= AL_6061_T6["stress_limit_mpa"],
            "deflection_ok": delta_tip_m <= AL_6061_T6["tip_deflection_limit_m"],
        }

    def calculate_centrifugal_stress(
        self,
        rpm: float = 1400.0,
        span_m: float | None = None,
    ) -> dict[str, float]:
        """Calculate centrifugal tensile stress at blade root at specified RPM."""
        omega = 2.0 * math.pi * abs(rpm) / 60.0
        span = span_m if span_m is not None else self.blade_span
        r_mid = self.r_hub + 0.5 * span

        # Centrifugal stress sigma_c = 0.5 * rho * omega^2 * (r_tip^2 - r_hub^2)
        sigma_c_pa = 0.5 * self.density * (omega ** 2) * (self.r_prop ** 2 - self.r_hub ** 2)
        sigma_c_mpa = sigma_c_pa / 1e6

        return {
            "omega_rad_s": omega,
            "centrifugal_stress_pa": sigma_c_pa,
            "centrifugal_stress_mpa": sigma_c_mpa,
        }

    def compute_expected_improvement(
        self,
        mu: float,
        sigma: float,
        f_best: float,
        maximize: bool = True,
        xi: float = 0.01,
    ) -> float:
        """Compute Expected Improvement (EI) matching COMSOL 6.4 EGO formulation."""
        from scipy.stats import norm
        if maximize:
            diff = mu - f_best - xi
        else:
            diff = f_best - mu - xi

        if sigma <= 1e-12:
            return max(0.0, float(diff))

        z = diff / sigma
        ei = diff * norm.cdf(z) + sigma * norm.pdf(z)
        return max(0.0, float(ei))

    def evaluate_reverse_symmetry(self, thrust_cw: float, thrust_ccw: float) -> dict[str, Any]:
        """Evaluate reverse thrust symmetry ratio eta_sym = min(T_CW, T_CCW) / max(T_CW, T_CCW)."""
        abs_cw = abs(thrust_cw)
        abs_ccw = abs(thrust_ccw)
        if max(abs_cw, abs_ccw) < 1e-9:
            ratio = 0.0
            satisfies_req = False
        else:
            ratio = abs_ccw / abs_cw if abs_cw > 0 else 0.0
            # Requirement: CCW reverse thrust >= 60% CW forward thrust
            satisfies_req = ratio >= 0.60

        eta_sym_normalized = min(abs_cw, abs_ccw) / max(1e-9, max(abs_cw, abs_ccw))
        return {
            "cw_thrust_magnitude": abs_cw,
            "ccw_thrust_magnitude": abs_ccw,
            "ccw_to_cw_ratio": ratio,
            "eta_sym_normalized": eta_sym_normalized,
            "satisfies_60pct_symmetry": satisfies_req,
        }


    def calculate_shaft_power_and_torque(
        self, torque_nm: float = 0.0, rpm: float = 1400.0, power_kw: float | None = None
    ) -> dict[str, Any]:
        """Calculate shaft power P = omega * Q and check against 13 kW limit."""
        if power_kw is not None:
            if power_kw < 0.0 or not math.isfinite(power_kw):
                raise ValueError(f"Shaft power must be non-negative and finite, got {power_kw}")
            p_kw = power_kw
            p_w = p_kw * 1000.0
            omega = 2.0 * math.pi * abs(rpm) / 60.0
            t_nm = p_w / omega if omega > 0 else 0.0
        else:
            if not math.isfinite(torque_nm):
                raise ValueError(f"Shaft torque must be finite, got {torque_nm}")
            omega = 2.0 * math.pi * abs(rpm) / 60.0
            p_w = abs(torque_nm) * omega
            p_kw = p_w / 1000.0
            t_nm = abs(torque_nm)

        within_13kw = p_kw <= MOTOR_POWER_ENVELOPE["P_rated_max_kw"]
        torque_margin_nm = MOTOR_POWER_ENVELOPE["max_shaft_torque_nm"] - t_nm
        return {
            "rpm": rpm,
            "omega_rad_s": omega,
            "torque_nm": t_nm,
            "power_w": p_w,
            "power_kw": p_kw,
            "within_13kw": within_13kw,
            "torque_margin_nm": torque_margin_nm,
            "thermal_overload": not within_13kw,
        }

    def validate_stress_limits(
        self, sigma_vm_mpa: float, tip_deflection_m: float = 0.0
    ) -> dict[str, bool]:
        """Validate von Mises stress and tip deflection against physical non-negativity and limits."""
        if not math.isfinite(sigma_vm_mpa) or sigma_vm_mpa < 0.0:
            raise ValueError(f"von Mises stress must be non-negative and finite, got {sigma_vm_mpa}")
        if not math.isfinite(tip_deflection_m) or tip_deflection_m < 0.0:
            raise ValueError(f"Tip deflection must be non-negative and finite, got {tip_deflection_m}")
        return {
            "stress_ok": sigma_vm_mpa <= AL_6061_T6["stress_limit_mpa"],
            "deflection_ok": tip_deflection_m <= AL_6061_T6["tip_deflection_limit_m"],
        }

    def validate_solver_residual(self, rel_residual: float, tolerance: float = 0.005) -> float:
        """Validate solver convergence residual against tolerance."""
        if rel_residual > tolerance:
            raise RuntimeError(
                f"Solver diverged: relative residual {rel_residual} exceeds tolerance {tolerance}"
            )
        return rel_residual

    def validate_sweep_configuration(
        self, distribute: bool = False, reusesol: bool = False
    ) -> dict[str, Any]:
        """Validate parametric sweep configuration for cluster / distributed execution.

        COMSOL 6.4 contract: Distributed parametric sweep executes parameter values
        in parallel across MPI cluster nodes. It strictly forbids sequential solution
        reuse (reusesol=True), which requires deterministic serial execution.
        """
        has_conflict = bool(distribute and reusesol)
        return {
            "distribute": distribute,
            "reusesol": reusesol,
            "is_valid": not has_conflict,
            "has_conflict": has_conflict,
            "error_code": "SWEEP_DISTRIBUTED_REUSESOL_CONFLICT" if has_conflict else None,
        }

    def evaluate_thrust_per_kw(self, thrust_n: float, power_kw: float) -> float:
        """Evaluate thrust-to-power efficiency T / P [N/kW]."""
        if power_kw <= 1e-6:
            return 0.0
        return float(thrust_n / power_kw)

    def validate_von_mises_stress(self, stress_mpa: float) -> bool:
        """Validate von Mises stress non-negativity and threshold."""
        if stress_mpa < 0:
            raise ValueError("von Mises stress must be non-negative")
        return stress_mpa <= AL_6061_T6["stress_limit_mpa"]

    def validate_shaft_power(self, power_kw: float) -> bool:
        """Validate shaft power non-negativity and 13 kW envelope."""
        if power_kw < 0:
            raise ValueError("Shaft power must be non-negative")
        return power_kw <= MOTOR_POWER_ENVELOPE["P_rated_max_kw"]

    def validate_blade_count(self, z: Any) -> bool:
        """Validate blade count integer type and allowed set {3, 4, 5}."""
        if not isinstance(z, int) or isinstance(z, bool):
            raise TypeError("Blade count Z must be an integer")
        if z not in BLADE_COUNT_SPECS["allowed_blade_counts"]:
            raise ValueError(f"Blade count Z={z} not in allowed set {BLADE_COUNT_SPECS['allowed_blade_counts']}")
        return True

    def validate_model_path(self, path: Path | str) -> bool:
        """Validate model file existence."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Model file not found: {p}")
        return True

    def validate_copy_on_write(self, src: Path | str, dst: Path | str) -> bool:
        """Enforce strict Copy-on-Write: destination cannot equal baseline source."""
        p_src = Path(src).resolve()
        p_dst = Path(dst).resolve()
        if p_src == p_dst:
            raise ValueError("Copy-on-Write violation: destination cannot equal baseline source")
        return True

    def validate_physics_node(self, tag: str, active_tags: list[str]) -> bool:
        """Validate Java API model physics node existence."""
        if tag not in active_tags:
            raise KeyError(f"NODE_NOT_FOUND: Node '{tag}' does not exist")
        return True

    def validate_solver_convergence(self, residual: float, tolerance: float = 0.005) -> bool:
        """Validate solver convergence residual against tolerance."""
        if residual > tolerance:
            raise RuntimeError(f"Solver diverged: relative residual {residual} exceeds tolerance {tolerance}")
        return True

    def validate_solved_output(self, output_path: Path | str) -> bool:
        """Validate existence of solved simulation output file."""
        p = Path(output_path)
        if not p.is_file():
            raise FileNotFoundError(f"Solved output missing: {p}")
        return True


@pytest.fixture(scope="session")
def motor_power_envelope() -> dict[str, Any]:
    """Session fixture for 13 kW rated motor power envelope."""
    return MOTOR_POWER_ENVELOPE


@pytest.fixture(scope="session")
def blade_count_specs() -> dict[str, Any]:
    """Session fixture for flexible blade count Z in {3, 4, 5} specs."""
    return BLADE_COUNT_SPECS


@pytest.fixture(scope="session")
def comsol_env() -> dict[str, Any]:
    """Session fixture returning COMSOL environment info."""
    return get_comsol_environment()


@pytest.fixture(scope="session")
def physical_invariants() -> dict[str, Any]:
    """Session fixture for frozen physical invariants."""
    return PHYSICAL_INVARIANTS


@pytest.fixture(scope="session")
def material_al6061() -> dict[str, Any]:
    """Session fixture for Aluminum 6061-T6 material properties."""
    return AL_6061_T6


@pytest.fixture(scope="session")
def baseline_benchmarks() -> dict[str, Any]:
    """Session fixture for baseline performance benchmarks."""
    return BASELINE_BENCHMARKS


@pytest.fixture(scope="session")
def comsol_native_spec() -> dict[str, Any]:
    """Session fixture for native COMSOL 6.4 API specifications."""
    return COMSOL_NATIVE_SPEC


@pytest.fixture(scope="session")
def analytical_validator() -> AnalyticalHydroStructuralValidator:
    """Session fixture providing analytical hydro-structural validation calculations."""
    return AnalyticalHydroStructuralValidator()


@pytest.fixture
def nominal_propeller_dofs() -> dict[str, float]:
    """Nominal valid 13-DOF propeller vector adhering to all invariants."""
    return {
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
        "tip_clearance_m": 0.00193,  # Nominal duct clearance 1.93 mm
        "duct_inlet_lip_ratio": 1.12,
        "duct_diffuser_angle_deg": 6.5,
    }
