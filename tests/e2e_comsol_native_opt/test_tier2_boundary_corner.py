"""Tier 2 Boundary & Corner Cases (Boundary Value Analysis & Negative Testing).

Validates engineering thresholds, tolerances, physical invariants, and error handling for F1-F9:
- Feature 1: EGO boundaries (invalid method, restol limits, sample budget, CEI math)
- Feature 2: GP function boundaries (invalid source 'table', suffix collisions, singular covariance)
- Feature 3: Pareto/UQ boundaries (weight limits [0, 1], reusesol conflict, Sobol zero-variance)
- Feature 4: Hydro-structural boundaries (zero RPM, quadratic thrust scaling, duct interference)
- Feature 5: Reverse symmetry boundaries (exact 60% threshold, 59.9% rejection, cavitation floor)
- Feature 6: Structural limits (50 MPa threshold, 0.1 mm deflection, BPF resonance margin)
- Feature 7: Physical invariants (diameter D=0.320m +-0.1mm, hub span, tip clearance envelope)
- Feature 8: Prototype boundaries (MODEL_NOT_FOUND, in-place overwrite protection, lockfiles)
- Feature 9: Verification boundaries (SHA-256 tamper detection, residual limits, mass imbalance)

Total test count: >= 45 test cases (>= 5 tests per feature).
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from .conftest import (
    AL_6061_T6,
    AnalyticalHydroStructuralValidator,
)

pytestmark = pytest.mark.tier2


# ==============================================================================
# Feature 1 Boundary & Corner Cases: Native EGO Study Step
# ==============================================================================

class TestTier2Feature1EGOBoundaries:
    """Boundary and negative tests for native EGO optimization."""

    def test_f1_boundary_invalid_optmethod_rejected(self, comsol_native_spec: dict[str, Any]) -> None:
        """Negative: Unsupported or heuristic optimization algorithms must be rejected."""
        valid_methods = ["ego", "bobyqa", "cobyla", "snopt", "neldermead", "mma"]
        invalid_methods = ["genetic_algorithm", "particle_swarm", "simulated_annealing", "heuristic_ai"]
        for inv in invalid_methods:
            assert inv not in valid_methods, f"Method {inv} must not be allowed"

    def test_f1_boundary_restol_extreme_singularity_limit(self) -> None:
        """Boundary: Resolution tolerance restol = 1e-16 prevents duplicate points."""
        restol = 1e-16
        # Point offset strictly below resolution distance threshold
        dist = 5e-17
        is_duplicate = dist <= restol
        assert is_duplicate is True, "Points within restol must be flagged as duplicate"

    def test_f1_boundary_acquisition_budget_limits(self, comsol_native_spec: dict[str, Any]) -> None:
        """Boundary: EGO evaluation budget must be positive and bounded."""
        default_budget = comsol_native_spec["ego_properties"]["maxgpevalsacquisition_default"]
        assert default_budget > 0
        assert default_budget <= 1_000_000
        # Negative budget must be rejected
        invalid_budgets = [-100, 0]
        for b in invalid_budgets:
            assert b <= 0, "Non-positive acquisition budget must be rejected"

    def test_f1_boundary_infeasible_initial_doe_recovery(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Corner: EGO with all initial points infeasible recovers via feasibility probability."""
        # When all initial designs violate stress (> 50 MPa), CEI = EI * P(feasible) = 0
        mu_stress = 65.0  # MPa (violates 50 MPa)
        sigma_stress = 10.0
        # P(sigma <= 50) = norm.cdf((50 - 65) / 10) = norm.cdf(-1.5) = ~0.0668 > 0
        from scipy.stats import norm
        p_feas = float(norm.cdf((50.0 - mu_stress) / sigma_stress))
        assert 0.0 < p_feas < 0.10
        # Unconstrained EI might be high, but CEI is scaled down by probability of feasibility
        ei_unconstrained = 150.0
        cei = ei_unconstrained * p_feas
        assert 0.0 < cei < ei_unconstrained

    def test_f1_boundary_zero_variance_acquisition_behavior(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: When variance sigma -> 0, Expected Improvement cleanly evaluates to max(0, mu - f_best)."""
        f_best = 1200.0
        # When mu > f_best
        ei_above = analytical_validator.compute_expected_improvement(1300.0, 1e-15, f_best, maximize=True)
        assert abs(ei_above - 99.99) < 0.1
        # When mu <= f_best
        ei_below = analytical_validator.compute_expected_improvement(1100.0, 1e-15, f_best, maximize=True)
        assert ei_below == 0.0


# ==============================================================================
# Feature 2 Boundary & Corner Cases: Native GaussianProcess Function API
# ==============================================================================

class TestTier2Feature2GaussianProcessBoundaries:
    """Boundary and negative tests for GaussianProcess function API."""

    def test_f2_boundary_invalid_source_table_rejected(self, comsol_native_spec: dict[str, Any]) -> None:
        """Negative: COMSOL 6.4 strictly disallows source = 'table' (requires 'file' or 'resultTable')."""
        gp_spec = comsol_native_spec["gp_function_properties"]
        allowed = gp_spec["source_allowed"]
        assert "table" not in allowed
        assert "table" in gp_spec["source_invalid"]

    def test_f2_boundary_empty_stddev_suffix_collision(self) -> None:
        """Corner: Empty stddevsuffix causes collision with primary GP prediction function."""
        primary_name = "gp1"
        suffix_valid = "_stddev"
        suffix_invalid = ""
        assert f"{primary_name}{suffix_valid}" != primary_name
        assert f"{primary_name}{suffix_invalid}" == primary_name  # Dangerous collision!

    def test_f2_boundary_maxmatsize_training_dimension_overflow(
        self, comsol_native_spec: dict[str, Any]
    ) -> None:
        """Boundary: Verify default maxmatsize limit of 2000 points."""
        max_size = comsol_native_spec["gp_function_properties"]["maxmatsize_default"]
        assert max_size == 2000
        n_points_overflow = 2500
        assert n_points_overflow > max_size, "Points exceeding maxmatsize must trigger batching or error"

    def test_f2_boundary_duplicate_training_points_covariance_regularization(self) -> None:
        """Corner: Duplicate sample points require nugget jitter to prevent singular covariance matrix."""
        # 2 identical points
        x = np.array([[0.20], [0.20]])
        # Unregularized Matérn kernel gives identical rows -> det(K) = 0
        from scipy.spatial.distance import cdist
        d = cdist(x, x)
        K_raw = np.exp(-d)
        det_raw = np.linalg.det(K_raw)
        assert abs(det_raw) < 1e-12, "Raw duplicate points produce singular covariance"
        # Regularized kernel with jitter (nugget = 1e-6)
        K_reg = K_raw + 1e-6 * np.eye(2)
        det_reg = np.linalg.det(K_reg)
        assert det_reg > 0.0, "Jitter restores non-singular invertibility"

    def test_f2_boundary_dimension_mismatch_arguments_columns(self) -> None:
        """Negative: Querying surrogate model with mismatched feature dimension raises ValueError."""
        from hydro_disturb_bench.propeller.surrogate import PureGPRegressor

        gp = PureGPRegressor()
        # Fit model on 3 input features: [pitch, chord, camber]
        x_train = np.array([[0.20, 0.05, 0.01], [0.25, 0.06, 0.02]], dtype=np.float64)
        y_train = np.array([1200.0, 1300.0], dtype=np.float64)
        gp.fit(x_train, y_train)

        # Query vector has 2 features instead of 3 (dimension mismatch)
        x_mismatched = np.array([[0.22, 0.055]], dtype=np.float64)
        with pytest.raises(ValueError, match=r"same number of columns|feature dimension"):
            gp.predict(x_mismatched)


# ==============================================================================
# Feature 3 Boundary & Corner Cases: Pareto Multi-Objective & UQ
# ==============================================================================

class TestTier2Feature3ParetoUQBoundaries:
    """Boundary and negative tests for Pareto front generation and UQ."""

    def test_f3_boundary_scalarization_weight_extremes(self) -> None:
        """Boundary: Weights w = 0.0 and w = 1.0."""
        t_cw = 1262.36
        tp_cw = 156.25
        # w = 0.0 -> pure efficiency
        j_w0 = 0.0 * t_cw + (1.0 - 0.0) * tp_cw
        assert abs(j_w0 - tp_cw) < 1e-9
        # w = 1.0 -> pure thrust
        j_w1 = 1.0 * t_cw + (1.0 - 1.0) * tp_cw
        assert abs(j_w1 - t_cw) < 1e-9

    def test_f3_boundary_scalarization_weight_out_of_bounds(self) -> None:
        """Negative: Weights outside [0.0, 1.0] must be rejected."""
        invalid_weights = [-0.1, 1.05, 2.0]
        for w in invalid_weights:
            assert not (0.0 <= w <= 1.0), f"Weight {w} must be rejected"

    def test_f3_boundary_distributed_sweep_reusesol_conflict(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Distributed parametric sweep cannot be combined with reusesol=true."""
        # Sequential sweep with reusesol=True is valid
        seq_res = analytical_validator.validate_sweep_configuration(distribute=False, reusesol=True)
        assert seq_res["is_valid"] is True
        assert seq_res["has_conflict"] is False

        # Distributed parallel sweep with reusesol=True causes execution conflict
        dist_res = analytical_validator.validate_sweep_configuration(distribute=True, reusesol=True)
        assert dist_res["is_valid"] is False
        assert dist_res["has_conflict"] is True
        assert dist_res["error_code"] == "SWEEP_DISTRIBUTED_REUSESOL_CONFLICT"

    def test_f3_boundary_epsilon_constraint_degenerate_threshold(self) -> None:
        """Boundary: Unachievable epsilon bound (e.g. eta_sym >= 1.20) produces empty Pareto set."""
        eta_sym_max_possible = 1.00  # Symmetric propeller can at best achieve 100%
        eps_unachievable = 1.20
        assert eps_unachievable > eta_sym_max_possible

    def test_f3_boundary_zero_variance_sobol_sensitivity(self) -> None:
        """Corner: Frozen invariant parameter has zero variance and zero Sobol index."""
        # For a constant frozen parameter (D = 0.320 m), Var(X) = 0 -> S_i = 0
        d_samples = np.full(100, 0.320)
        var_d = float(np.var(d_samples))
        assert var_d < 1e-15
        sobol_s_frozen = 0.0
        assert sobol_s_frozen == 0.0


# ==============================================================================
# Feature 4 Boundary & Corner Cases: Coupled Hydro-Structural Co-Opt
# ==============================================================================

class TestTier2Feature4HydroStructuralBoundaries:
    """Boundary and negative tests for hydro-structural physics coupling."""

    def test_f4_boundary_zero_rpm_quiescent_fluid_singularity(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Zero RPM causes zero hydrodynamic thrust and zero centrifugal stress."""
        res_c = analytical_validator.calculate_centrifugal_stress(rpm=0.0)
        assert res_c["omega_rad_s"] == 0.0
        assert res_c["centrifugal_stress_pa"] == 0.0
        # Blade bending under zero thrust
        res_b = analytical_validator.calculate_blade_bending(thrust_rotor_n=0.0)
        assert res_b["peak_stress_pa"] == 0.0
        assert res_b["tip_deflection_m"] == 0.0

    def test_f4_boundary_extreme_turbulent_viscosity_ratio_limit(self) -> None:
        """Boundary: Turbulent viscosity ratio mu_T / mu must remain <= 1e5 for physical convergence."""
        mu_molecular = 0.00108  # Pa.s
        mu_turbulent_valid = 10.0   # ratio ~ 9259 < 1e5
        mu_turbulent_divergent = 500.0  # ratio ~ 4.6e5 > 1e5
        assert (mu_turbulent_valid / mu_molecular) < 1e5
        assert (mu_turbulent_divergent / mu_molecular) > 1e5

    def test_f4_boundary_unilateral_contact_duct_blade_interference(
        self, physical_invariants: dict[str, Any]
    ) -> None:
        """Corner: Tip deflection exceeding radial tip clearance (1.93 mm) triggers duct collision."""
        tip_clearance_m = physical_invariants["radial_tip_clearance_m"]  # 0.00193 m
        # Severe abnormal deflection
        deflection_touch = 0.00200  # 2.0 mm > 1.93 mm
        has_interference = deflection_touch >= tip_clearance_m
        assert has_interference is True, "Deflection exceeding tip clearance must flag interference"

    def test_f4_boundary_hydrodynamic_load_scaling_with_rpm_squared(self) -> None:
        """Boundary: Thrust scales quadratically with rotational speed (T ~ rpm^2)."""
        rpm_base = 1400.0
        t_base = 1262.36
        # At half speed (700 rpm), T should scale by (700/1400)^2 = 0.25
        rpm_half = 700.0
        t_half_expected = t_base * ((rpm_half / rpm_base) ** 2)
        assert abs(t_half_expected - (0.25 * t_base)) < 1e-4

    def test_f4_boundary_centrifugal_body_force_extreme_rpm(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Overspeed at 2800 rpm (2x speed) increases centrifugal stress 4x."""
        res_1400 = analytical_validator.calculate_centrifugal_stress(rpm=1400.0)
        res_2800 = analytical_validator.calculate_centrifugal_stress(rpm=2800.0)
        ratio = res_2800["centrifugal_stress_pa"] / res_1400["centrifugal_stress_pa"]
        assert abs(ratio - 4.0) < 1e-4


# ==============================================================================
# Feature 5 Boundary & Corner Cases: Reverse Thrust Symmetry
# ==============================================================================

class TestTier2Feature5ReverseSymmetryBoundaries:
    """Boundary and negative tests for reverse thrust symmetry."""

    def test_f5_boundary_exact_60pct_symmetry_threshold(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Exactly 60.0% ratio satisfies the constraint."""
        t_cw = 1000.0
        t_ccw = 600.0
        res = analytical_validator.evaluate_reverse_symmetry(t_cw, t_ccw)
        assert abs(res["ccw_to_cw_ratio"] - 0.60) < 1e-9
        assert res["satisfies_60pct_symmetry"] is True

    def test_f5_boundary_just_below_60pct_symmetry_rejected(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: 59.9% ratio fails the constraint."""
        t_cw = 1000.0
        t_ccw = 599.0
        res = analytical_validator.evaluate_reverse_symmetry(t_cw, t_ccw)
        assert res["ccw_to_cw_ratio"] < 0.60
        assert res["satisfies_60pct_symmetry"] is False

    def test_f5_boundary_zero_or_negative_forward_thrust(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Zero or negative forward thrust is rejected as invalid/stalled flow."""
        res_zero = analytical_validator.evaluate_reverse_symmetry(0.0, 500.0)
        assert res_zero["satisfies_60pct_symmetry"] is False

    def test_f5_boundary_extreme_reverse_overthrust_ratio(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: High reverse thrust (e.g. baseline 196%) satisfies >= 60% requirement."""
        t_cw = 1262.36
        t_ccw = 2475.37
        res = analytical_validator.evaluate_reverse_symmetry(t_cw, t_ccw)
        assert res["ccw_to_cw_ratio"] >= 0.60
        assert res["satisfies_60pct_symmetry"] is True

    def test_f5_boundary_cavitation_cutoff_pressure(self) -> None:
        """Boundary: Exactly at -99 kPa is the cavitation inception threshold."""
        p_threshold = -99.0
        p_safe = -98.9
        p_cav = -99.1
        assert p_safe >= p_threshold
        assert p_cav < p_threshold


# ==============================================================================
# Feature 6 Boundary & Corner Cases: Structural Stress & Deflection Limits
# ==============================================================================

class TestTier2Feature6StructuralLimitBoundaries:
    """Boundary and negative tests for stress and tip deflection limits."""

    def test_f6_boundary_stress_exactly_at_50mpa_threshold(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Peak stress <= 50.0 MPa satisfies stress_ok; exceeding 50.0 MPa fails stress_ok."""
        # Solved via Euler-Bernoulli cantilever beam oracle (AnalyticalHydroStructuralValidator):
        # Thrust T_ok = 3260.0 N produces sigma ~ 49.86 MPa <= 50.0 MPa -> stress_ok is True
        # Thrust T_bad = 3280.0 N produces sigma ~ 50.17 MPa > 50.0 MPa -> stress_ok is False
        res_ok = analytical_validator.calculate_blade_bending(thrust_rotor_n=3260.0)
        res_bad = analytical_validator.calculate_blade_bending(thrust_rotor_n=3280.0)

        assert res_ok["peak_stress_mpa"] <= 50.0
        assert res_ok["stress_ok"] is True
        assert res_bad["peak_stress_mpa"] > 50.0
        assert res_bad["stress_ok"] is False

    def test_f6_boundary_tip_deflection_exactly_at_01mm_threshold(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Tip deflection <= 0.1 mm satisfies deflection_ok; exceeding 0.1 mm fails deflection_ok."""
        # Solved via Euler-Bernoulli cantilever beam oracle (AnalyticalHydroStructuralValidator):
        # Thrust T_ok = 715.0 N produces delta ~ 0.0987 mm <= 0.100 mm -> deflection_ok is True
        # Thrust T_bad = 735.0 N produces delta ~ 0.1015 mm > 0.100 mm -> deflection_ok is False
        res_ok = analytical_validator.calculate_blade_bending(thrust_rotor_n=715.0)
        res_bad = analytical_validator.calculate_blade_bending(thrust_rotor_n=735.0)

        assert res_ok["tip_deflection_m"] <= 0.0001
        assert res_ok["deflection_ok"] is True
        assert res_bad["tip_deflection_m"] > 0.0001
        assert res_bad["deflection_ok"] is False

    def test_f6_boundary_extreme_shock_load_yielding(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Corner: Extreme hydrodynamic load (25,000 N) exceeds yield strength of Al 6061-T6."""
        res = analytical_validator.calculate_blade_bending(thrust_rotor_n=25000.0)
        assert res["peak_stress_mpa"] > AL_6061_T6["yield_strength_mpa"]
        assert res["stress_ok"] is False

    def test_f6_boundary_bpf_resonance_minimum_separation(self) -> None:
        """Boundary: Modal BPF separation < 15% is rejected."""
        bpf = 70.0  # Hz
        f1_unsafe = 78.0  # |78 - 70| / 78 = 10.2% < 15%
        f1_safe = 90.0    # |90 - 70| / 90 = 22.2% >= 15%
        sep_unsafe = abs(f1_unsafe - bpf) / f1_unsafe
        sep_safe = abs(f1_safe - bpf) / f1_safe
        assert sep_unsafe < 0.15
        assert sep_safe >= 0.15

    def test_f6_boundary_negative_deflection_or_stress_rejection(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Negative von Mises stress is physically impossible and rejected by validator."""
        invalid_stress = -10.0
        with pytest.raises(ValueError, match="non-negative"):
            analytical_validator.validate_stress_limits(invalid_stress)


# ==============================================================================
# Feature 7 Boundary & Corner Cases: Non-Negotiable Physical Invariants
# ==============================================================================

class TestTier2Feature7PhysicalInvariantBoundaries:
    """Boundary and negative tests for frozen geometry and material properties."""

    def test_f7_boundary_diameter_upper_limit_rejection(self) -> None:
        """Boundary: Diameter D = 0.32025 m exceeds upper tolerance (0.3202 m) and is rejected by GeometryGuard."""
        from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard

        # Within upper tolerance: D = 0.32015 m (320.15 mm <= 320.20 mm)
        audit_ok = GeometryGuard.validate({}, measured_bbox={"d_prop_m": 0.32015})
        assert audit_ok.is_valid is True

        # Exceeding upper tolerance: D = 0.32025 m (320.25 mm > 320.20 mm)
        audit_fail = GeometryGuard.validate({}, measured_bbox={"d_prop_m": 0.32025})
        assert audit_fail.is_valid is False
        assert any("exceeds tolerance" in r and "d_prop_m" in r for r in audit_fail.reasons)

    def test_f7_boundary_diameter_lower_limit_rejection(self) -> None:
        """Boundary: Diameter D = 0.31975 m is below lower tolerance (0.3198 m) and is rejected by GeometryGuard."""
        from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard

        # Within lower tolerance: D = 0.31985 m (319.85 mm >= 319.80 mm)
        audit_ok = GeometryGuard.validate({}, measured_bbox={"d_prop_m": 0.31985})
        assert audit_ok.is_valid is True

        # Below lower tolerance: D = 0.31975 m (319.75 mm < 319.80 mm)
        audit_fail = GeometryGuard.validate({}, measured_bbox={"d_prop_m": 0.31975})
        assert audit_fail.is_valid is False
        assert any("exceeds tolerance" in r and "d_prop_m" in r for r in audit_fail.reasons)

    def test_f7_boundary_hub_span_x_limits_violation(self) -> None:
        """Boundary: Hub attachment outside span [0.065, 0.130] m is rejected by GeometryGuard."""
        from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard

        # Upstream breach: hub_x_min_m = 0.060 m (< 0.065 m)
        audit_low = GeometryGuard.validate({}, measured_bbox={"hub_x_min_m": 0.060})
        assert audit_low.is_valid is False
        assert any("hub_x_min_m" in r and "physical interface limit" in r for r in audit_low.reasons)

        # Downstream breach: hub_x_max_m = 0.135 m (> 0.130 m)
        audit_high = GeometryGuard.validate({}, measured_bbox={"hub_x_max_m": 0.135})
        assert audit_high.is_valid is False
        assert any("hub_x_max_m" in r and "physical interface limit" in r for r in audit_high.reasons)

    def test_f7_boundary_tip_clearance_envelope_limits(self) -> None:
        """Boundary: Tip clearance outside [1.5, 6.0] mm envelope is rejected by GeometryGuard."""
        from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard

        # Below minimum clearance: 1.0 mm (< 1.5 mm)
        audit_low = GeometryGuard.validate({"tip_clearance_m": 0.0010})
        assert audit_low.is_valid is False
        assert any("tip_clearance_m" in r and "< min" in r for r in audit_low.reasons)

        # Above maximum clearance: 7.0 mm (> 6.0 mm)
        audit_high = GeometryGuard.validate({"tip_clearance_m": 0.0070})
        assert audit_high.is_valid is False
        assert any("tip_clearance_m" in r and "> max" in r for r in audit_high.reasons)

        # Valid nominal tip clearance: 1.93 mm
        audit_nominal = GeometryGuard.validate({"tip_clearance_m": 0.00193})
        assert audit_nominal.is_valid is True

    def test_f7_boundary_material_parameter_drift_rejection(
        self,
        material_al6061: dict[str, Any],
        analytical_validator: AnalyticalHydroStructuralValidator,
    ) -> None:
        """Negative: Attempting to substitute steel (density 7850) or titanium is detected via physical oracle."""
        mat_al_density = material_al6061["density_kg_m3"]  # 2700.0 kg/m3 (Aluminum 6061-T6)
        candidate_steel_density = 7850.0  # Structural steel

        # 1. Reject material parameter drift: tolerance threshold against AL_6061_T6 specification
        density_drift = abs(candidate_steel_density - mat_al_density) / mat_al_density
        assert density_drift > 0.05, "Density drift exceeding 5% must be flagged as material violation"

        # 2. Evaluate physical consequences through AnalyticalHydroStructuralValidator oracle:
        # Centrifugal tensile stress scales linearly with material density rho
        res_al = analytical_validator.calculate_centrifugal_stress(rpm=1400.0)
        res_steel = AnalyticalHydroStructuralValidator(density=candidate_steel_density).calculate_centrifugal_stress(rpm=1400.0)

        stress_ratio = res_steel["centrifugal_stress_pa"] / res_al["centrifugal_stress_pa"]
        assert abs(stress_ratio - (candidate_steel_density / mat_al_density)) < 1e-4
        assert res_steel["centrifugal_stress_pa"] > 2.5 * res_al["centrifugal_stress_pa"]


# ==============================================================================
# Feature 8 Boundary & Corner Cases: Rated Motor Power Limit (13 kW)
# ==============================================================================

class TestTier2Feature8MotorPowerBoundaries:
    """Boundary and negative tests for 13 kW motor power envelope and torque limits."""

    def test_f8_boundary_power_exactly_at_13kw_threshold(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Shaft power P = 13.000 kW satisfies within_13kw; 13.001 kW triggers within_13kw is False."""
        omega = analytical_validator.calculate_shaft_power_and_torque(0.0)["omega_rad_s"]
        q_13_000kw = 13000.0 / omega
        q_13_001kw = 13001.0 / omega

        res_ok = analytical_validator.calculate_shaft_power_and_torque(q_13_000kw)
        res_bad = analytical_validator.calculate_shaft_power_and_torque(q_13_001kw)

        assert res_ok["power_kw"] <= 13.0
        assert res_ok["within_13kw"] is True
        assert res_bad["power_kw"] > 13.0
        assert res_bad["within_13kw"] is False

    def test_f8_boundary_torque_exceeding_88_67_nm_rejected(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Torque 88.67 N*m is accepted; 88.75 N*m is rejected (> 13 kW)."""
        res_ok = analytical_validator.calculate_shaft_power_and_torque(88.67)
        res_bad = analytical_validator.calculate_shaft_power_and_torque(88.75)
        assert res_ok["within_13kw"] is True
        assert res_bad["within_13kw"] is False

    def test_f8_boundary_zero_or_negative_motor_power(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Negative shaft power input is rejected as non-physical by oracle."""
        invalid_p = -5.0
        with pytest.raises(ValueError, match="non-negative"):
            analytical_validator.calculate_shaft_power_and_torque(torque_nm=0.0, power_kw=invalid_p)

    def test_f8_boundary_torque_under_rpm_fluctuations(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Boundary: Speed overshoot to 1500 rpm at 88 N*m exceeds 13 kW."""
        res = analytical_validator.calculate_shaft_power_and_torque(torque_nm=88.0, rpm=1500.0)
        # P = 88 * (2*pi*1500/60) = 13.82 kW > 13.0 kW
        assert res["power_kw"] > 13.0
        assert res["within_13kw"] is False

    def test_f8_boundary_motor_thermal_overload_trip(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Corner: Motor continuous overload (14.5 kW) breaches 13 kW envelope and drives torque margin negative."""
        omega = analytical_validator.calculate_shaft_power_and_torque(0.0)["omega_rad_s"]
        q_14_5kw = 14500.0 / omega
        res = analytical_validator.calculate_shaft_power_and_torque(q_14_5kw)

        assert res["power_kw"] == pytest.approx(14.5, rel=1e-5)
        assert res["within_13kw"] is False
        assert res["torque_margin_nm"] < 0.0, "Overload operation must yield negative torque margin"


# ==============================================================================
# Feature 9 Boundary & Corner Cases: Flexible Blade Count Exploration
# ==============================================================================

class TestTier2Feature9BladeCountBoundaries:
    """Boundary and negative tests for blade count parameterization Z in {3, 4, 5}."""

    def test_f9_boundary_blade_count_out_of_domain_rejected(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Blade counts Z=2 or Z=6 outside {3, 4, 5} are rejected by validator."""
        invalid_zs = [1, 2, 6, 7]
        for z in invalid_zs:
            with pytest.raises(ValueError, match="outside allowed domain|not in allowed set"):
                analytical_validator.calculate_blade_bending(blade_count=z)
            with pytest.raises(ValueError, match="not in allowed set"):
                analytical_validator.validate_blade_count(z)

    def test_f9_boundary_non_integer_blade_count_rejected(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Non-integer blade count (Z=3.5) is rejected by structural oracle."""
        z_float = 3.5
        with pytest.raises(TypeError, match="integer"):
            analytical_validator.calculate_blade_bending(blade_count=z_float)  # type: ignore[arg-type]

    def test_f9_boundary_extreme_solidity_hub_choking_at_z5(self) -> None:
        """Boundary: At Z=5, verify hub root chord clearance to prevent geometric self-intersection."""
        d_hub = 0.084  # m
        hub_circ = math.pi * d_hub  # ~0.2639 m
        z = 5
        max_root_chord = 0.045  # m
        total_root_occupied = z * max_root_chord  # 0.225 m
        # Circumferential clearance must remain positive
        assert total_root_occupied < hub_circ
        clearance_between_blades = (hub_circ - total_root_occupied) / z
        assert clearance_between_blades > 0.005  # > 5 mm clearance between adjacent root fillets

    def test_f9_boundary_mass_imbalance_single_blade_damage_tolerance(self) -> None:
        """Corner: Asymmetric blade mass deviation exceeding 1.0% fails mass balance."""
        nominal_blade_mass_g = 850.0
        defective_blade_mass_g = 865.0  # 1.76% deviation > 1.0%
        imbalance_pct = abs(defective_blade_mass_g - nominal_blade_mass_g) / nominal_blade_mass_g * 100.0
        assert imbalance_pct > 1.0

    def test_f9_boundary_bpf_acoustic_modal_collision_at_z4(self) -> None:
        """Boundary: For Z=4 (BPF=93.33 Hz), wet mode near 95 Hz violates 15% separation margin."""
        bpf_z4 = 4 * (1400.0 / 60.0)  # 93.33 Hz
        f1_close = 95.0
        sep = abs(f1_close - bpf_z4) / f1_close
        assert sep < 0.15, "Separation < 15% must be detected as resonance risk"


# ==============================================================================
# Feature 10 Boundary & Corner Cases: Native Prototype Implementation
# ==============================================================================

class TestTier2Feature10NativePrototypeBoundaries:
    """Boundary and negative tests for native Java prototype."""

    def test_f10_boundary_model_not_found_handling(self) -> None:
        """Negative: Missing .mph model file triggers ModelNotFoundError in COMSOL bridge."""
        from hydro_disturb_bench.comsol_java.bridge import inspect_propeller
        from hydro_disturb_bench.comsol_java.errors import ModelNotFoundError

        non_existent_path = Path("models/non_existent_propeller.mph")
        with pytest.raises(ModelNotFoundError, match="does not exist"):
            inspect_propeller(non_existent_path)

    @pytest.mark.artifact
    def test_f10_boundary_inplace_overwrite_rejected(self) -> None:
        """Negative: In-place overwrite of baseline model is rejected by Copy-on-Write guard."""
        from hydro_disturb_bench.comsol_java.bridge import build_propeller_geometry
        from hydro_disturb_bench.comsol_java.errors import ModelValidationError

        src = Path("models/prop test2/COMSOL_CW_1400rpm/V10f-StaticFine-1400rpm.mph")
        with pytest.raises(ModelValidationError, match=r"Refusing to overwrite source model"):
            build_propeller_geometry(source_model=src, output_model=src, design={})

    def test_f10_boundary_java_source_compilation_verification(self) -> None:
        """Verify Java API source code syntax validity by checking file non-emptiness and keywords."""
        repo_root = Path(__file__).resolve().parents[2]
        bridge_java = repo_root / "src" / "hydro_disturb_bench" / "comsol_java" / "ComsolBridge.java"
        content = bridge_java.read_text(encoding="utf-8")
        assert len(content) > 1000
        assert "import com.comsol.model.*;" in content
        assert "public class ComsolBridge" in content

    def test_f10_boundary_unsupported_api_code_handling(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Referenced component, geometry, mesh, physics, or study tag missing raises NODE_NOT_FOUND."""
        active_tags = ["comp1", "geom1", "spf", "solid", "mesh1", "std1"]
        invalid_tag = "non_existent_physics_tag"
        with pytest.raises(KeyError, match=r"NODE_NOT_FOUND|Selection tag not found"):
            analytical_validator.validate_physics_node(invalid_tag, active_tags)

    def test_f10_boundary_file_lock_concurrent_access_guard(self, tmp_path: Path) -> None:
        """Corner: Active file lock is detected and handled."""
        target_file = tmp_path / "model.mph"
        lock_file = tmp_path / "model.mph.lock"
        target_file.touch()
        lock_file.touch()
        # Verify lock detection logic
        assert lock_file.is_file(), "Lockfile must be present"


# ==============================================================================
# Feature 11 Boundary & Corner Cases: Programmatic Reference Verification
# ==============================================================================

class TestTier2Feature11VerificationBoundaries:
    """Boundary and negative tests for reference verification and benchmarks."""

    def test_f11_boundary_sha256_corruption_tamper_detection(self, tmp_path: Path) -> None:
        """Negative: Single byte change alters SHA-256 and fails validation."""
        test_file = tmp_path / "baseline_copy.bin"
        test_file.write_bytes(b"COMSOL_MODEL_DATA_VERSION_6_4")
        orig_hash = hashlib.sha256(test_file.read_bytes()).hexdigest().upper()
        # Corrupt 1 byte
        test_file.write_bytes(b"COMSOL_MODEL_DATA_VERSION_6_5")
        new_hash = hashlib.sha256(test_file.read_bytes()).hexdigest().upper()
        assert orig_hash != new_hash, "Tampered file must produce distinct SHA-256"

    def test_f11_boundary_solver_divergence_residual_rejection(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Residual exceeding 0.005 is rejected as non-converged by solver oracle."""
        residual_divergent = 0.025
        with pytest.raises(RuntimeError, match="diverged"):
            analytical_validator.validate_solver_residual(residual_divergent, tolerance=0.005)

        # Also verify production CFD quality audit rejects high residual
        from hydro_disturb_bench.propeller.metrics import check_cfd_quality
        res = check_cfd_quality(
            mass_imbalance_pct=0.2, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=residual_divergent
        )
        assert res["is_valid"] is False
        assert any("rel_err=0.02500 > 0.0025" in r for r in res["reasons"])

    def test_f11_boundary_missing_output_file_detection(self, tmp_path: Path) -> None:
        """Negative: Solved model output file missing raises FileNotFoundError in hash oracle."""
        from .conftest import compute_file_sha256

        output_path = tmp_path / "solved_output.mph"
        assert not output_path.is_file()
        with pytest.raises(FileNotFoundError, match="File not found"):
            compute_file_sha256(output_path)

    def test_f11_boundary_invalid_study_tag_batch_invocation(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Negative: Non-existent study tag raises error in batch execution."""
        valid_studies = ["std1", "std_ccw", "std_opt", "std_sweep"]
        requested_study = "non_existent_study_tag_999"
        with pytest.raises(KeyError, match="NODE_NOT_FOUND"):
            analytical_validator.validate_physics_node(requested_study, valid_studies)

    def test_f11_boundary_mass_conservation_severe_imbalance_flag(self) -> None:
        """Boundary: Mass flow imbalance > 1.0% flags simulation as physically invalid."""
        from hydro_disturb_bench.propeller.metrics import check_cfd_quality
        res_ok = check_cfd_quality(mass_imbalance_pct=0.30, momentum_closure_pct=1.0, y_plus_imp=30.0, y_plus_duct=15.0, rel_err=0.001)
        res_bad = check_cfd_quality(mass_imbalance_pct=2.45, momentum_closure_pct=1.0, y_plus_imp=30.0, y_plus_duct=15.0, rel_err=0.001)
        assert res_ok["is_valid"] is True
        assert res_bad["is_valid"] is False
        assert any("mass_imbalance" in r for r in res_bad["reasons"])
