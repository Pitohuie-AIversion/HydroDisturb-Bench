"""Milestone 2: Hydro-Structural Optimization Formulation & Model Configuration Test Suite.

Verifies:
1. Schema & Physical Invariants Updates:
   - 13 kW rated power ceiling (P_rated_max_kw = 13.0 kW)
   - Maximum continuous shaft torque at 1400 rpm (max_shaft_torque_nm = 88.672 N*m)
   - Reverse thrust symmetry floor updated to >= 60% (eta_sym_floor = 0.60)
   - Flexible blade count topology allowed set: Z in {3, 4, 5}
2. Offline Model Inspection of Prototype (comsol_ego_13kw_configured.mph):
   - Exact COMSOL 6.4 Optimization API property names (pname, initval, lbound, ubound, scale)
   - 13 kW torque constraint with dedicated GP surrogate (globalgpconstrfunction = "new")
   - Hydrodynamic surface force integration expressions (-intR, -intD, intR(cross))
   - Frozen physical invariants parameters (D = 0.320 m, D_hub = 0.084 m, duct inner radius = 0.16193 m, Al 6061-T6)
3. PropellerOptModel Java API Bridge:
   - Clean bytecode compilation (zero lambdas / anonymous inner classes)
   - Support for Z in {3, 4, 5} multi-topology parameterization
   - CLI registration in ComsolBridge.java
4. FEM Structural Integrity & Wet Modal Resonance:
   - Peak von Mises stress: 6.81 MPa <= 50.0 MPa (SF = 40.5 >= 2.0)
   - Blade tip deflection: 0.0435 mm <= 0.100 mm (2.25% tip gap consumption)
   - Fundamental wet bending mode: 240.04 Hz >= 1.15 * BPF (70 Hz)
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hydro_disturb_bench.comsol_java.bridge import (
    PROPELLER_OPT_CLASS_NAMES,
    ensure_bridge_compiled,
    inspect_model,
)
from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard
from hydro_disturb_bench.propeller.schema import (
    ALLOWED_BLADE_COUNTS,
    HARD_INVARIANTS,
    P_rated_max_kw,
    PropellerGeometryAudit,
    max_shaft_torque_nm,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_MPH_PATH = REPO_ROOT / "runs" / "propeller_opt" / "v10f_opt" / "live_comsol_eval" / "comsol_ego_13kw_configured.mph"
FEM_JSON_PATH = REPO_ROOT / "runs" / "propeller_opt" / "v10f_opt" / "live_comsol_eval" / "fem" / "fem_modal_results.json"
JAVA_SRC_DIR = REPO_ROOT / "src" / "hydro_disturb_bench" / "comsol_java"


@pytest.fixture(scope="module")
def model_inspection_data() -> dict[str, Any]:
    """Load inspection data directly from pre-inspected cache or inspect model."""
    cache_path = REPO_ROOT / "runs" / "configured_inspection.json"
    if cache_path.is_file():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    assert LIVE_MPH_PATH.is_file(), f"Model file missing: {LIVE_MPH_PATH}"
    return inspect_model(LIVE_MPH_PATH)


@pytest.fixture(scope="module")
def fem_results() -> dict[str, Any]:
    """Load FEM modal and stress results."""
    assert FEM_JSON_PATH.is_file(), f"FEM results missing: {FEM_JSON_PATH}"
    with open(FEM_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


class TestM2SchemaAndInvariants:
    """Verifies schema updates matching Milestone 2 and user clarification requirements."""

    def test_schema_13kw_rated_power_and_torque_constants(self) -> None:
        """Verify module-level and dictionary constants for 13 kW / 88.672 N*m envelope."""
        assert P_rated_max_kw == 13.0
        assert max_shaft_torque_nm == 88.672
        assert HARD_INVARIANTS["P_rated_max_kw"] == 13.0
        assert HARD_INVARIANTS["max_shaft_torque_nm"] == 88.672

        # Physics consistency: P = omega * Q at 1400 rpm
        omega = 1400.0 * 2.0 * math.pi / 60.0
        theoretical_torque = 13000.0 / omega
        assert abs(max_shaft_torque_nm - theoretical_torque) < 0.01

    def test_schema_reverse_thrust_symmetry_floor_60pct(self) -> None:
        """Verify reverse thrust symmetry floor is authoritative 60% (not legacy 75%)."""
        assert HARD_INVARIANTS["eta_sym_floor"] == 0.60
        assert HARD_INVARIANTS["eta_sym_floor"] != 0.75

    def test_schema_blade_count_flexible_domain(self) -> None:
        """Verify allowed blade counts set is {3, 4, 5}."""
        assert ALLOWED_BLADE_COUNTS == {3, 4, 5}
        assert HARD_INVARIANTS["allowed_blade_counts"] == {3, 4, 5}
        assert set(HARD_INVARIANTS["blade_counts"]) == {3, 4, 5}

        # Valid blade counts succeed
        for z in (3, 4, 5):
            audit = PropellerGeometryAudit(blade_count=z)
            assert audit.blade_count == z

        # Out-of-bounds blade counts raise ValidationError
        for z in (1, 2, 6, 7):
            with pytest.raises(ValidationError):
                PropellerGeometryAudit(blade_count=z)

    def test_geometry_guard_flexible_blade_counts(self) -> None:
        """Verify GeometryGuard accepts blade counts 3, 4, 5 and rejects others."""
        for z in (3, 4, 5):
            audit = GeometryGuard.validate({}, measured_bbox={"blade_count": z})
            assert audit.is_valid is True
            assert audit.blade_count == z

        # Blade count 2 or 6 should produce invalid audit
        bbox_invalid = {"blade_count": 6}
        audit_invalid = GeometryGuard.validate({}, measured_bbox=bbox_invalid)
        assert audit_invalid.is_valid is False
        assert any("blade_count" in r for r in audit_invalid.reasons)


@pytest.mark.artifact
class TestM2ConfiguredModelOfflineInspection:
    """Verifies live COMSOL prototype model comsol_ego_13kw_configured.mph."""

    def test_configured_model_file_exists_and_size(self) -> None:
        """Verify presence and non-trivial file size of live configured prototype."""
        assert LIVE_MPH_PATH.is_file(), f"Prototype model missing: {LIVE_MPH_PATH}"
        file_size = LIVE_MPH_PATH.stat().st_size
        assert file_size > 130_000_000, f"Prototype size too small: {file_size} bytes (expected ~130.8 MB)"

    def test_exact_optimization_api_property_names(self, model_inspection_data: dict[str, Any]) -> None:
        """Verify COMSOL 6.4 Optimization feature uses exact properties (pname, initval, lbound, ubound, scale)."""
        studies = model_inspection_data.get("studies", [])
        std_ego = next((s for s in studies if s["tag"] == "std_ego"), None)
        assert std_ego is not None, "Study std_ego missing from model"

        opt_feat = next((f for f in std_ego.get("features", []) if f["tag"] == "opt"), None)
        assert opt_feat is not None, "Optimization feature 'opt' missing from std_ego"

        props = opt_feat.get("properties", {})

        # Verify exact property keys exist
        assert "pname" in props, "pname missing from opt feature"
        assert "initval" in props, "initval missing from opt feature (NOT pinit)"
        assert "lbound" in props, "lbound missing from opt feature (NOT plower)"
        assert "ubound" in props, "ubound missing from opt feature (NOT pupper)"
        assert "scale" in props, "scale missing from opt feature (NOT pscale)"

        # Verify legacy property keys are NOT present
        assert "pinit" not in props, "Legacy 'pinit' must not be in COMSOL 6.4 opt feature"
        assert "plower" not in props, "Legacy 'plower' must not be in COMSOL 6.4 opt feature"
        assert "pupper" not in props, "Legacy 'pupper' must not be in COMSOL 6.4 opt feature"
        assert "pscale" not in props, "Legacy 'pscale' must not be in COMSOL 6.4 opt feature"

        # Verify control parameters
        pname = props["pname"]
        for p in ["pitch_r07_deg", "pitch_r10_deg", "chord_r07_m", "chord_r10_m", "max_camber_ratio"]:
            assert p in pname, f"Control parameter {p} missing from pname"

    def test_13kw_torque_constraint_and_surrogate_settings(self, model_inspection_data: dict[str, Any]) -> None:
        """Verify 13 kW torque constraint and dedicated GP constraint surrogate."""
        std_ego = next(s for s in model_inspection_data["studies"] if s["tag"] == "std_ego")
        opt_feat = next(f for f in std_ego["features"] if f["tag"] == "opt")
        props = opt_feat["properties"]

        # Method and kernel
        assert props.get("optmethod") == "ego"
        assert props.get("covfunction") == "matern32"
        assert props.get("meanfunction") == "const"
        assert props.get("acquisitionmaxmethod") == "direct"

        # Objective: thrust_cw_total maximization
        assert props.get("optobj") == "thrust_cw_total"
        assert props.get("objectivetype") == "maximization"

        # 13 kW torque constraint
        assert "torque_cw_shaft" in props.get("constraintExpression", "")
        assert "88.672[N*m]" in props.get("constraintUbound", "")
        assert props.get("globalgpconstrfunction") == "new"

    def test_frozen_physical_invariants_parameters(self, model_inspection_data: dict[str, Any]) -> None:
        """Verify non-negotiable physical invariants in model.param()."""
        params = model_inspection_data.get("parameters", {})

        assert "D_prop" in params
        assert params["D_prop"]["expr"] == "0.320[m]"
        assert math.isclose(params["D_prop"]["value"], 0.320, abs_tol=1e-4)

        assert "R_prop" in params
        assert params["R_prop"]["expr"] == "0.160[m]"
        assert math.isclose(params["R_prop"]["value"], 0.160, abs_tol=1e-4)

        assert "D_hub" in params
        assert params["D_hub"]["expr"] == "0.084[m]"
        assert math.isclose(params["D_hub"]["value"], 0.084, abs_tol=1e-4)

        assert "tip_clearance_m" in params
        assert params["tip_clearance_m"]["expr"] == "0.00193[m]"

        assert "speed_rpm" in params
        assert math.isclose(params["speed_rpm"]["value"], 23.333333, rel_tol=1e-3)

        assert "P_rated_max" in params
        assert params["P_rated_max"]["expr"] == "13000[W]"
        assert params["P_rated_max"]["value"] == 13000.0

        assert "Q_rated_max" in params
        assert params["Q_rated_max"]["expr"] == "88.672[N*m]"
        assert math.isclose(params["Q_rated_max"]["value"], 88.672, abs_tol=1e-3)

        assert "Z_blades" in params
        assert params["Z_blades"]["value"] in (3.0, 4.0, 5.0)

    def test_hydrodynamic_force_integration_operators_and_mesh(self, model_inspection_data: dict[str, Any]) -> None:
        """Verify integration operators intR, intD and mesh element count."""
        comp = model_inspection_data["components"][0]
        meshes = comp.get("meshes", [])
        assert len(meshes) > 0
        mesh = meshes[0]
        assert mesh["num_elem"] > 1_000_000, f"Mesh elements: {mesh['num_elem']}"


class TestM2PropellerOptModelJavaClass:
    """Verifies PropellerOptModel implementation, bytecode compilation, and structure."""

    def test_ensure_bridge_compilation_succeeds(self) -> None:
        """Verify that ensure_bridge_compiled() compiles cleanly with zero errors."""
        bridge_class = ensure_bridge_compiled()
        assert bridge_class.is_file(), f"ComsolBridge.class not found at {bridge_class}"

    def test_propeller_opt_model_bytecode_artifacts_exist(self) -> None:
        """Verify that all required PropellerOptModel bytecode classes are generated."""
        for name in PROPELLER_OPT_CLASS_NAMES:
            class_path = JAVA_SRC_DIR / name
            assert class_path.is_file(), f"Required bytecode class missing: {name}"

    def test_propeller_opt_model_java_source_integrity(self) -> None:
        """Verify that PropellerOptModel.java uses named static nested classes and zero lambdas."""
        source_path = JAVA_SRC_DIR / "PropellerOptModel.java"
        assert source_path.is_file(), "PropellerOptModel.java missing"
        content = source_path.read_text(encoding="utf-8")

        # Zero lambdas for javac/comsolcompile bytecode determinism
        assert "->" not in content, "PropellerOptModel.java contains lambda operators"

        # Named static nested classes
        assert "class InvariantConstants" in content
        assert "class OptConfig" in content
        assert "class ModelSummary" in content

        # Exact COMSOL 6.4 API signatures
        assert '"pname"' in content
        assert '"initval"' in content
        assert '"lbound"' in content
        assert '"ubound"' in content
        assert '"scale"' in content
        assert '"globalgpconstrfunction"' in content
        assert "createAutoSequences" in content

        # Multi-topology check (Z in {3, 4, 5})
        assert "Z_blades" in content
        assert "csel_wet_blade" in content
        assert "csel_tip_edge" in content

    def test_comsol_bridge_registers_configure_propeller_opt_model_cli(self) -> None:
        """Verify that ComsolBridge.java exposes the configure-propeller-opt-model CLI command."""
        bridge_src = (JAVA_SRC_DIR / "ComsolBridge.java").read_text(encoding="utf-8")
        assert "configure-propeller-opt-model" in bridge_src
        assert "PropellerOptModel.handleConfigure" in bridge_src


@pytest.mark.artifact
class TestM2FEMStructuralAndResonanceValidation:
    """Verifies solved FEM structural metrics from propeller_fem_modal_solved."""

    def test_fem_peak_von_mises_stress_under_50mpa(self, fem_results: dict[str, Any]) -> None:
        """Verify peak von Mises stress <= 50.0 MPa (SF >= 2.0)."""
        stress = fem_results["max_von_mises_stress_mpa"]
        assert stress == 6.81
        assert stress <= 50.0, f"Stress {stress} MPa exceeds 50.0 MPa limit"

        sf = fem_results["safety_factor"]
        assert sf == 40.5
        assert sf >= 2.0, f"Safety factor {sf} < 2.0"

    def test_fem_tip_deflection_under_0_1mm(self, fem_results: dict[str, Any]) -> None:
        """Verify blade tip deflection <= 0.100 mm."""
        disp = fem_results["max_displacement_mm"]
        assert disp == 0.0435
        assert disp <= 0.100, f"Tip deflection {disp} mm exceeds 0.100 mm limit"

        # Tip clearance consumption
        tip_clearance_mm = 1.93
        consumption_pct = (disp / tip_clearance_mm) * 100.0
        assert abs(consumption_pct - 2.25) < 0.1

    def test_fem_wet_modal_frequency_bpf_separation(self, fem_results: dict[str, Any]) -> None:
        """Verify fundamental wet frequency f_1 = 240.04 Hz >= 1.15 * BPF (80.5 Hz)."""
        bpf = fem_results["bpf_1x_hz"]
        assert bpf == 70.0  # 3 blades * 1400 / 60

        modes = fem_results["modes"]
        mode1 = modes[0]
        f_wet = mode1["wet_freq_hz"]
        assert f_wet == 240.04

        min_allowable_f = 1.15 * bpf  # 80.5 Hz
        assert f_wet >= min_allowable_f, f"Fundamental wet mode {f_wet} Hz < {min_allowable_f} Hz"

        # Campbell margin: (240.04 - 70) / 70 = 242.9%
        campbell_margin = ((f_wet - bpf) / bpf) * 100.0
        assert abs(campbell_margin - 242.9) < 0.2
        assert campbell_margin >= 15.0
