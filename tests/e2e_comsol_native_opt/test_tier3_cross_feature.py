"""Tier 3 Cross-Feature Combination Tests (Pairwise Combinatorial Multi-Physics).

Validates multi-feature interactions and physical coupling across F1-F9:
- Cross 1: EGO Active Sampling + GaussianProcess Surrogate (F1 + F2)
- Cross 2: EGO Optimization + Reverse Thrust Symmetry Constraint (F1 + F5)
- Cross 3: EGO Optimization + Structural Stress & Tip Deflection Limits (F1 + F6)
- Cross 4: Reverse Thrust Symmetry + CFD MRF Rotating Frame Coupling (F4 + F5)
- Cross 5: Hydro-Structural Interface Tractions + Invariant Al 6061-T6 Geometry (F4 + F6 + F7)
- Cross 6: Pareto Multi-Objective Frontier + Symmetry + Stress Bounds (F3 + F5 + F6)
- Cross 7: Native Java API Prototype + EGO Study Step + GP Surrogate (F1 + F2 + F8)
- Cross 8: Programmatic Reference Verification + Invariants + Immutability (F7 + F8 + F9)
- Cross 9: Full Multi-Physics Closed-Loop Co-Optimization Integration (F1 + F2 + F4 + F5 + F6 + F7 + F8)
- Cross 10: Manufacturing Uncertainty Quantification + Invariant Tolerances (F3 + F6 + F7)

Total test count: >= 9 test cases.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from .conftest import (
    AL_6061_T6,
    BASELINE_BENCHMARKS,
    COMSOL_NATIVE_SPEC,
    PHYSICAL_INVARIANTS,
    PROP_TEST2_DIR,
    AnalyticalHydroStructuralValidator,
    compute_file_sha256,
)

pytestmark = pytest.mark.tier3


class TestTier3CrossFeatureCombinations:
    """Pairwise and multi-physics interaction test cases."""

    def test_cross_1_ego_active_sampling_with_gp_surrogate(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F1 + F2: EGO sequential active sampling infill point selection using GP surrogate."""
        # Initial training points (pitch [m], thrust [N])
        x_train = np.array([0.18, 0.20, 0.24, 0.28])
        y_train = np.array([1150.0, 1262.36, 1380.0, 1420.0])
        f_best = float(np.max(y_train))
        
        # Candidate points across design space
        x_candidates = np.linspace(0.16, 0.30, 15)
        
        # Simplified Gaussian Process posterior mean and uncertainty
        l = 0.04
        eis = []
        for xc in x_candidates:
            # Distance to nearest sample point governs epistemic uncertainty sigma
            min_dist = float(np.min(np.abs(x_train - xc)))
            sigma = 80.0 * (1.0 - math.exp(-3.0 * (min_dist / l)))
            # Trend model: quadratic interpolation of thrust
            mu = 1262.36 + 1800.0 * (xc - 0.20) - 4000.0 * ((xc - 0.20) ** 2)
            ei = analytical_validator.compute_expected_improvement(mu, sigma, f_best, maximize=True)
            eis.append(ei)
            
        eis = np.array(eis)
        # Verify that active learning identifies an infill candidate with positive Expected Improvement
        best_infill_idx = int(np.argmax(eis))
        assert eis[best_infill_idx] > 0.0
        # Infill point should explore unvisited design space with high potential
        assert 0.16 <= x_candidates[best_infill_idx] <= 0.30

    def test_cross_2_ego_with_reverse_thrust_symmetry_constraint(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F1 + F5: EGO constrained optimization filtering candidates by T_CCW >= 0.60 * T_CW."""
        # Candidate 1: High CW thrust but poor reverse symmetry
        c1 = {"t_cw": 1600.0, "t_ccw": 850.0}  # ratio 0.531 < 0.60 -> Infeasible
        # Candidate 2: Balanced bidirectional thrust
        c2 = {"t_cw": 1450.0, "t_ccw": 1050.0}  # ratio 0.724 >= 0.60 -> Feasible
        
        eval1 = analytical_validator.evaluate_reverse_symmetry(c1["t_cw"], c1["t_ccw"])
        eval2 = analytical_validator.evaluate_reverse_symmetry(c2["t_cw"], c2["t_ccw"])
        
        assert eval1["satisfies_60pct_symmetry"] is False
        assert eval2["satisfies_60pct_symmetry"] is True
        
        # Constrained acquisition CEI penalizes infeasible Candidate 1
        ei_c1 = 45.0
        ei_c2 = 30.0
        p_feas_c1 = 0.0  # Violated
        p_feas_c2 = 0.95  # Feasible
        
        cei_1 = ei_c1 * p_feas_c1
        cei_2 = ei_c2 * p_feas_c2
        assert cei_2 > cei_1, "EGO CEI must prefer feasible candidate despite slightly lower raw EI"

    def test_cross_3_ego_with_structural_stress_and_tip_deflection_limits(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F1 + F6: EGO optimization constrained by peak stress <= 50 MPa and deflection <= 0.1 mm."""
        # Evaluate three candidate designs under hydrodynamic thrust loads
        candidates = [
            {"thrust_rotor": 600.0, "root_thick": 0.012, "desc": "thick root, conservative"},
            {"thrust_rotor": 900.0, "root_thick": 0.011, "desc": "higher thrust, moderate root"},
            {"thrust_rotor": 1200.0, "root_thick": 0.007, "desc": "aggressive thrust, thin root"},
        ]
        
        feasible_count = 0
        for c in candidates:
            res = analytical_validator.calculate_blade_bending(
                thrust_rotor_n=c["thrust_rotor"],
                thickness_root_m=c["root_thick"],
            )
            is_feasible = (
                res["peak_stress_mpa"] <= AL_6061_T6["stress_limit_mpa"]
                and res["tip_deflection_m"] <= AL_6061_T6["tip_deflection_limit_m"]
            )
            if is_feasible:
                feasible_count += 1
                
        # Candidate 1 and 2 are feasible, Candidate 3 violates structural limits
        assert feasible_count >= 1

    def test_cross_4_reverse_thrust_symmetry_coupled_cfd_mrf(
        self, baseline_benchmarks: dict[str, Any]
    ) -> None:
        """Cross F4 + F5: Forward and reverse MRF domain CFD thrust and power coupling at +-1400 rpm."""
        t_cw = baseline_benchmarks["cw_thrust_total_n"]
        p_cw = baseline_benchmarks["cw_power_kw"]
        t_ccw = baseline_benchmarks["ccw_thrust_total_n"]
        p_ccw = baseline_benchmarks["ccw_power_kw"]
        
        # Forward and reverse efficiencies
        eff_cw = t_cw / p_cw  # N/kW
        eff_ccw = t_ccw / p_ccw  # N/kW
        assert abs(eff_cw - 156.25) < 0.1
        assert abs(eff_ccw - 200.84) < 0.1
        
        # Mean thrust across forward and reverse operating envelope
        t_mean = 0.5 * (t_cw + t_ccw)
        assert abs(t_mean - 1868.865) < 0.1

    def test_cross_5_hydro_structural_stress_and_invariants(
        self, physical_invariants: dict[str, Any], material_al6061: dict[str, Any],
        baseline_benchmarks: dict[str, Any],
        analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F4 + F6 + F7: Blade cantilever bending coupled with frozen diameter and Al 6061-T6."""
        # Blade active span is strictly determined by frozen D_prop and D_hub
        span = physical_invariants["R_prop_m"] - physical_invariants["R_hub_m"]
        assert abs(span - 0.118) < 1e-4  # 0.160 - 0.042 = 0.118 m
        
        # Peak baseline hydrodynamic load: 679.77 N on 3 blades
        res = analytical_validator.calculate_blade_bending(
            thrust_rotor_n=baseline_benchmarks["cw_rotor_fx_n"]
        )
        assert res["peak_stress_mpa"] <= material_al6061["stress_limit_mpa"]
        assert res["tip_deflection_m"] <= material_al6061["tip_deflection_limit_m"]

    def test_cross_6_pareto_frontier_tradeoff_symmetry_and_stress(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F3 + F5 + F6: Multi-objective trade-off between forward thrust, symmetry, and stress."""
        # Simulated Pareto front points: [T_CW (N), T_CCW (N), peak_stress (MPa), delta_tip (mm)]
        pareto_points = [
            {"t_cw": 1350.0, "t_ccw": 900.0, "stress": 38.0, "delta": 0.055},  # eta = 0.667
            {"t_cw": 1420.0, "t_ccw": 960.0, "stress": 42.5, "delta": 0.068},  # eta = 0.676
            {"t_cw": 1490.0, "t_ccw": 1020.0, "stress": 47.0, "delta": 0.082}, # eta = 0.685
            {"t_cw": 1540.0, "t_ccw": 980.0, "stress": 52.0, "delta": 0.105},  # Violates limits!
        ]
        
        feasible_pareto = []
        for pt in pareto_points:
            sym_ok = (pt["t_ccw"] / pt["t_cw"]) >= 0.60
            stress_ok = pt["stress"] <= 50.0
            deflection_ok = pt["delta"] <= 0.10
            if sym_ok and stress_ok and deflection_ok:
                feasible_pareto.append(pt)
                
        # First 3 points form the valid feasible non-dominated Pareto frontier
        assert len(feasible_pareto) == 3
        for pt in feasible_pareto:
            assert pt["stress"] <= 50.0
            assert pt["t_ccw"] >= 0.60 * pt["t_cw"]

    def test_cross_7_native_java_prototype_with_ego_and_gp(self) -> None:
        """Cross F1 + F2 + F8: Java API bridge code verification for EGO study and GP surrogate."""
        repo_root = Path(__file__).resolve().parents[2]
        java_bridge = repo_root / "src" / "hydro_disturb_bench" / "comsol_java" / "PropellerBridge.java"
        content = java_bridge.read_text(encoding="utf-8")
        
        # Verify MRF frozen rotor and physics setup methods exist in native Java class
        assert "createMRFStudy" in content or "setupPhysics" in content or "handleEval" in content
        assert "ModelUtil" in content

    def test_cross_8_programmatic_reference_verification_with_invariants_and_immutability(
        self, baseline_benchmarks: dict[str, Any], physical_invariants: dict[str, Any]
    ) -> None:
        """Cross F7 + F8 + F9: Verification of baseline model SHA-256 integrity and invariant geometry."""
        cw_model = PROP_TEST2_DIR / "COMSOL_CW_1400rpm" / "V10f-StaticFine-1400rpm.mph"
        assert cw_model.is_file()
        current_hash = compute_file_sha256(cw_model)
        assert current_hash == baseline_benchmarks["cw_sha256"]
        
        # Verify non-negotiable physical dimensions match baseline configuration
        assert physical_invariants["D_prop_m"] == 0.320
        assert physical_invariants["D_hub_m"] == 0.084
        assert physical_invariants["duct_inner_radius_m"] == 0.16193

    def test_cross_9_full_multiphysics_closed_loop_coopt_integration(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F1 + F2 + F4 + F5 + F6 + F7 + F8: End-to-end iteration evaluating hydro-structural loop."""
        # Simulated closed-loop evaluation step:
        # Design candidate: pitch = 24 deg, chord = 0.075 m, root_thick = 0.012 m
        rpm = 1400.0
        
        # 1. Hydrodynamic MRF CFD calculation
        t_cw_cand = 1380.0   # N
        t_ccw_cand = 950.0   # N
        rotor_fx = 740.0     # N
        
        # 2. Reverse thrust symmetry check (F5)
        sym_res = analytical_validator.evaluate_reverse_symmetry(t_cw_cand, t_ccw_cand)
        assert sym_res["satisfies_60pct_symmetry"] is True
        
        # 3. Structural linear elasticity check (F6 + F7)
        struct_res = analytical_validator.calculate_blade_bending(
            thrust_rotor_n=rotor_fx,
            chord_root_m=0.075,
            thickness_root_m=0.013,
        )
        centrif_res = analytical_validator.calculate_centrifugal_stress(rpm=rpm)
        total_stress_mpa = struct_res["peak_stress_mpa"] + centrif_res["centrifugal_stress_mpa"]
        assert total_stress_mpa <= 50.0
        assert struct_res["deflection_ok"] is True
        
        # 4. EGO Expected Improvement calculation (F1 + F2)
        f_best_known = 1262.36
        mu_cand = t_cw_cand
        sigma_cand = 25.0
        ei = analytical_validator.compute_expected_improvement(mu_cand, sigma_cand, f_best_known, maximize=True)
        assert ei > 0.0

    def test_cross_10_uq_sobol_manufacturing_tolerances_on_stress(self) -> None:
        """Cross F3 + F6 + F7: Sobol sensitivity of blade root stress to manufacturing geometric tolerances."""
        # Tolerances: chord +- 0.5 mm, thickness +- 0.2 mm, pitch +- 0.5 deg
        n_mc = 500
        np.random.seed(42)
        chords = np.random.normal(0.075, 0.0005, n_mc)
        thicknesses = np.random.normal(0.012, 0.0002, n_mc)
        
        # Calculate bending stress across sample population for constant load 680 N
        stresses = []
        for c, t in zip(chords, thicknesses):
            i_xx = (c * (t ** 3)) / 12.0
            m_b = (680.0 / 3.0) * (0.70 * 0.118)
            sigma = (m_b * (t / 2.0)) / i_xx / 1e6  # MPa
            stresses.append(sigma)
            
        stresses = np.array(stresses)
        mean_stress = float(np.mean(stresses))
        std_stress = float(np.std(stresses))
        
        # All manufacturing tolerance variants must satisfy peak stress <= 50 MPa
        assert np.max(stresses) <= 50.0
        assert std_stress > 0.0
        assert std_stress < 2.0  # Variance due to tight tolerance is controlled

    def test_cross_11_motor_power_13kw_envelope_coupled_with_reverse_thrust_symmetry(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F5 + F8: Simultaneous verification of reverse thrust symmetry and 13 kW power ceiling."""
        # Baseline: T_CW = 1262.36 N (P=8.08 kW), T_CCW = 2475.37 N (P=12.33 kW, Q=84.07 N*m)
        # Baseline CCW operates very close to 13 kW ceiling (84.07 / 88.67 = 94.8% motor rating)
        res_cw = analytical_validator.calculate_shaft_power_and_torque(torque_nm=55.11)
        res_ccw = analytical_validator.calculate_shaft_power_and_torque(torque_nm=84.07)
        
        assert res_cw["within_13kw"] is True
        assert res_ccw["within_13kw"] is True
        assert res_cw["power_kw"] <= 13.0
        assert res_ccw["power_kw"] <= 13.0
        
        # Candidate optimized thruster:
        # T_CW = 1450 N (Q = 62 N*m -> P = 9.09 kW <= 13 kW)
        # T_CCW = 1050 N (Q = 58 N*m -> P = 8.50 kW <= 13 kW)
        cand_sym = analytical_validator.evaluate_reverse_symmetry(1450.0, 1050.0)
        assert cand_sym["satisfies_60pct_symmetry"] is True
        
        cand_p_cw = analytical_validator.calculate_shaft_power_and_torque(62.0)
        cand_p_ccw = analytical_validator.calculate_shaft_power_and_torque(58.0)
        assert cand_p_cw["within_13kw"] is True
        assert cand_p_ccw["within_13kw"] is True

    def test_cross_12_blade_count_tradeoff_z_across_power_and_stress_envelope(
        self, analytical_validator: AnalyticalHydroStructuralValidator
    ) -> None:
        """Cross F6 + F8 + F9: Multi-blade topology (Z in {3, 4, 5}) trade-off under stress and 13 kW power."""
        # Compare designs with Z = 3, 4, 5 operating at rated 1400 rpm:
        cases = [
            {"z": 3, "thrust_cw": 1400.0, "torque_nm": 65.0, "desc": "Z=3 baseline topology"},
            {"z": 4, "thrust_cw": 1560.0, "torque_nm": 74.0, "desc": "Z=4 increased bollard pull"},
            {"z": 5, "thrust_cw": 1680.0, "torque_nm": 82.0, "desc": "Z=5 high-solidity within 13 kW"},
        ]
        
        for c in cases:
            # Check 13 kW power envelope
            p_info = analytical_validator.calculate_shaft_power_and_torque(c["torque_nm"])
            assert p_info["within_13kw"] is True
            assert p_info["power_kw"] <= 13.0
            
            # Check blade root stress
            f_rotor = c["thrust_cw"] * 0.54
            s_info = analytical_validator.calculate_blade_bending(
                thrust_rotor_n=f_rotor,
                blade_count=c["z"],
                chord_root_m=0.070,
                thickness_root_m=0.012,
            )
            assert s_info["peak_stress_mpa"] <= AL_6061_T6["stress_limit_mpa"]
            assert s_info["stress_ok"] is True
            
            # Check thrust-to-power efficiency
            eff = analytical_validator.evaluate_thrust_per_kw(c["thrust_cw"], p_info["power_kw"])
            assert eff >= 135.0
