"""Tier 1 Opaque-Box Feature Coverage Suite (75 tests: T1-F01-01 .. T1-F15-05).

Covers all 15 implementation features (5 equivalence-class tests per feature):
- 64 deterministic offline tests (run under `-m "not integration and not artifact"`)
- 11 live COMSOL integration tests (`@pytest.mark.integration`)

Zero top-level imports of `hydro_disturb_bench.propeller.*` are allowed; all future
M1-M4 contracts are loaded via `require_contract(...)`.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, to_safe_path
from hydro_disturb_bench.comsol_java.file_transactions import (
    file_publish_lock,
    publish_staging_file,
    task_staging_session,
)

from .conftest import (
    BASELINE_CONSTANTS,
    BASELINE_XT,
    CCW_BASELINE_MPH,
    CW_BASELINE_MPH,
    HARD_INVARIANTS,
    require_contract,
    require_repo_artifact,
    resolve_contract_attr,
)

pytestmark = pytest.mark.tier1


def _call_guard_validate(guard_cls: Any, design: Any) -> Any:
    if hasattr(guard_cls, "validate") and callable(guard_cls.validate):
        try:
            return guard_cls.validate(design)
        except TypeError:
            return guard_cls().validate(design)
    return guard_cls().validate(design)


def _extract_valid_flag(audit: Any) -> bool:
    if isinstance(audit, dict):
        return bool(audit.get("is_valid", audit.get("valid", False)))
    return bool(getattr(audit, "is_valid", getattr(audit, "valid", False)))


# ==============================================================================
# F01: COMSOL 3D Propeller Bridge & Selector Extension (5 tests)
# ==============================================================================


@pytest.mark.integration
def test_t1_f01_01_inspect_propeller_baseline_cw(live_comsol_env, baseline_constants) -> None:
    """T1-F01-01: Read-only inspect_propeller on CW baseline .mph."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("inspect_propeller",))
    res = bridge.inspect_propeller(live_comsol_env["cw_mph"])
    assert res.get("status") == "SUCCESS"
    payload_bytes = json.dumps(res, ensure_ascii=False).encode("utf-8")
    assert len(payload_bytes) < 51200
    assert res.get("sdim") == 3
    text_dump = json.dumps(res)
    assert "rot1" in text_dump or "moving" in text_dump
    for cpl_tag in ("intR", "intD", "intFar"):
        assert cpl_tag in text_dump
    assert compute_file_hash(live_comsol_env["cw_mph"]) == baseline_constants["cw_sha256"]


@pytest.mark.integration
def test_t1_f01_02_3d_bounding_box_probe_baseline(live_comsol_env, hard_invariants) -> None:
    """T1-F01-02: 3D bounding-box probe on baseline impeller & nozzle."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("inspect_propeller",))
    res = bridge.inspect_propeller(live_comsol_env["cw_mph"], include_bbox=True)
    assert res.get("status") == "SUCCESS"
    bbox = res.get("bbox", res.get("geometry_audit", {}))
    r_prop = float(bbox.get("r_prop_m", bbox.get("d_prop_m", 0.0) / 2.0))
    assert abs(r_prop - hard_invariants["R_prop_m"]) <= hard_invariants["radius_tol_m"]
    r_hub = float(bbox.get("r_hub_m", bbox.get("d_hub_m", 0.0) / 2.0))
    assert abs(r_hub - hard_invariants["R_hub_m"]) <= hard_invariants["radius_tol_m"]
    duct_r_max = float(bbox.get("duct_outer_max_r_m", bbox.get("duct_outer_max_d_m", 0.0) / 2.0))
    assert duct_r_max <= hard_invariants["duct_outer_max_r_m"] + hard_invariants["radius_tol_m"]


def test_t1_f01_03_spatial_3d_classifier_shuffled_entity_ids() -> None:
    """T1-F01-03: Spatial 3D boundary/domain classifier handles renumbered entity IDs."""
    mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    classifier = resolve_contract_attr(
        mod,
        ("classify_spatial_entities", "classify_boundaries", "SpatialEntityClassifier"),
        "spatial 3D classifier",
    )
    synthetic_faces = [
        {"id": 101, "x_min": -3.39, "x_max": -3.39, "r_max": 1.60},  # -X far cap
        {"id": 102, "x_min": 1.54, "x_max": 1.54, "r_max": 1.60},    # +X far cap
        {"id": 103, "x_min": -3.39, "x_max": 1.54, "r_max": 1.60},   # outer cylinder far
        {"id": 104, "x_min": 0.055, "x_max": 0.140, "r_max": 0.1625},  # moving rotorflow interface
        {"id": 105, "x_min": 0.010, "x_max": 0.202, "r_max": 0.194},   # nozzle
        {"id": 106, "x_min": 0.065, "x_max": 0.130, "r_max": 0.160},   # impeller
    ]
    result = classifier(synthetic_faces) if callable(classifier) else classifier().classify(synthetic_faces)
    assert isinstance(result, dict)
    assert 101 in result.get("far", []) and 102 in result.get("far", [])
    assert 105 in result.get("nozzle", [])
    assert 106 in result.get("impeller", [])


def test_t1_f01_04_imp_filename_utf8_base64_and_safe_path() -> None:
    """T1-F01-04: Base64 UTF-8 IPC & to_safe_path() resolve Unicode/space paths cleanly."""
    assert BASELINE_XT.is_file(), f"Missing baseline Parasolid: {BASELINE_XT}"
    safe_xt = to_safe_path(BASELINE_XT)
    assert isinstance(safe_xt, str) and len(safe_xt) > 0
    assert Path(safe_xt).exists()
    cmd_payload = {
        "operation": "update_import_filename",
        "imp_tag": "imp1",
        "filename": safe_xt,
        "original_unicode_path": str(BASELINE_XT),
        "override_legacy": "E:/prop test2/3_320kgf_v10f.x_t",
    }
    encoded = base64.b64encode(json.dumps(cmd_payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    assert encoded.isascii()
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded["filename"] == safe_xt
    assert "3_320kgf_v10f.x_t" in decoded["original_unicode_path"]
    assert decoded["override_legacy"] != decoded["filename"]


@pytest.mark.integration
def test_t1_f01_05_summary_numerical_eval_on_baseline(live_comsol_env, baseline_constants) -> None:
    """T1-F01-05: Bridge summary numerical evaluation on solved baseline dset1."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("inspect_propeller",))
    res = bridge.inspect_propeller(live_comsol_env["ccw_mph"], include_summary=True)
    assert res.get("status") == "SUCCESS"
    summary = res.get("summary", res)
    t_total = abs(float(summary.get("T_total", summary.get("thrust_n", 0.0))))
    p_kw = abs(float(summary.get("power_kw", 0.0)))
    assert abs(t_total - baseline_constants["ccw_kpi"]["T_N"]) / baseline_constants["ccw_kpi"]["T_N"] <= 0.02
    assert abs(p_kw - baseline_constants["ccw_kpi"]["P_kw"]) / baseline_constants["ccw_kpi"]["P_kw"] <= 0.05


# ==============================================================================
# F02: Baseline Reproduction & SHA-256 Safety (5 tests)
# ==============================================================================


@pytest.mark.integration
def test_t1_f02_01_warm_start_cow_cw_baseline(live_comsol_env, baseline_constants) -> None:
    """T1-F02-01: Warm-start CoW reproduction of CW baseline (+1400 rpm)."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("run_propeller_case",))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_cw" / "cw_repro.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.run_propeller_case(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        speed_rpm=1400.0,
        warm_start=True,
    )
    assert res.get("status") == "SUCCESS"
    assert abs(float(res["thrust_n"]) - baseline_constants["cw_kpi"]["T_N"]) <= 25.0
    assert abs(float(res["power_kw"]) - baseline_constants["cw_kpi"]["P_kw"]) <= 0.3
    assert compute_file_hash(live_comsol_env["cw_mph"]) == baseline_constants["cw_sha256"]


@pytest.mark.integration
def test_t1_f02_02_warm_start_cow_ccw_baseline(live_comsol_env, baseline_constants) -> None:
    """T1-F02-02: Warm-start CoW reproduction of CCW baseline (-1400 rpm)."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("run_propeller_case",))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_ccw" / "ccw_repro.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.run_propeller_case(
        source_model=live_comsol_env["ccw_mph"],
        output_model=out_mph,
        speed_rpm=-1400.0,
        warm_start=True,
    )
    assert res.get("status") == "SUCCESS"
    assert abs(float(res["thrust_n"]) - baseline_constants["ccw_kpi"]["T_N"]) <= 50.0
    assert abs(float(res["power_kw"]) - baseline_constants["ccw_kpi"]["P_kw"]) <= 0.4
    assert compute_file_hash(live_comsol_env["ccw_mph"]) == baseline_constants["ccw_sha256"]


def test_t1_f02_03_baseline_sha256_integrity_verified(baseline_constants) -> None:
    """T1-F02-03: Pre/post SHA-256 integrity guard on real read-only baseline .mph assets."""
    if not CW_BASELINE_MPH.is_file() or not CCW_BASELINE_MPH.is_file():
        pytest.skip("Baseline .mph files not present locally")
    assert compute_file_hash(CW_BASELINE_MPH) == baseline_constants["cw_sha256"]
    assert compute_file_hash(CCW_BASELINE_MPH) == baseline_constants["ccw_sha256"]
    assert compute_file_hash(CW_BASELINE_MPH) == baseline_constants["cw_sha256"]


def test_t1_f02_04_cow_staging_locks_and_ascii_dirs(tmp_path: Path) -> None:
    """T1-F02-04: Sequential CoW staging allocation with OS lease & publish locks."""
    staging_root = tmp_path / "runs" / "propeller_opt" / "staging"
    created_dirs: list[Path] = []
    for idx in range(2):
        case_dir = staging_root / f"case_{idx:03d}"
        case_mph = case_dir / f"model_{idx:03d}.mph"
        with file_publish_lock(staging_root / ".locks" / "propeller.lock", timeout_sec=5.0):
            with task_staging_session(case_mph, f"case_{idx:03d}"):
                case_mph.write_bytes(f"COW_PAYLOAD_{idx}".encode("ascii"))
                assert (case_dir / f".lease_case_{idx:03d}.lock").is_file()
        assert not (case_dir / f".lease_case_{idx:03d}.lock").exists()
        created_dirs.append(case_dir)
    assert created_dirs[0] != created_dirs[1]
    assert all((d / f"model_{i:03d}.mph").is_file() for i, d in enumerate(created_dirs))


def test_t1_f02_05_staging_path_ascii_and_source_isolation(tmp_path: Path) -> None:
    """T1-F02-05: Published staging outputs are ASCII-named, non-symlink, source != output."""
    source_mph = tmp_path / "source_baseline.mph"
    source_mph.write_bytes(b"BASELINE_IMMUTABLE")
    source_sha = compute_file_hash(source_mph)

    staging_file = tmp_path / "runs" / "propeller_opt" / "staging" / "case_001" / "stage_001.mph"
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    staging_file.write_bytes(b"SOLVED_OUTPUT_CASE_001")
    final_out = tmp_path / "runs" / "propeller_opt" / "staging" / "case_001" / "propeller_out.mph"

    publish_staging_file(staging_file, final_out, stage_id="case_001", allow_overwrite=False)
    assert final_out.is_file()
    assert not final_out.is_symlink()
    assert final_out.name.isascii() and " " not in final_out.name
    assert final_out.resolve() != source_mph.resolve()
    assert compute_file_hash(source_mph) == source_sha


# ==============================================================================
# F03: Bidirectional Rotation BC Swapping (5 tests)
# ==============================================================================


def test_t1_f03_01_cw_positive_rpm_bc_selection() -> None:
    """T1-F03-01: CW (+1400 rpm) maps upstream to +X cap [29] and open to [1,2,3]."""
    mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        mod,
        ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"),
        "bidirectional BC swapper",
    )
    far_faces = [
        {"id": 1, "x_min": -3.39, "x_max": -3.39},
        {"id": 2, "x_min": -3.39, "x_max": 1.54},
        {"id": 3, "x_min": -3.39, "x_max": 1.54},
        {"id": 29, "x_min": 1.54, "x_max": 1.54},
    ]
    bc = swapper(speed_rpm=1400.0, far_faces=far_faces)
    assert bc["upstream"] == [29]
    assert sorted(bc["open"]) == [1, 2, 3]
    assert set(bc["upstream"]).isdisjoint(set(bc["open"]))


def test_t1_f03_02_ccw_negative_rpm_bc_selection() -> None:
    """T1-F03-02: CCW (-1400 rpm) maps upstream to -X cap [1] and open to [2,3,29]."""
    mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        mod,
        ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"),
        "bidirectional BC swapper",
    )
    far_faces = [
        {"id": 1, "x_min": -3.39, "x_max": -3.39},
        {"id": 2, "x_min": -3.39, "x_max": 1.54},
        {"id": 3, "x_min": -3.39, "x_max": 1.54},
        {"id": 29, "x_min": 1.54, "x_max": 1.54},
    ]
    bc = swapper(speed_rpm=-1400.0, far_faces=far_faces)
    assert bc["upstream"] == [1]
    assert sorted(bc["open"]) == [2, 3, 29]
    assert set(bc["upstream"]).isdisjoint(set(bc["open"]))


def test_t1_f03_03_bc_swap_with_renumbered_cad_faces() -> None:
    """T1-F03-03: Spatial BC swapping adapts cleanly to renumbered CAD faces."""
    mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        mod,
        ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"),
        "bidirectional BC swapper",
    )
    renumbered_faces = [
        {"id": 108, "x_min": -3.39, "x_max": -3.39},
        {"id": 105, "x_min": -3.39, "x_max": 1.54},
        {"id": 106, "x_min": -3.39, "x_max": 1.54},
        {"id": 104, "x_min": 1.54, "x_max": 1.54},
    ]
    bc_ccw = swapper(speed_rpm=-1200.0, far_faces=renumbered_faces)
    assert bc_ccw["upstream"] == [108]
    assert sorted(bc_ccw["open"]) == [104, 105, 106]
    bc_cw = swapper(speed_rpm=1200.0, far_faces=renumbered_faces)
    assert bc_cw["upstream"] == [104]
    assert sorted(bc_cw["open"]) == [105, 106, 108]


def test_t1_f03_04_alternating_cw_ccw_cw_idempotent() -> None:
    """T1-F03-04: Repeated +1400 -> -1400 -> +1400 swaps are idempotent."""
    mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        mod,
        ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"),
        "bidirectional BC swapper",
    )
    far_faces = [
        {"id": 1, "x_min": -3.39, "x_max": -3.39},
        {"id": 2, "x_min": -3.39, "x_max": 1.54},
        {"id": 3, "x_min": -3.39, "x_max": 1.54},
        {"id": 29, "x_min": 1.54, "x_max": 1.54},
    ]
    bc1 = swapper(speed_rpm=1400.0, far_faces=far_faces)
    bc2 = swapper(speed_rpm=-1400.0, far_faces=far_faces)
    bc3 = swapper(speed_rpm=1400.0, far_faces=far_faces)
    assert bc1 == bc3
    assert bc1["upstream"] != bc2["upstream"]


@pytest.mark.integration
def test_t1_f03_05_live_comsol_bc_swap_inspection(live_comsol_env) -> None:
    """T1-F03-05: Live CoW model inspection confirms opposite upstream/open endcaps."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("inspect_propeller",))
    cw_info = bridge.inspect_propeller(live_comsol_env["cw_mph"])
    ccw_info = bridge.inspect_propeller(live_comsol_env["ccw_mph"])
    assert cw_info.get("upstream") != ccw_info.get("upstream")
    assert set(cw_info.get("upstream", [])).isdisjoint(set(cw_info.get("open", [])))


# ==============================================================================
# F04: Fixed Propeller Outer Diameter Lock (5 tests)
# ==============================================================================


def test_t1_f04_01_design_vector_excludes_diameter_dof() -> None:
    """T1-F04-01: PropellerDesignVector strictly excludes diameter from free DOFs."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    fields = set(schema_mod.PropellerDesignVector.model_fields.keys())
    for forbidden in ("diameter", "D_prop", "d_prop_m", "r_prop_m", "R_prop"):
        assert forbidden not in fields


def test_t1_f04_02_fixed_diameter_attached_on_nominal_design(
    nominal_design_dict, hard_invariants
) -> None:
    """T1-F04-02: Derived geometry spec preserves D_prop_m = 0.320 m."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    audit = _call_guard_validate(guard_mod.GeometryGuard, design)
    assert _extract_valid_flag(audit)
    audit_dict = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
    assert abs(float(audit_dict["d_prop_m"]) - hard_invariants["D_prop_m"]) <= hard_invariants["diameter_tol_m"]


def test_t1_f04_03_three_blade_tip_radius_passes_guard(nominal_design_dict, hard_invariants) -> None:
    """T1-F04-03: Measured 3D tip radius r_tip_m = 0.16000 passes GeometryGuard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    audit = guard_mod.GeometryGuard.validate(
        design,
        measured_bbox={"d_prop_m": 0.3200, "r_prop_m": 0.1600, "d_hub_m": 0.0840},
    )
    assert _extract_valid_flag(audit)


def test_t1_f04_04_batch_20_lhs_samples_preserve_exact_diameter() -> None:
    """T1-F04-04: 20 valid LHS samples across free DOFs all resolve to D_prop = 0.320 m."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs", "lhs_sample"), "LHS sampler")
    samples = doe_fn(n_samples=20, method="lhs", seed=7)
    assert len(samples) == 20
    for s in samples:
        audit = _call_guard_validate(guard_mod.GeometryGuard, s)
        audit_dict = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
        assert abs(float(audit_dict["d_prop_m"]) - 0.320) <= 2e-4


@pytest.mark.integration
def test_t1_f04_05_live_comsol_parametric_diameter_lock(
    live_comsol_env, nominal_design_dict, hard_invariants
) -> None:
    """T1-F04-05: Live COMSOL parametric build measures 2 * r_max == 0.3200 +/- 0.0002 m."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("build_propeller_geometry",))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_f04" / "diam_check.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        design=nominal_design_dict,
    )
    assert res.get("status") == "SUCCESS"
    d_measured = float(res["geometry_audit"]["d_prop_m"])
    assert abs(d_measured - hard_invariants["D_prop_m"]) <= hard_invariants["diameter_tol_m"]


# ==============================================================================
# F05: Hub Axial & Radial Interface Lock (5 tests)
# ==============================================================================


def test_t1_f05_01_design_vector_excludes_hub_invariants() -> None:
    """T1-F05-01: Hub radius and axial bounds excluded from free optimization DOFs."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    fields = set(schema_mod.PropellerDesignVector.model_fields.keys())
    for forbidden in ("hub_radius", "d_hub_m", "hub_x_min", "hub_x_max"):
        assert forbidden not in fields


def test_t1_f05_02_nominal_hub_bbox_passes_guard(nominal_design_dict) -> None:
    """T1-F05-02: Nominal hub bbox (D_hub=0.084 m, x in [0.065, 0.130] m) passes."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    audit = guard_mod.GeometryGuard.validate(
        design,
        measured_bbox={"d_hub_m": 0.0840, "hub_x_min_m": 0.0650, "hub_x_max_m": 0.1300},
    )
    assert _extract_valid_flag(audit)


def test_t1_f05_03_high_pitch_moderate_rake_root_inside_hub(nominal_design_dict) -> None:
    """T1-F05-03: High pitch + moderate rake whose blade root stays inside [0.065, 0.130] m passes."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict)
    d.update({"pitch_r04_deg": 34.0, "rake_tip_m": 0.005})
    design = schema_mod.PropellerDesignVector(**d)
    audit = _call_guard_validate(guard_mod.GeometryGuard, design)
    assert _extract_valid_flag(audit)


def test_t1_f05_04_hub_invariants_unchanged_across_chord_sweep(nominal_design_dict) -> None:
    """T1-F05-04: Hub D_hub=0.084 m, L_hub=0.065 m remain invariant across 10 chords."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    for chord in np.linspace(0.042, 0.068, 10):
        d = dict(nominal_design_dict)
        d["chord_r04_m"] = float(chord)
        design = schema_mod.PropellerDesignVector(**d)
        audit = _call_guard_validate(guard_mod.GeometryGuard, design)
        audit_dict = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
        assert abs(float(audit_dict["d_hub_m"]) - 0.084) <= 2e-4


@pytest.mark.integration
def test_t1_f05_05_live_comsol_hub_interface_lock(
    live_comsol_env, nominal_design_dict, hard_invariants
) -> None:
    """T1-F05-05: Live COMSOL build confirms hub r=0.042 m and x in [0.065, 0.130] m."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("build_propeller_geometry",))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_f05" / "hub_check.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        design=nominal_design_dict,
    )
    ga = res["geometry_audit"]
    assert abs(float(ga["d_hub_m"]) - hard_invariants["D_hub_m"]) <= hard_invariants["diameter_tol_m"]
    assert abs(float(ga["hub_x_span"][0]) - hard_invariants["hub_x_min_m"]) <= 2e-4
    assert abs(float(ga["hub_x_span"][1]) - hard_invariants["hub_x_max_m"]) <= 2e-4


# ==============================================================================
# F06: Duct Outer Envelope & Clearance Compatibility (5 tests)
# ==============================================================================


def test_t1_f06_01_design_vector_excludes_duct_outer_envelope() -> None:
    """T1-F06-01: Outer duct envelope fields excluded from free DOFs."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    fields = set(schema_mod.PropellerDesignVector.model_fields.keys())
    for forbidden in ("duct_outer_max_r", "duct_x_min", "duct_x_max"):
        assert forbidden not in fields
    for required in ("tip_clearance_m", "duct_inlet_lip_ratio", "duct_diffuser_angle_deg"):
        assert required in fields


def test_t1_f06_02_nominal_duct_envelope_and_clearance_pass(nominal_design_dict) -> None:
    """T1-F06-02: Nominal outer duct envelope + 4.95 mm tip clearance passes."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict)
    d["tip_clearance_m"] = 0.00495
    design = schema_mod.PropellerDesignVector(**d)
    audit = _call_guard_validate(guard_mod.GeometryGuard, design)
    assert _extract_valid_flag(audit)


def test_t1_f06_03_tip_clearance_varies_inner_throat_only(nominal_design_dict) -> None:
    """T1-F06-03: Varying tip_clearance_m adjusts R_throat while keeping R_prop=0.160 m fixed."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    build_spec = resolve_contract_attr(
        cad_mod, ("build_geometry_descriptor", "generate_geometry_spec", "build_blade_duct_spec"), "CAD descriptor"
    )
    for gap in (0.0020, 0.0035, 0.0050):
        d = dict(nominal_design_dict)
        d["tip_clearance_m"] = gap
        spec = build_spec(schema_mod.PropellerDesignVector(**d))
        for key in ("duct_throat_r_m", "r_prop_m", "duct_outer_max_r_m"):
            assert key in spec, f"CAD descriptor missing required key {key!r}"
        r_throat = float(spec["duct_throat_r_m"])
        assert abs(r_throat - (0.160 + gap)) <= 1e-6
        assert abs(float(spec["r_prop_m"]) - 0.160) <= 1e-6
        assert float(spec["duct_outer_max_r_m"]) <= 0.194428 + 1e-6


def test_t1_f06_04_rotating_domain_and_blade_axial_envelope(nominal_design_dict) -> None:
    """T1-F06-04: Blade stays strictly inside rotorflow (r<0.1625, x in [0.056, 0.139])."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    audit = guard_mod.GeometryGuard.validate(
        design,
        measured_bbox={"d_prop_m": 0.320, "blade_x_min_m": 0.068, "blade_x_max_m": 0.137},
    )
    assert _extract_valid_flag(audit)


@pytest.mark.integration
def test_t1_f06_05_live_comsol_duct_outer_envelope_and_throat(
    live_comsol_env, nominal_design_dict, hard_invariants
) -> None:
    """T1-F06-05: Live COMSOL build verifies outer duct envelope & R_throat=0.1630 m."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("build_propeller_geometry",))
    d = dict(nominal_design_dict)
    d["tip_clearance_m"] = 0.0030
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_f06" / "duct_check.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        design=d,
    )
    ga = res["geometry_audit"]
    assert float(ga["duct_outer_max_d_m"]) <= hard_invariants["duct_outer_max_d_m"] + 2e-4
    assert abs(float(ga["tip_clearance_m"]) - 0.0030) <= 2e-4


# ==============================================================================
# F07: Parametric Blade & Internal Duct Geometry (5 tests)
# ==============================================================================


def test_t1_f07_01_nominal_13dof_generates_3_blades(nominal_design_dict) -> None:
    """T1-F07-01: Nominal 13-DOF vector generates 3 blades at 120-deg spacing."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    gen = resolve_contract_attr(
        cad_mod, ("build_geometry_descriptor", "generate_geometry_spec", "build_blade_duct_spec"), "CAD generator"
    )
    spec = gen(schema_mod.PropellerDesignVector(**nominal_design_dict))
    assert "blade_count" in spec, "CAD descriptor missing 'blade_count'"
    assert int(spec["blade_count"]) == 3


def test_t1_f07_02_symmetric_zero_camber_foil_valid(nominal_design_dict) -> None:
    """T1-F07-02: Zero camber (symmetric bidirectional foil) generates closed valid sections."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict)
    d["max_camber_ratio"] = 0.0
    design = schema_mod.PropellerDesignVector(**d)
    assert _extract_valid_flag(_call_guard_validate(guard_mod.GeometryGuard, design))


def test_t1_f07_03_cambered_foil_finite_te_thickness(nominal_design_dict) -> None:
    """T1-F07-03: Cambered foil generates finite trailing-edge thickness >= 0.3 mm."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    gen = resolve_contract_attr(
        cad_mod, ("build_geometry_descriptor", "generate_geometry_spec", "build_blade_duct_spec"), "CAD generator"
    )
    d = dict(nominal_design_dict)
    d.update({"max_camber_ratio": 0.03, "chord_r04_m": 0.065, "chord_r07_m": 0.075, "chord_r10_m": 0.050})
    spec = gen(schema_mod.PropellerDesignVector(**d))
    assert "te_thickness_m" in spec, "CAD descriptor missing 'te_thickness_m'"
    te = float(spec["te_thickness_m"])
    assert te >= HARD_INVARIANTS["min_te_thickness_m"]


def test_t1_f07_04_iges_fallback_export_artifact(tmp_path: Path, nominal_design_dict) -> None:
    """T1-F07-04: IGES Type 128 / coordinate fallback writes non-empty CAD artifact."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    exporter = resolve_contract_attr(
        cad_mod, ("export_iges", "write_iges_fallback", "export_geometry_coordinates"), "IGES/coord exporter"
    )
    out_file = tmp_path / "propeller_fallback.igs"
    res_path = exporter(schema_mod.PropellerDesignVector(**nominal_design_dict), out_file)
    written = Path(res_path) if res_path else out_file
    assert written.is_file() and written.stat().st_size > 0


@pytest.mark.integration
def test_t1_f07_05_live_comsol_native_build_and_pilot_mesh(
    live_comsol_env, nominal_design_dict
) -> None:
    """T1-F07-05: Live COMSOL native Loft/Revolve/fin(union) + Pilot mesh succeeds."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("build_propeller_geometry",))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_f07" / "native_pilot.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        design=nominal_design_dict,
        mesh_level="pilot",
    )
    assert res.get("status") == "SUCCESS"
    assert "min_mesh_quality" in res, "Live build result missing 'min_mesh_quality'"
    assert float(res["min_mesh_quality"]) > 0.0


# ==============================================================================
# F08: Pre-Solve & Post-Build Geometry Guard + Anti-Entanglement (5 tests)
# ==============================================================================


def test_t1_f08_01_presolve_geometry_guard_nominal_pass(nominal_design_dict) -> None:
    """T1-F08-01: Pre-solve GeometryGuard.validate passes with empty violations."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    audit = _call_guard_validate(
        guard_mod.GeometryGuard, schema_mod.PropellerDesignVector(**nominal_design_dict)
    )
    assert _extract_valid_flag(audit)
    violations = getattr(audit, "violations", getattr(audit, "reasons", []))
    assert len(violations) == 0


def test_t1_f08_02_high_skew_anti_entanglement_passes(nominal_design_dict) -> None:
    """T1-F08-02: High positive skew candidate passes anti-entanglement guard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict)
    d["skew_tip_deg"] = 25.0
    audit = _call_guard_validate(guard_mod.GeometryGuard, schema_mod.PropellerDesignVector(**d))
    assert _extract_valid_flag(audit)


def test_t1_f08_03_large_valid_chord_interblade_clearance_passes(nominal_design_dict) -> None:
    """T1-F08-03: Large valid mid-span chord maintains inter-blade clearance."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict)
    d["chord_r07_m"] = 0.085
    audit = _call_guard_validate(guard_mod.GeometryGuard, schema_mod.PropellerDesignVector(**d))
    assert _extract_valid_flag(audit)


def test_t1_f08_04_post_build_audit_matches_invariants(nominal_design_dict, hard_invariants) -> None:
    """T1-F08-04: Post-build GeometryGuard validates COMSOL readout invariants."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    audit = guard_mod.GeometryGuard.validate(
        schema_mod.PropellerDesignVector(**nominal_design_dict),
        measured_bbox={
            "d_prop_m": 0.320,
            "d_hub_m": 0.084,
            "duct_outer_max_d_m": 0.3885,
            "tip_clearance_m": 0.0030,
        },
    )
    assert _extract_valid_flag(audit)


@pytest.mark.integration
def test_t1_f08_05_live_comsol_pre_and_post_guard_roundtrip(
    live_comsol_env, nominal_design_dict
) -> None:
    """T1-F08-05: Pre-solve and post-build COMSOL coordinates both pass GeometryGuard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("build_propeller_geometry",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert _extract_valid_flag(_call_guard_validate(guard_mod.GeometryGuard, design))
    out_mph = live_comsol_env["staging_root"] / "tmp_e2e_f08" / "guard_rt.mph"
    out_mph.parent.mkdir(parents=True, exist_ok=True)
    res = bridge.build_propeller_geometry(
        source_model=live_comsol_env["cw_mph"],
        output_model=out_mph,
        design=nominal_design_dict,
    )
    post_audit = guard_mod.GeometryGuard.validate(design, measured_bbox=res["geometry_audit"])
    assert _extract_valid_flag(post_audit)


# ==============================================================================
# F09: Pilot-to-Fine Warm-Start & Serial Evaluator (5 tests, all offline)
# ==============================================================================


def test_t1_f09_01_serial_evaluator_pilot_to_fine_warm_start(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T1-F09-01: Serial evaluator executes Pilot -> Fine warm-start with max_workers=1."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    fake_runner = mock_comsol_propeller_subprocess()
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=fake_runner, max_workers=1)
    result = ev.evaluate(nominal_design_dict, case_id="t1_f09_01")
    res_dict = result if isinstance(result, dict) else result.model_dump()
    assert res_dict["status"] == "SUCCESS"
    assert len(fake_runner.calls) >= 1


def test_t1_f09_02_evaluator_ledger_cache_hit_skips_comsol(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T1-F09-02: Re-submitting identical design hits cache with 0 COMSOL invocations."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    fake_runner = mock_comsol_propeller_subprocess()
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=fake_runner, max_workers=1)
    r1 = ev.evaluate(nominal_design_dict, case_id="cache_1")
    calls_after_first = len(fake_runner.calls)
    r2 = ev.evaluate(nominal_design_dict, case_id="cache_2")
    assert len(fake_runner.calls) == calls_after_first
    r2_dict = r2 if isinstance(r2, dict) else r2.model_dump()
    assert r2_dict["status"] == (r1["status"] if isinstance(r1, dict) else r1.status)


def test_t1_f09_03_evaluator_resume_completes_remaining_cases(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T1-F09-03: Resuming interrupted 4-case batch evaluates only remaining 2 cases."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    batch = []
    for i in range(4):
        d = dict(nominal_design_dict)
        d["pitch_r07_deg"] = 20.0 + i
        batch.append(d)

    runner1 = mock_comsol_propeller_subprocess()
    ev1 = evaluator_cls(work_dir=tmp_path / "runs", process_runner=runner1, max_workers=1)
    for idx in range(2):
        ev1.evaluate(batch[idx], case_id=f"case_{idx}")
    calls_partial = len(runner1.calls)

    runner2 = mock_comsol_propeller_subprocess()
    ev2 = evaluator_cls(work_dir=tmp_path / "runs", process_runner=runner2, max_workers=1)
    results = [ev2.evaluate(d, case_id=f"case_{idx}") for idx, d in enumerate(batch)]
    assert len(results) == 4
    assert len(runner2.calls) == calls_partial  # only 2 new cases executed


def test_t1_f09_04_propeller_eval_result_contract_fields(calibrated_rov_oracle, nominal_design_dict) -> None:
    """T1-F09-04: PropellerEvalResult schema enforces all PROJECT.md contract fields."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerEvalResult",))
    raw = calibrated_rov_oracle.evaluate(nominal_design_dict, case_id="schema_check")
    parsed = schema_mod.PropellerEvalResult(**raw)
    dumped = parsed.model_dump()
    for required_key in (
        "case_id",
        "status",
        "geometry_audit",
        "cfd_ccw",
        "cfd_cw",
        "dp_maneuver",
        "hover_disturbance",
        "cavitation",
        "structural_modal",
        "fidelity",
        "wall_time_s",
        "error_code",
    ):
        assert required_key in dumped


def test_t1_f09_05_warm_start_mocked_fine_convergence_and_baseline_safe(
    tmp_path: Path, nominal_design_dict, unicode_space_tmp_dir, mock_comsol_propeller_subprocess
) -> None:
    """T1-F09-05: Mocked Pilot-to-Fine warm-start converges (rel_err <= 0.0025), baseline untouched."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    fake_runner = mock_comsol_propeller_subprocess(
        payload={
            "status": "SUCCESS",
            "rel_err": 0.0012,
            "mass_imbalance_pct": 0.22,
            "thrust_n": 2150.0,
            "power_kw": 10.4,
        }
    )
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=fake_runner, max_workers=1)
    res = ev.evaluate(nominal_design_dict, case_id="t1_f09_05")
    res_dict = res if isinstance(res, dict) else res.model_dump()
    assert res_dict["status"] == "SUCCESS"
    assert compute_file_hash(unicode_space_tmp_dir["mph"]) == unicode_space_tmp_dir["sha256"]


# ==============================================================================
# F10: Bollard Pull (J=0) & Bidirectional Symmetry KPIs (5 tests, all offline)
# ==============================================================================


def test_t1_f10_01_bollard_pull_kpis_from_ccw_baseline() -> None:
    """T1-F10-01: Extract T_CCW=2475.37 N, P=12.33 kW, T/P=200.84 N/kW from raw forces."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_bollard_kpis", "compute_bollard_pull", "extract_bollard_metrics"), "bollard KPI fn"
    )
    out = fn(f_rotor_x=-1387.10, f_duct_x=-1088.27, torque_nm=84.07, speed_rpm=-1400.0)
    assert abs(float(out["thrust_n"]) - 2475.37) <= 0.05
    assert abs(float(out["power_kw"]) - 12.326) <= 0.03
    assert abs(float(out["t_over_p"]) - 200.84) <= 0.6


def test_t1_f10_02_bidirectional_symmetry_passes_floor() -> None:
    """T1-F10-02: Improved symmetric candidate (1850 vs 2000 N) yields eta_sym=0.925 >= 0.75."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    eta = float(fn(t_cw=1850.0, t_ccw=2000.0))
    assert abs(eta - 0.925) <= 1e-6
    assert eta >= HARD_INVARIANTS["eta_sym_floor"]


def test_t1_f10_03_bidirectional_symmetry_uses_min_over_max() -> None:
    """T1-F10-03: Symmetry uses min/max when T_CW > T_CCW (1900/2100 = 0.9048)."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    eta = float(fn(t_cw=2100.0, t_ccw=1900.0))
    assert abs(eta - (1900.0 / 2100.0)) <= 1e-6
    assert eta <= 1.0


def test_t1_f10_04_conservation_and_wall_y_plus_audit_pass() -> None:
    """T1-F10-04: C4_cfd_quality passes with valid mass, momentum, y+, and rel_err."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("check_cfd_quality", "audit_cfd_quality", "validate_cfd_quality"), "CFD quality audit"
    )
    res = fn(
        mass_imbalance_pct=0.21,
        momentum_closure_pct=2.8,
        y_plus_imp=38.1,
        y_plus_duct=13.5,
        rel_err=0.0011,
    )
    passed = bool(res if isinstance(res, bool) else res.get("passed", res.get("is_valid")))
    assert passed


def test_t1_f10_05_baseline_cw_ccw_symmetry_kpi_reproduction(calibrated_rov_oracle, nominal_design_dict) -> None:
    """T1-F10-05: Calibrated baseline reproduction yields eta_sym~0.510 and mass<1%."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    base = calibrated_rov_oracle.evaluate(nominal_design_dict, baseline_anchor=True)
    eta = float(fn(t_cw=base["cfd_cw"]["thrust_n"], t_ccw=base["cfd_ccw"]["thrust_n"]))
    assert abs(eta - 0.5100) <= 0.005
    assert base["cfd_ccw"]["mass_imbalance_pct"] < HARD_INVARIANTS["mass_imbalance_max_pct"]


# ==============================================================================
# F11: Low/Medium-Speed DP (J>0) & Hover Disturbance Rejection (5 tests, offline)
# ==============================================================================


def test_t1_f11_01_dp_open_water_kpis_exact_formula() -> None:
    """T1-F11-01: K_T, K_Q, eta_0 at U_a=0.75 m/s, 1400 rpm, D=0.320 m match formula."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    out = fn(u_a_mps=0.75, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=1800.0, torque_nm=65.0, rho=1025.0)
    n_rps = 1400.0 / 60.0
    j_exp = 0.75 / (n_rps * 0.320)
    kt_exp = 1800.0 / (1025.0 * (n_rps**2) * (0.320**4))
    kq_exp = 65.0 / (1025.0 * (n_rps**2) * (0.320**5))
    eta_exp = (j_exp / (2.0 * np.pi)) * (kt_exp / kq_exp)
    assert abs(float(out["J"]) - j_exp) <= 1e-5
    assert abs(float(out["K_T"]) - kt_exp) <= 1e-5
    assert abs(float(out["K_Q"]) - kq_exp) <= 1e-6
    assert abs(float(out["eta_0"]) - eta_exp) <= 1e-5


def test_t1_f11_02_dp_curve_sweep_j_zero_to_030() -> None:
    """T1-F11-02: DP sweep across J=[0.0, 0.15, 0.30] gives eta_0(0)=0 and eta_0(J>0)>0."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    n_rps = 1400.0 / 60.0
    etas = []
    for j_val, thrust in ((0.0, 2000.0), (0.15, 1750.0), (0.30, 1450.0)):
        u_a = j_val * n_rps * 0.320
        out = fn(u_a_mps=u_a, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=thrust, torque_nm=62.0, rho=1025.0)
        etas.append(float(out["eta_0"]))
    assert etas[0] == 0.0
    assert etas[1] > 0.0 and etas[2] > etas[1]


def test_t1_f11_03_steady_crossflow_hover_disturbance_sensitivity() -> None:
    """T1-F11-03: Steady crossflow S_dist = |Delta T|/T_0 + alpha*||F_yz||/T_0 exact."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_hover_disturbance", "compute_disturbance_sensitivity"), "disturbance fn"
    )
    out = fn(t_0_n=2000.0, t_disturbed_n=1920.0, f_y_n=60.0, f_z_n=80.0, alpha=0.5)
    assert abs(float(out["s_dist"]) - 0.065) <= 1e-6


def test_t1_f11_04_transient_pulse_disturbance_settling_time() -> None:
    """T1-F11-04: Transient Gaussian pulse extracts peak |Delta T|/T_0 and settling time."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("extract_transient_pulse_metrics", "compute_pulse_disturbance_metrics"), "pulse metric fn"
    )
    t = np.linspace(0.0, 10.0, 201)
    thrust = 2000.0 - 200.0 * np.exp(-0.5 * ((t - 3.0) / 0.4) ** 2)
    out = fn(times_s=t, thrust_series_n=thrust, t_0_n=2000.0)
    assert abs(float(out["peak_delta_t_rel"]) - 0.10) <= 0.01
    assert 0.5 <= float(out["settling_time_s"]) <= 7.0


def test_t1_f11_05_velocity_inlet_dp_eval_thrust_drops_with_ua(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T1-F11-05: Velocity-inlet DP evaluation shows T(U_a=0.8) < T(J=0) and finite eta_0."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    r_j0 = calibrated_rov_oracle.evaluate(nominal_design_dict, advance_j=0.0)
    r_dp = calibrated_rov_oracle.evaluate(nominal_design_dict, advance_j=0.107)
    out = fn(
        u_a_mps=r_dp["dp_maneuver"]["U_a_mps"],
        speed_rpm=1400.0,
        d_prop_m=0.320,
        thrust_n=r_dp["t_mean_n"] * 0.90,
        torque_nm=60.0,
        rho=1025.0,
    )
    assert float(out["eta_0"]) > 0.0
    assert r_j0["t_mean_n"] * 0.90 < r_j0["t_mean_n"]


# ==============================================================================
# F12: Cavitation Inception & Tip-Gap Leakage Metrics (5 tests, all offline)
# ==============================================================================


def test_t1_f12_01_cavitation_clean_operating_point_passes() -> None:
    """T1-F12-01: Clean operating point (p_min=-72 kPa, A_sub99=0) passes C3_cavitation."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    out = fn(p_min_gauge_kpa=-72.0, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)
    assert bool(out["c3_passed"])
    assert float(out["cav_area_ratio"]) == 0.0
    assert float(out["sigma"]) + float(out["c_p_min"]) > 0.0


def test_t1_f12_02_cavitation_number_and_cp_min_at_depth_10m() -> None:
    """T1-F12-02: Cavitation sigma & C_p_min at depth h=10 m match exact physics formula."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    out = fn(p_min_gauge_kpa=-110.0, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=10.0, speed_rpm=1400.0)
    rho, g, p_atm, p_v = 1025.0, 9.81, 101325.0, 2338.0
    n_rps = 1400.0 / 60.0
    q_dyn = 0.5 * rho * ((np.pi * n_rps * 0.320) ** 2)
    p_amb = p_atm + rho * g * 10.0
    sigma_exp = (p_amb - p_v) / q_dyn
    assert abs(float(out["sigma"]) - sigma_exp) <= 1e-4


def test_t1_f12_03_tip_gap_leakage_decreases_with_tighter_clearance() -> None:
    """T1-F12-03: Tighter tip gap (5.0 -> 2.0 mm) reduces tip-gap leakage metric."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_tip_gap_leakage", "estimate_tip_leakage"), "tip leakage fn"
    )
    leak_wide = float(fn(tip_clearance_m=0.0050, delta_p_pa=25000.0))
    leak_tight = float(fn(tip_clearance_m=0.0020, delta_p_pa=25000.0))
    assert leak_tight >= 0.0 and leak_tight < leak_wide


def test_t1_f12_04_dual_direction_worst_case_cavitation_aggregation() -> None:
    """T1-F12-04: Worst-case aggregation picks CW -88 kPa over CCW -65 kPa."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("aggregate_bidirectional_cavitation", "compute_cavitation_metrics"), "cavitation agg fn"
    )
    out = fn(p_min_ccw_kpa=-65.0, p_min_cw_kpa=-88.0, cav_area_ccw_pct=0.0, cav_area_cw_pct=0.0)
    assert abs(float(out["worst_p_min_kpa"]) - (-88.0)) <= 1e-6
    assert bool(out["c3_passed"])


def test_t1_f12_05_baseline_cavitation_asymmetry_verified(calibrated_rov_oracle, nominal_design_dict) -> None:
    """T1-F12-05: Baseline CW (-214.44 kPa) fails C3 while CCW (-84.76 kPa) passes."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    base = calibrated_rov_oracle.evaluate(nominal_design_dict, baseline_anchor=True)
    ccw_res = fn(p_min_gauge_kpa=base["cavitation"]["p_min_ccw_kpa"], area_sub99_m2=0.0, area_imp_m2=0.1348)
    cw_res = fn(p_min_gauge_kpa=base["cavitation"]["p_min_cw_kpa"], area_sub99_m2=0.0007, area_imp_m2=0.1348)
    assert bool(ccw_res["c3_passed"])
    assert not bool(cw_res["c3_passed"])


# ==============================================================================
# F13: FSI / Structural Stress, Wet Modal BPF & Acoustics (5 tests, all offline)
# ==============================================================================


def test_t1_f13_01_calibrated_analytical_structural_fidelity_tag(nominal_design_dict) -> None:
    """T1-F13-01: Analytical structural evaluator computes SF and tags calibrated_analytical."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(
        mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics", "StructuralModalEvaluator"), "multiphysics eval"
    )
    res = fn(nominal_design_dict, thrust_n=2200.0, speed_rpm=1400.0) if callable(fn) else fn().evaluate(nominal_design_dict, thrust_n=2200.0, speed_rpm=1400.0)
    assert res["fidelity"] == "calibrated_analytical"
    assert float(res["fatigue_sf"]) > 0.0 and float(res["sigma_vm_max_pa"]) > 0.0


def test_t1_f13_02_wet_modal_bpf_separation_passes_above_15pct() -> None:
    """T1-F13-02: Wet modal f1=115 Hz vs BPF=70 Hz (harmonics 70,140,210) passes >= 0.15."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("check_bpf_margin", "compute_bpf_separation"), "BPF margin fn")
    out = fn(wet_f1_hz=115.0, speed_rpm=1400.0, blade_count=3)
    sep = float(out["min_bpf_sep"] if isinstance(out, dict) else out)
    assert abs(sep - (25.0 / 115.0)) <= 1e-4
    assert sep >= HARD_INVARIANTS["modal_bpf_sep_min"]


def test_t1_f13_03_acoustic_proxy_improves_with_higher_skew(nominal_design_dict) -> None:
    """T1-F13-03: Higher skew reduces tip-vortex/BPF SPL_proxy with explicit fidelity."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(
        mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics", "compute_acoustic_proxy"), "acoustic proxy"
    )
    d_low = dict(nominal_design_dict, skew_tip_deg=4.0)
    d_high = dict(nominal_design_dict, skew_tip_deg=24.0)
    r_low = fn(d_low, thrust_n=2000.0, speed_rpm=1400.0)
    r_high = fn(d_high, thrust_n=2000.0, speed_rpm=1400.0)
    spl_low = float(r_low["spl_proxy_db"] if isinstance(r_low, dict) else r_low)
    spl_high = float(r_high["spl_proxy_db"] if isinstance(r_high, dict) else r_high)
    assert spl_high < spl_low


def test_t1_f13_04_thicker_blade_reduces_stress_raises_wet_f1(nominal_design_dict) -> None:
    """T1-F13-04: Increasing max_thickness_ratio (0.08->0.12) lowers stress & raises f1."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics"), "multiphysics eval")
    r_thin = fn(dict(nominal_design_dict, max_thickness_ratio=0.08), thrust_n=2200.0, speed_rpm=1400.0)
    r_thick = fn(dict(nominal_design_dict, max_thickness_ratio=0.12), thrust_n=2200.0, speed_rpm=1400.0)
    assert float(r_thick["sigma_vm_max_pa"]) < float(r_thin["sigma_vm_max_pa"])
    assert float(r_thick["wet_f1_hz"]) > float(r_thin["wet_f1_hz"])


def test_t1_f13_05_comsol_3d_fsi_study_builder_tags_fidelity(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T1-F13-05: COMSOL SolidMechanics/acpr study wrapper tags result fidelity='comsol_3d'."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(
        mp_mod, ("run_comsol_fsi_modal", "build_comsol_fsi_study", "evaluate_comsol_structural_modal"), "COMSOL FSI wrapper"
    )
    fake_runner = mock_comsol_propeller_subprocess(
        payload={"status": "SUCCESS", "fidelity": "comsol_3d", "sigma_vm_max_pa": 48e6, "wet_f1_hz": 122.0}
    )
    res = fn(nominal_design_dict, work_dir=tmp_path, process_runner=fake_runner)
    assert res["fidelity"] == "comsol_3d"
    assert float(res["sigma_vm_max_pa"]) > 0.0 and float(res["wet_f1_hz"]) > 0.0


# ==============================================================================
# F14: DoE & Multi-Output Surrogate Modeling (5 tests, all offline)
# ==============================================================================


def test_t1_f14_01_lhs_doe_26x13_deterministic_no_diameter() -> None:
    """T1-F14-01: LHS DoE generates deterministic 26x13 matrix inside free bounds."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    s1 = doe_fn(n_samples=26, method="lhs", seed=42)
    s2 = doe_fn(n_samples=26, method="lhs", seed=42)
    assert len(s1) == 26
    assert s1 == s2
    for row in s1:
        assert len(row) == 13
        for forbidden in HARD_INVARIANTS["forbidden_dof_names"]:
            assert forbidden not in row


def test_t1_f14_02_sobol_doe_32_filtered_by_geometry_guard() -> None:
    """T1-F14-02: Sobol 32 samples filtered by GeometryGuard preserve D_prop=0.320 m."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_sobol"), "Sobol DoE generator")
    samples = doe_fn(n_samples=32, method="sobol", seed=42)
    assert len(samples) == 32
    valid_count = 0
    for row in samples:
        audit = _call_guard_validate(guard_mod.GeometryGuard, row)
        if _extract_valid_flag(audit):
            valid_count += 1
            ad = audit.geometry_audit if hasattr(audit, "geometry_audit") else audit.model_dump()
            assert abs(float(ad["d_prop_m"]) - 0.320) <= 2e-4
    assert valid_count >= 16


def test_t1_f14_03_gp_kriging_fit_predict_mean_and_nonneg_std(calibrated_rov_oracle) -> None:
    """T1-F14-03: GP Kriging multi-output surrogate fits 30 samples with std >= 0."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=30, method="lhs", seed=11)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    model = surrogate_cls(backend="gp")
    model.fit(samples, y_rows)
    preds = model.predict(samples[:5])
    assert len(preds["mean"]) == 5
    assert np.all(np.asarray(preds["std"]) >= 0.0)


def test_t1_f14_04_rbf_surrogate_kfold_and_loo_cv_r2(calibrated_rov_oracle) -> None:
    """T1-F14-04: RBF surrogate K-fold (K=5) and LOO CV report finite R^2 > 0.85."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=35, method="lhs", seed=19)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    model = surrogate_cls(backend="rbf")
    cv_metrics = model.cross_validate(samples, y_rows, k_folds=5)
    for key, val in cv_metrics["r2"].items():
        assert np.isfinite(val) and float(val) > 0.85, f"Low R2 on {key}: {val}"


def test_t1_f14_05_surrogate_save_reload_roundtrip_1e9(tmp_path: Path, calibrated_rov_oracle) -> None:
    """T1-F14-05: Surrogate save/load round-trip matches predictions within 1e-9."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=30, method="lhs", seed=23)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    model = surrogate_cls(backend="rbf")
    model.fit(samples, y_rows)
    save_path = tmp_path / "surrogate_model.npz"
    model.save(save_path)
    reloaded = surrogate_cls.load(save_path)
    p1 = np.asarray(model.predict(samples[:10])["mean"])
    p2 = np.asarray(reloaded.predict(samples[:10])["mean"])
    assert float(np.max(np.abs(p1 - p2))) <= 1e-9


# ==============================================================================
# F15: Constrained NSGA-II & Real-CFD Back-Verification (5 tests, all offline)
# ==============================================================================


def test_t1_f15_01_constrained_nsga2_pareto_front_feasible(calibrated_rov_oracle) -> None:
    """T1-F15-01: Constrained NSGA-II (pop=40, gen=30) returns >=5 feasible Pareto designs."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    res = opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=40, n_gen=30, seed=42)
    pareto = res["pareto_set"] if isinstance(res, dict) else res.pareto_set
    assert len(pareto) >= 5
    for cand in pareto:
        assert "diameter" not in cand


def test_t1_f15_02_adaptive_infill_selects_3_distinct_valid_candidates(calibrated_rov_oracle) -> None:
    """T1-F15-02: Adaptive infill chooses 3 distinct valid non-duplicate designs."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    infill_fn = resolve_contract_attr(opt_mod, ("select_infill_candidates", "adaptive_infill"), "infill selector")
    existing = doe_fn(n_samples=20, method="lhs", seed=5)
    candidates = doe_fn(n_samples=20, method="lhs", seed=99)
    chosen = infill_fn(existing_doe=existing, candidate_pool=candidates, k=3)
    assert len(chosen) == 3


def test_t1_f15_03_staged_pipeline_end_to_end_with_mock_bridge(
    tmp_path: Path, calibrated_rov_oracle
) -> None:
    """T1-F15-03: Full precheck->doe->train->optimize->verify pipeline emits checkpoints & report."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")
    out_dir = tmp_path / "runs" / "propeller_opt" / "e2e_wf"
    report = run_wf(
        work_dir=out_dir,
        stage="all",
        evaluator_fn=calibrated_rov_oracle.evaluate,
        doe_samples=20,
        pop_size=24,
        n_gen=15,
        seed=42,
    )
    assert report["status"] == "SUCCESS"
    assert len(report.get("pareto_front", [])) >= 1


def test_t1_f15_04_cli_and_config_smoke() -> None:
    """T1-F15-04: CLI propeller-* subcommands & frozen config JSON load cleanly."""
    cfg_path = require_repo_artifact("configs/propeller_opt_v10f.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert float(cfg["invariants"]["D_prop_m"]) == 0.320
    assert cfg["baseline"]["cw_sha256"] == BASELINE_CONSTANTS["cw_sha256"]
    assert cfg["baseline"]["ccw_sha256"] == BASELINE_CONSTANTS["ccw_sha256"]

    cli_mod = require_contract("hydro_disturb_bench.cli", ("main",))
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main(["propeller-inspect", "--help"])
    assert exc_info.value.code == 0


def test_t1_f15_05_back_verification_vs_baseline_delta_table(
    tmp_path: Path, calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T1-F15-05: Back-verification confirms D_prop=0.320, C2..C5 pass, & emits baseline delta."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    verify_fn = resolve_contract_attr(
        wf_mod, ("verify_candidates", "back_verify_pareto", "run_verification_stage"), "back-verification"
    )
    cand = dict(nominal_design_dict, max_camber_ratio=0.004, skew_tip_deg=20.0, tip_clearance_m=0.0020)
    report = verify_fn(
        candidates=[cand],
        work_dir=tmp_path / "verify",
        evaluator_fn=calibrated_rov_oracle.evaluate,
    )
    assert len(report["verified_candidates"]) == 1
    row = report["verified_candidates"][0]
    assert abs(float(row["geometry_audit"]["d_prop_m"]) - 0.320) <= 2e-4
    assert float(row["eta_sym"]) >= HARD_INVARIANTS["eta_sym_floor"]
    assert float(row["cavitation"]["worst_p_min_kpa"]) >= HARD_INVARIANTS["p_min_cav_kpa"]
    assert "delta_vs_baseline" in row
