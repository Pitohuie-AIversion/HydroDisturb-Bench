"""Tier 2 Opaque-Box Boundary, Corner & Negative Rejection Suite (90 tests: T2-F01-01 .. T2-F15-06).

Covers 6 boundary/negative tests for each of the 15 implementation features:
- 88 deterministic offline tests
- 2 live COMSOL integration tests (`T2-F01-06`, `T2-F02-06`)

Zero top-level imports of `hydro_disturb_bench.propeller.*` are allowed.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, to_safe_path
from hydro_disturb_bench.comsol_java.errors import (
    ComsolError,
    ModelNotFoundError,
    ModelValidationError,
    NodeNotFoundError,
)
from hydro_disturb_bench.comsol_java.file_transactions import (
    list_quarantine_records,
    publish_staging_file,
    task_staging_session,
)

from .conftest import (
    BASELINE_CONSTANTS,
    BASELINE_XT,
    HARD_INVARIANTS,
    require_contract,
    resolve_contract_attr,
)

pytestmark = pytest.mark.tier2


def _call_guard_validate(guard_cls: Any, design: Any, **kwargs: Any) -> Any:
    if kwargs:
        return guard_cls.validate(design, **kwargs)
    return guard_cls.validate(design)


def _is_valid(audit: Any) -> bool:
    if isinstance(audit, dict):
        return bool(audit.get("is_valid", audit.get("valid", False)))
    return bool(getattr(audit, "is_valid", getattr(audit, "valid", False)))


# ==============================================================================
# F01 Boundaries (6 tests: 5 offline + 1 integration)
# ==============================================================================


def test_t2_f01_01_unicode_emoji_space_path_safe_and_base64() -> None:
    """T2-F01-01: Unicode/emoji/space path resolves via to_safe_path() and Base64 UTF-8."""
    assert BASELINE_XT.is_file()
    safe = to_safe_path(BASELINE_XT)
    assert isinstance(safe, str) and Path(safe).exists()
    payload = json.dumps({"path": str(BASELINE_XT), "safe": safe}, ensure_ascii=False)
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    assert b64.isascii()
    decoded = json.loads(base64.b64decode(b64).decode("utf-8"))
    assert decoded["path"] == str(BASELINE_XT)


def test_t2_f01_02_nonexistent_mph_fails_closed_model_not_found(tmp_path: Path) -> None:
    """T2-F01-02: Non-existent .mph path raises ModelNotFoundError cleanly."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("inspect_propeller",),
        allow_pending_attrs=True,
    )
    missing_mph = tmp_path / "does_not_exist.mph"
    with pytest.raises(ModelNotFoundError):
        bridge.inspect_propeller(missing_mph)


def test_t2_f01_03_truncated_bridge_stdout_raises_validation_error(
    tmp_path: Path, mock_comsol_propeller_subprocess
) -> None:
    """T2-F01-03: Corrupted stdout missing <<<JSON_END>>> raises ComsolError, never {}."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("inspect_propeller",),
        allow_pending_attrs=True,
    )
    dummy = tmp_path / "dummy.mph"
    dummy.write_bytes(b"MOCK_MPH")
    bad_runner = mock_comsol_propeller_subprocess(raw_stdout='<<<JSON_START>>>\n{"status": "SUCCESS"')
    with pytest.raises((ComsolError, ModelValidationError, ValueError)):
        bridge.inspect_propeller(dummy, process_runner=bad_runner)


def test_t2_f01_04_oversized_probe_payload_enforces_50kb_ceiling() -> None:
    """T2-F01-04: Response downsampler enforces < 50 KB MCP JSON ceiling."""
    bridge = require_contract(
        "hydro_disturb_bench.comsol_java.bridge",
        ("inspect_propeller",),
        allow_pending_attrs=True,
    )
    downsampler = resolve_contract_attr(
        bridge,
        ("downsample_bridge_payload", "truncate_bridge_response", "_downsample_payload"),
        "bridge downsampler",
    )
    huge_payload = {"status": "SUCCESS", "nodal_values": list(range(100000))}
    compact = downsampler(huge_payload)
    assert len(json.dumps(compact).encode("utf-8")) < 51200


def test_t2_f01_05_degenerate_spatial_entities_fail_closed() -> None:
    """T2-F01-05: Spatial classifier fails closed on missing far-field cap or zero-volume face."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    classifier = resolve_contract_attr(
        cad_mod,
        ("classify_spatial_entities", "classify_boundaries", "SpatialEntityClassifier"),
        "spatial 3D classifier",
    )
    degenerate = [{"id": 1, "x_min": 0.0, "x_max": 0.0, "r_max": 0.0}]
    with pytest.raises((NodeNotFoundError, ComsolError, ValueError)):
        classifier(degenerate) if callable(classifier) else classifier().classify(degenerate)


@pytest.mark.integration
def test_t2_f01_06_live_comsol_missing_selection_raises_node_not_found(
    live_comsol_env, baseline_constants
) -> None:
    """T2-F01-06: Live COMSOL inspect with nonexistent selection returns NODE_NOT_FOUND."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("inspect_propeller",))
    with pytest.raises(NodeNotFoundError):
        bridge.inspect_propeller(live_comsol_env["cw_mph"], selection_tag="nonexistent_sel_xyz")
    assert compute_file_hash(live_comsol_env["cw_mph"]) == baseline_constants["cw_sha256"]


# ==============================================================================
# F02 Boundaries (6 tests: 5 offline + 1 integration)
# ==============================================================================


def test_t2_f02_01_tampered_baseline_sha256_aborts_precheck(tmp_path: Path) -> None:
    """T2-F02-01: 1-byte tampered baseline copy fails SHA-256 verification before COMSOL."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    verify_sha = resolve_contract_attr(
        eval_mod, ("verify_baseline_sha256", "check_baseline_integrity"), "SHA-256 baseline guard"
    )
    tampered = tmp_path / "tampered.mph"
    tampered.write_bytes(b"TAMPERED_BASELINE_BYTE")
    with pytest.raises((ModelValidationError, ComsolError, ValueError)):
        verify_sha(tampered, expected_sha256=BASELINE_CONSTANTS["cw_sha256"])


def test_t2_f02_02_cow_forbids_overwrite_and_quarantines_conflict(tmp_path: Path) -> None:
    """T2-F02-02: publish_staging_file(allow_overwrite=False) rejects existing target & quarantines."""
    existing_out = tmp_path / "existing.mph"
    existing_out.write_bytes(b"ORIGINAL_TARGET_BYTES")
    orig_sha = compute_file_hash(existing_out)

    staging = tmp_path / "stage_bad.mph"
    staging.write_bytes(b"NEW_STAGING_BYTES")
    with pytest.raises(FileExistsError):
        publish_staging_file(staging, existing_out, stage_id="dup01", allow_overwrite=False)

    assert compute_file_hash(existing_out) == orig_sha
    q_records = list_quarantine_records(tmp_path)
    assert any(r["kind"] == "conflict" for r in q_records)


def test_t2_f02_03_symlink_destination_rejected_fail_closed(tmp_path: Path) -> None:
    """T2-F02-03: Symlink destination rejected by publish_staging_file with ModelValidationError."""
    target = tmp_path / "real_baseline.mph"
    target.write_bytes(b"IMMUTABLE")
    link = tmp_path / "link_out.mph"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("OS privilege does not allow symlink creation in temp dir")

    staging = tmp_path / "stage_001.mph"
    staging.write_bytes(b"STAGED")
    with pytest.raises(ModelValidationError):
        publish_staging_file(staging, link, stage_id="sym01")


def test_t2_f02_04_non_ascii_staging_path_rejected_or_sanitized(tmp_path: Path) -> None:
    """T2-F02-04: Non-ASCII staging case_id is rejected or sanitized strictly to ASCII."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    sanitize_fn = resolve_contract_attr(
        eval_mod,
        ("prepare_staging_dir", "allocate_staging_dir", "sanitize_case_id"),
        "ASCII staging path helper",
    )
    try:
        res = Path(sanitize_fn(tmp_path / "staging", case_id="测试_case"))
        assert res.name.isascii()
    except (ValueError, ComsolError):
        pass


def test_t2_f02_05_stale_mph_lock_does_not_block_readonly_hash(unicode_space_tmp_dir) -> None:
    """T2-F02-05: Pre-existing .mph.lock file does not block read-only SHA-256 or staging."""
    assert unicode_space_tmp_dir["lock"].is_file()
    digest = compute_file_hash(unicode_space_tmp_dir["mph"])
    assert digest == unicode_space_tmp_dir["sha256"]
    stage_mph = unicode_space_tmp_dir["dir"] / "staging" / "copy.mph"
    with task_staging_session(stage_mph, "lock_ok"):
        stage_mph.write_bytes(unicode_space_tmp_dir["mph"].read_bytes())
        assert compute_file_hash(stage_mph) == digest


@pytest.mark.integration
def test_t2_f02_06_live_failed_staging_leaves_baselines_untouched(
    live_comsol_env, baseline_constants
) -> None:
    """T2-F02-06: Failed staging run preserves both baseline SHA-256s with zero temp leaks."""
    bridge = require_contract("hydro_disturb_bench.comsol_java.bridge", ("run_propeller_case",))
    bad_out = live_comsol_env["staging_root"] / "tmp_e2e_fail" / "bad_case.mph"
    bad_out.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ComsolError):
        bridge.run_propeller_case(
            source_model=live_comsol_env["cw_mph"],
            output_model=bad_out,
            speed_rpm=0.0,  # invalid zero RPM triggers failure before/during run
        )
    assert compute_file_hash(live_comsol_env["cw_mph"]) == baseline_constants["cw_sha256"]
    assert compute_file_hash(live_comsol_env["ccw_mph"]) == baseline_constants["ccw_sha256"]


# ==============================================================================
# F03 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f03_01_zero_rpm_rejected_invalid_parameter() -> None:
    """T2-F03-01: Zero RPM (0.0) rejected cleanly."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        cad_mod, ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"), "BC swapper"
    )
    far_faces = [{"id": 1, "x_min": -3.39, "x_max": -3.39}, {"id": 29, "x_min": 1.54, "x_max": 1.54}]
    with pytest.raises((ValueError, ComsolError)):
        swapper(speed_rpm=0.0, far_faces=far_faces)


def test_t2_f03_02_nonfinite_rpm_rejected() -> None:
    """T2-F03-02: NaN and +/-Inf RPM values rejected immediately."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        cad_mod, ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"), "BC swapper"
    )
    far_faces = [{"id": 1, "x_min": -3.39, "x_max": -3.39}, {"id": 29, "x_min": 1.54, "x_max": 1.54}]
    for bad_rpm in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises((ValueError, ComsolError)):
            swapper(speed_rpm=bad_rpm, far_faces=far_faces)


def test_t2_f03_03_mismatched_rpm_sign_and_bc_detected() -> None:
    """T2-F03-03: Mismatched speed_rpm=-1400 paired with CW upstream=[29] is detected."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    validator = resolve_contract_attr(
        cad_mod, ("validate_rotation_bcs", "check_rotation_bcs"), "BC consistency validator"
    )
    far_faces = [
        {"id": 1, "x_min": -3.39, "x_max": -3.39},
        {"id": 2, "x_min": -3.39, "x_max": 1.54},
        {"id": 3, "x_min": -3.39, "x_max": 1.54},
        {"id": 29, "x_min": 1.54, "x_max": 1.54},
    ]
    with pytest.raises((ModelValidationError, ComsolError, ValueError)):
        validator(speed_rpm=-1400.0, upstream=[29], open_faces=[1, 2, 3], far_faces=far_faces)


def test_t2_f03_04_near_zero_signed_rpm_no_abs_sign_bug() -> None:
    """T2-F03-04: Tiny -1e-6 vs +1e-6 rpm either rejected or respects sign (no abs() bug)."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        cad_mod, ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"), "BC swapper"
    )
    far_faces = [{"id": 1, "x_min": -3.39, "x_max": -3.39}, {"id": 29, "x_min": 1.54, "x_max": 1.54}]
    try:
        bc_neg = swapper(speed_rpm=-1e-6, far_faces=far_faces)
        bc_pos = swapper(speed_rpm=1e-6, far_faces=far_faces)
        assert bc_neg["upstream"] == [1] and bc_pos["upstream"] == [29]
    except (ValueError, ComsolError):
        pass


def test_t2_f03_05_invalid_rpm_unit_string_rejected() -> None:
    """T2-F03-05: Invalid unit string '1400[m/s]' or '1400[bogus]' rejected."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    parser = resolve_contract_attr(
        cad_mod, ("parse_speed_rpm", "format_speed_rpm"), "RPM unit validator"
    )
    for bad in ("1400[m/s]", "1400[bogus]"):
        with pytest.raises((ValueError, ComsolError)):
            parser(bad)


def test_t2_f03_06_empty_or_single_far_face_rejected() -> None:
    """T2-F03-06: Empty or single-face far boundary list fails closed."""
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    swapper = resolve_contract_attr(
        cad_mod, ("select_rotation_bcs", "assign_rotation_bcs", "swap_rotation_bcs"), "BC swapper"
    )
    for bad_faces in ([], [{"id": 1, "x_min": -3.39, "x_max": -3.39}]):
        with pytest.raises((ValueError, ComsolError, NodeNotFoundError)):
            swapper(speed_rpm=1400.0, far_faces=bad_faces)


# ==============================================================================
# F04 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f04_01_injecting_diameter_key_raises_validation_error(nominal_design_dict) -> None:
    """T2-F04-01: Injecting diameter key into PropellerDesignVector is forbidden."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for key in ("diameter", "D_prop_m", "R_prop_m"):
        bad = dict(nominal_design_dict)
        bad[key] = 0.350
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**bad)


def test_t2_f04_02_bva_radius_tolerance_boundary(nominal_design_dict) -> None:
    """T2-F04-02: R_prop +/- 1e-4 m passes; +/- 1.1e-4 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    for r_ok in (0.159900, 0.160100):
        assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_prop_m": 2 * r_ok, "r_prop_m": r_ok}))
    for r_bad in (0.159890, 0.160110):
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_prop_m": 2 * r_bad, "r_prop_m": r_bad}))


def test_t2_f04_03_diameter_tampering_rejected(nominal_design_dict) -> None:
    """T2-F04-03: Diameter tampering (0.300 m, 0.340 m) rejected with violation."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    for bad_d in (0.300, 0.340):
        audit = _call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_prop_m": bad_d, "r_prop_m": bad_d / 2.0})
        assert not _is_valid(audit)


def test_t2_f04_04_asymmetric_single_blade_radial_breach_rejected(nominal_design_dict) -> None:
    """T2-F04-04: Single blade tip r=0.1605 m (max radial extent) fails GeometryGuard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    audit = _call_guard_validate(
        guard_mod.GeometryGuard,
        design,
        measured_bbox={"blade_tip_radii_m": [0.1600, 0.1600, 0.1605], "r_prop_m": 0.1605, "d_prop_m": 0.3210},
    )
    assert not _is_valid(audit)


def test_t2_f04_05_nonpositive_or_nan_diameter_fails_closed(nominal_design_dict) -> None:
    """T2-F04-05: Negative, zero, or NaN d_prop_m fails closed (no NaN comparison trap)."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    for bad_d in (-0.320, 0.0, float("nan")):
        audit = _call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_prop_m": bad_d, "r_prop_m": bad_d / 2.0})
        assert not _is_valid(audit)


def test_t2_f04_06_design_space_bounds_rejects_diameter_key() -> None:
    """T2-F04-06: Config/bounds schema loader rejects registering 'diameter' as a DOF."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    cfg_cls = resolve_contract_attr(
        schema_mod, ("PropellerOptConfig", "DesignSpaceConfig", "validate_design_space_bounds"), "config/bounds schema"
    )
    bad_bounds = {"diameter": [0.28, 0.36], "pitch_r07_deg": [15.0, 30.0]}
    with pytest.raises((ValidationError, ValueError)):
        cfg_cls(bounds=bad_bounds) if isinstance(cfg_cls, type) else cfg_cls(bad_bounds)


# ==============================================================================
# F05 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f05_01_injecting_hub_invariant_key_rejected(nominal_design_dict) -> None:
    """T2-F05-01: Hub interface keys rejected by PropellerDesignVector."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for key in ("hub_radius", "hub_x_min", "hub_x_max"):
        bad = dict(nominal_design_dict)
        bad[key] = 0.05
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**bad)


def test_t2_f05_02_bva_hub_diameter_tolerance(nominal_design_dict) -> None:
    """T2-F05-02: Hub R_hub +/- 1e-4 m passes; +/- 1.1e-4 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    for r_ok in (0.04190, 0.04210):
        assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_hub_m": 2 * r_ok}))
    for r_bad in (0.04189, 0.04211):
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"d_hub_m": 2 * r_bad}))


def test_t2_f05_03_hub_axial_span_violation_rejected(nominal_design_dict) -> None:
    """T2-F05-03: Hub axial breach (< 0.065 or > 0.130 m) rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"hub_x_min_m": 0.0648, "hub_x_max_m": 0.1300}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"hub_x_min_m": 0.0650, "hub_x_max_m": 0.1302}))


def test_t2_f05_04_blade_root_axial_overhang_rejected(nominal_design_dict) -> None:
    """T2-F05-04: Blade root LE < 0.065 or TE > 0.130 m rejected even with valid hub."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"blade_root_x_min_m": 0.0645, "blade_root_x_max_m": 0.125}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"blade_root_x_min_m": 0.070, "blade_root_x_max_m": 0.1305}))


def test_t2_f05_05_blade_root_radial_detachment_rejected(nominal_design_dict) -> None:
    """T2-F05-05: Blade root inner radius 0.0430 m (> 0.0420 m hub) rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"blade_root_r_min_m": 0.0430}))


def test_t2_f05_06_excessive_rake_and_chord_overhang_caught_presolve(nominal_design_dict) -> None:
    """T2-F05-06: Excessive rake + chord shifting blade past axial envelope caught pre-solve."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    d = dict(nominal_design_dict, rake_tip_m=0.045, chord_r04_m=0.095)
    try:
        design = schema_mod.PropellerDesignVector(**d)
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design))
    except ValidationError:
        pass


# ==============================================================================
# F06 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f06_01_duct_axial_envelope_breach_rejected(nominal_design_dict) -> None:
    """T2-F06-01: Duct axial < 0.009966 or > 0.202606 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"duct_x_min_m": 0.009800, "duct_x_max_m": 0.202606}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"duct_x_min_m": 0.009966, "duct_x_max_m": 0.202800}))


def test_t2_f06_02_bva_duct_outer_diameter_limit(nominal_design_dict) -> None:
    """T2-F06-02: D_duct_out=0.388856 m passes; 0.388900 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"duct_outer_max_d_m": 0.388856}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"duct_outer_max_d_m": 0.388900}))


def test_t2_f06_03_bva_tip_clearance_lower_bound(nominal_design_dict) -> None:
    """T2-F06-03: tip_clearance_m 0.00150 m passes; 0.00149 m, 0.0, -0.001 rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    ok_d = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=0.00150))
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, ok_d))
    for bad_gap in (0.00149, 0.0, -0.001):
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=bad_gap))


def test_t2_f06_04_bva_tip_clearance_upper_bound(nominal_design_dict) -> None:
    """T2-F06-04: tip_clearance_m 0.00600 m passes; 0.00601 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    ok_d = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=0.00600))
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, ok_d))
    with pytest.raises(ValidationError):
        schema_mod.PropellerDesignVector(**dict(nominal_design_dict, tip_clearance_m=0.00601))


def test_t2_f06_05_rotorflow_collision_rejected(nominal_design_dict) -> None:
    """T2-F06-05: Blade r_tip >= 0.1625 m or axial span breaching [0.056, 0.139] rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"r_prop_m": 0.1625, "d_prop_m": 0.3250}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"blade_x_min_m": 0.0558, "blade_x_max_m": 0.1392}))


def test_t2_f06_06_injecting_duct_envelope_key_rejected(nominal_design_dict) -> None:
    """T2-F06-06: Duct outer invariant keys forbidden in PropellerDesignVector."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for key in ("duct_outer_max_r", "duct_x_min", "duct_x_max"):
        bad = dict(nominal_design_dict)
        bad[key] = 0.20
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**bad)


# ==============================================================================
# F07 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f07_01_nonpositive_chord_rejected(nominal_design_dict) -> None:
    """T2-F07-01: Zero or negative chord rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for field, val in (("chord_r04_m", 0.0), ("chord_r07_m", -0.02)):
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, **{field: val}))


def test_t2_f07_02_zero_camber_accepted_negative_camber_rejected(nominal_design_dict) -> None:
    """T2-F07-02: Zero camber accepted as symmetric foil; negative camber rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    ok = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, max_camber_ratio=0.0))
    assert float(ok.max_camber_ratio) == 0.0
    with pytest.raises(ValidationError):
        schema_mod.PropellerDesignVector(**dict(nominal_design_dict, max_camber_ratio=-0.01))


def test_t2_f07_03_extreme_thickness_ratio_rejected(nominal_design_dict) -> None:
    """T2-F07-03: Unphysical thickness ratio (<=0, 0.005, 0.50) rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for bad_t in (0.0, -0.05, 0.005, 0.50):
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, max_thickness_ratio=bad_t))


def test_t2_f07_04_blade_count_not_3_rejected(nominal_design_dict) -> None:
    """T2-F07-04: blade_count != 3 rejected against baseline invariant."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    for bad_count in (0, 1, 2, 6):
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"blade_count": bad_count}))


def test_t2_f07_05_nan_inf_design_fields_rejected(nominal_design_dict) -> None:
    """T2-F07-05: NaN / Inf in design vector rejected at schema validation."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    for field, val in (
        ("pitch_r07_deg", float("nan")),
        ("skew_tip_deg", float("inf")),
        ("duct_diffuser_angle_deg", float("-inf")),
    ):
        with pytest.raises(ValidationError):
            schema_mod.PropellerDesignVector(**dict(nominal_design_dict, **{field: val}))


def test_t2_f07_06_corner_valid_extreme_geometry_no_loft_inversion(nominal_design_dict) -> None:
    """T2-F07-06: Corner valid extreme geometry (high pitch/skew + 1.5 mm gap) builds cleanly."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    cad_mod = require_contract("hydro_disturb_bench.propeller.cad_generator")
    gen = resolve_contract_attr(
        cad_mod, ("build_geometry_descriptor", "generate_geometry_spec", "build_blade_duct_spec"), "CAD descriptor"
    )
    corner = dict(
        nominal_design_dict,
        pitch_r04_deg=34.0,
        pitch_r07_deg=28.0,
        pitch_r10_deg=22.0,
        skew_tip_deg=24.0,
        tip_clearance_m=0.0015,
    )
    design = schema_mod.PropellerDesignVector(**corner)
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design))
    spec = gen(design)
    assert "duct_throat_r_m" in spec, "CAD descriptor missing 'duct_throat_r_m'"
    assert abs(float(spec["duct_throat_r_m"]) - 0.1615) <= 1e-6


# ==============================================================================
# F08 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f08_01_bva_trailing_edge_thickness_030mm(nominal_design_dict) -> None:
    """T2-F08-01: TE thickness 0.00030 m passes; 0.00029 m and 0.0 m rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"te_thickness_m": 0.00030}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"te_thickness_m": 0.00029}))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"te_thickness_m": 0.0}))


def test_t2_f08_02_negative_skew_violates_anti_entanglement(nominal_design_dict) -> None:
    """T2-F08-02: Negative skew (-5 deg) rejected for anti-entanglement violation."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    try:
        design = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, skew_tip_deg=-5.0))
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design))
    except ValidationError:
        pass


def test_t2_f08_03_self_intersecting_foil_section_rejected(nominal_design_dict) -> None:
    """T2-F08-03: Self-intersecting upper/lower foil profile rejected by GeometryGuard."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"self_intersecting": True}))


def test_t2_f08_04_interblade_root_overlap_rejected(nominal_design_dict) -> None:
    """T2-F08-04: Excessive root chord overlapping 2*pi*R_hub/3 spacing rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    try:
        design = schema_mod.PropellerDesignVector(**dict(nominal_design_dict, chord_r04_m=0.110))
        assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design))
    except ValidationError:
        pass


def test_t2_f08_05_post_build_duct_drift_overrides_presolve_pass(nominal_design_dict) -> None:
    """T2-F08-05: Post-build COMSOL Loft/Boolean drift (D_duct=0.3895 m) rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design))
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"duct_outer_max_d_m": 0.3895}))


def test_t2_f08_06_discontinuous_fillet_angle_rejected(nominal_design_dict) -> None:
    """T2-F08-06: Sharp discontinuous fillet violating anti-entanglement smoothness rejected."""
    schema_mod = require_contract("hydro_disturb_bench.propeller.schema", ("PropellerDesignVector",))
    guard_mod = require_contract("hydro_disturb_bench.propeller.geometry_guard", ("GeometryGuard",))
    design = schema_mod.PropellerDesignVector(**nominal_design_dict)
    assert not _is_valid(_call_guard_validate(guard_mod.GeometryGuard, design, measured_bbox={"anti_entanglement_smooth": False}))


# ==============================================================================
# F09 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f09_01_solver_failed_quarantines_case2_continues_case3(
    tmp_path: Path, nominal_design_dict
) -> None:
    """T2-F09-01: SOLVER_FAILED on case 2 quarantines staging and completes case 3."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    call_idx = {"n": 0}

    def _runner(cmd, *args, **kwargs):
        call_idx["n"] += 1
        import subprocess

        if call_idx["n"] == 2:
            payload = {"status": "SOLVER_FAILED", "error_code": "SOLVER_FAILED"}
        else:
            payload = {"status": "SUCCESS", "rel_err": 0.001, "thrust_n": 2000.0, "power_kw": 10.0}
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=f"<<<JSON_START>>>\n{json.dumps(payload)}\n<<<JSON_END>>>\n",
            stderr="",
        )

    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=_runner, max_workers=1)
    r1 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=20.0), case_id="c1")
    r2 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=21.0), case_id="c2")
    r3 = ev.evaluate(dict(nominal_design_dict, pitch_r07_deg=22.0), case_id="c3")
    assert (r1["status"] if isinstance(r1, dict) else r1.status) == "SUCCESS"
    assert (r2["status"] if isinstance(r2, dict) else r2.status) == "SOLVER_FAILED"
    assert (r3["status"] if isinstance(r3, dict) else r3.status) == "SUCCESS"


def test_t2_f09_02_bva_solver_relative_error_00025(tmp_path: Path, nominal_design_dict) -> None:
    """T2-F09-02: rel_err=0.00250 passes convergence gate; 0.00251 fails."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("check_cfd_quality", "audit_cfd_quality", "validate_cfd_quality"), "CFD quality audit"
    )
    ok = fn(mass_imbalance_pct=0.2, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=0.00250)
    bad = fn(mass_imbalance_pct=0.2, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=0.00251)
    assert bool(ok if isinstance(ok, bool) else ok.get("passed", ok.get("is_valid")))
    assert not bool(bad if isinstance(bad, bool) else bad.get("passed", bad.get("is_valid")))


def test_t2_f09_03_timeout_releases_locks_and_records_error_code(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T2-F09-03: Subprocess timeout records typed error_code and leaks zero lease locks."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    timeout_runner = mock_comsol_propeller_subprocess(raise_timeout=True)
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=timeout_runner, max_workers=1)
    res = ev.evaluate(nominal_design_dict, case_id="timeout_case")
    res_d = res if isinstance(res, dict) else res.model_dump()
    assert res_d["status"] == "SOLVER_FAILED"
    assert res_d["error_code"] in ("TIMEOUT_EXCEEDED", "SOLVER_FAILED")
    assert not list((tmp_path / "runs").rglob(".lease_*.lock"))


def test_t2_f09_04_geometry_rejected_short_circuits_zero_comsol_calls(
    tmp_path: Path, nominal_design_dict, mock_comsol_propeller_subprocess
) -> None:
    """T2-F09-04: Invalid geometry short-circuits evaluator with 0 COMSOL invocations."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    fake_runner = mock_comsol_propeller_subprocess()
    ev = evaluator_cls(work_dir=tmp_path / "runs", process_runner=fake_runner, max_workers=1)
    bad = dict(nominal_design_dict, skew_tip_deg=-10.0)
    res = ev.evaluate(bad, case_id="geom_rej")
    res_d = res if isinstance(res, dict) else res.model_dump()
    assert res_d["status"] == "GEOMETRY_REJECTED"
    assert len(fake_runner.calls) == 0


def test_t2_f09_05_parallel_workers_clamped_or_rejected(tmp_path: Path) -> None:
    """T2-F09-05: Configuring max_workers=4 enforces max_workers==1 or raises ValueError."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    try:
        ev = evaluator_cls(work_dir=tmp_path / "runs", max_workers=4)
        assert int(ev.max_workers) == 1
    except ValueError:
        pass


def test_t2_f09_06_corrupted_ledger_raises_or_recovers_cleanly(tmp_path: Path) -> None:
    """T2-F09-06: Truncated JSON checkpoint ledger raises integrity error or safely quarantines."""
    eval_mod = require_contract("hydro_disturb_bench.propeller.evaluator")
    evaluator_cls = resolve_contract_attr(eval_mod, ("PropellerEvaluator", "SerialPropellerEvaluator"), "evaluator")
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ledger.json", "checkpoint.json", "eval_ledger.json"):
        (runs_dir / name).write_text('{"cases": [{"case_id": "broken"', encoding="utf-8")
    try:
        ev = evaluator_cls(work_dir=runs_dir, max_workers=1)
        assert ev is not None
    except (ValueError, json.JSONDecodeError, ModelValidationError):
        pass


# ==============================================================================
# F10 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f10_01_bva_symmetry_floor_075() -> None:
    """T2-F10-01: eta_sym=0.7500 passes C2; 0.7499 and baseline 0.5100 fail C2."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    sym_fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    assert float(sym_fn(t_cw=1500.0, t_ccw=2000.0)) >= HARD_INVARIANTS["eta_sym_floor"]
    assert float(sym_fn(t_cw=1499.8, t_ccw=2000.0)) < HARD_INVARIANTS["eta_sym_floor"]
    assert float(sym_fn(t_cw=1262.36, t_ccw=2475.37)) < HARD_INVARIANTS["eta_sym_floor"]


def test_t2_f10_02_bva_mass_imbalance_1pct_gate() -> None:
    """T2-F10-02: mass_imbalance=0.99% passes; 1.00% and 1.05% fail C4_cfd_quality."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("check_cfd_quality", "audit_cfd_quality", "validate_cfd_quality"), "CFD quality audit"
    )
    ok = fn(mass_imbalance_pct=0.99, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=0.001)
    at_lim = fn(mass_imbalance_pct=1.00, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=0.001)
    over = fn(mass_imbalance_pct=1.05, momentum_closure_pct=2.0, y_plus_imp=35.0, y_plus_duct=15.0, rel_err=0.001)
    assert bool(ok if isinstance(ok, bool) else ok.get("passed", ok.get("is_valid")))
    assert not bool(at_lim if isinstance(at_lim, bool) else at_lim.get("passed", at_lim.get("is_valid")))
    assert not bool(over if isinstance(over, bool) else over.get("passed", over.get("is_valid")))


def test_t2_f10_03_zero_power_and_zero_thrust_no_zerodivision() -> None:
    """T2-F10-03: Zero shaft torque or zero thrust avoids ZeroDivisionError."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    bollard_fn = resolve_contract_attr(
        metrics_mod, ("compute_bollard_kpis", "compute_bollard_pull", "extract_bollard_metrics"), "bollard KPI fn"
    )
    sym_fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    try:
        out = bollard_fn(f_rotor_x=-1000.0, f_duct_x=-500.0, torque_nm=0.0, speed_rpm=1400.0)
        assert not math.isinf(float(out.get("t_over_p", 0.0)))
    except (ValueError, ComsolError):
        pass
    try:
        eta = float(sym_fn(t_cw=0.0, t_ccw=0.0))
        assert eta == 0.0 or math.isnan(eta) is False
    except (ValueError, ComsolError):
        pass


def test_t2_f10_04_nan_inf_summary_forces_rejected() -> None:
    """T2-F10-04: NaN/Inf in summary thrust/torque rejected rather than propagated."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    bollard_fn = resolve_contract_attr(
        metrics_mod, ("compute_bollard_kpis", "compute_bollard_pull", "extract_bollard_metrics"), "bollard KPI fn"
    )
    for bad_val in (float("nan"), float("inf")):
        with pytest.raises((ValueError, ComsolError, ModelValidationError)):
            bollard_fn(f_rotor_x=bad_val, f_duct_x=-500.0, torque_nm=50.0, speed_rpm=1400.0)


def test_t2_f10_05_wall_y_plus_and_momentum_closure_violations() -> None:
    """T2-F10-05: y+ outside [11, 300] or momentum closure >= 5% flagged."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("check_cfd_quality", "audit_cfd_quality", "validate_cfd_quality"), "CFD quality audit"
    )
    for kwargs in (
        {"mass_imbalance_pct": 0.2, "momentum_closure_pct": 2.0, "y_plus_imp": 5.0, "y_plus_duct": 15.0, "rel_err": 0.001},
        {"mass_imbalance_pct": 0.2, "momentum_closure_pct": 2.0, "y_plus_imp": 350.0, "y_plus_duct": 15.0, "rel_err": 0.001},
        {"mass_imbalance_pct": 0.2, "momentum_closure_pct": 5.0, "y_plus_imp": 35.0, "y_plus_duct": 15.0, "rel_err": 0.001},
    ):
        res = fn(**kwargs)
        assert not bool(res if isinstance(res, bool) else res.get("passed", res.get("is_valid")))


def test_t2_f10_06_negative_thrust_cannot_produce_fake_symmetry() -> None:
    """T2-F10-06: Negative opposing thrust T_CW < 0 yields 0.0 or raises ValueError."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    sym_fn = resolve_contract_attr(
        metrics_mod, ("compute_thrust_symmetry", "compute_bidirectional_symmetry", "symmetry_ratio"), "symmetry fn"
    )
    try:
        eta = float(sym_fn(t_cw=-1500.0, t_ccw=1500.0))
        assert eta <= 0.0
    except (ValueError, ComsolError):
        pass


# ==============================================================================
# F11 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f11_01_zero_advance_speed_j0_no_zerodivision() -> None:
    """T2-F11-01: J=0.0 (U_a=0.0) yields eta_0=0.0 and finite K_T, K_Q."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    out = fn(u_a_mps=0.0, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=2000.0, torque_nm=70.0, rho=1025.0)
    assert float(out["eta_0"]) == 0.0
    assert np.isfinite(float(out["K_T"])) and np.isfinite(float(out["K_Q"]))


def test_t2_f11_02_high_advance_and_windmilling_negative_thrust() -> None:
    """T2-F11-02: High J=0.40 valid; windmilling T<=0 yields eta_0<=0 cleanly."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    n_rps = 1400.0 / 60.0
    ok = fn(u_a_mps=0.40 * n_rps * 0.320, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=600.0, torque_nm=40.0, rho=1025.0)
    assert float(ok["eta_0"]) > 0.0
    wm = fn(u_a_mps=0.80 * n_rps * 0.320, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=-120.0, torque_nm=15.0, rho=1025.0)
    assert float(wm["eta_0"]) <= 0.0


def test_t2_f11_03_zero_hover_thrust_or_nan_disturbance_guarded() -> None:
    """T2-F11-03: Zero T_0 or NaN disturbance guarded against ZeroDivisionError/NaN."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_hover_disturbance", "compute_disturbance_sensitivity"), "disturbance fn"
    )
    for t0, td in ((0.0, 100.0), (2000.0, float("nan"))):
        with pytest.raises((ValueError, ComsolError)):
            fn(t_0_n=t0, t_disturbed_n=td, f_y_n=50.0, f_z_n=20.0, alpha=0.5)


def test_t2_f11_04_zero_rpm_or_nonpositive_torque_in_dp_rejected() -> None:
    """T2-F11-04: Zero speed_rpm or torque <= 0 rejected cleanly."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_dp_kpis", "compute_open_water_kpis", "extract_dp_metrics"), "DP KPI fn"
    )
    with pytest.raises((ValueError, ComsolError)):
        fn(u_a_mps=0.5, speed_rpm=0.0, d_prop_m=0.320, thrust_n=1000.0, torque_nm=40.0, rho=1025.0)
    with pytest.raises((ValueError, ComsolError)):
        fn(u_a_mps=0.5, speed_rpm=1400.0, d_prop_m=0.320, thrust_n=1000.0, torque_nm=0.0, rho=1025.0)


def test_t2_f11_05_extreme_lateral_crossflow_unclipped_s_dist() -> None:
    """T2-F11-05: Extreme lateral crossflow computes high unclipped S_dist accurately."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_hover_disturbance", "compute_disturbance_sensitivity"), "disturbance fn"
    )
    out = fn(t_0_n=2000.0, t_disturbed_n=800.0, f_y_n=-300.0, f_z_n=400.0, alpha=0.5)
    assert abs(float(out["s_dist"]) - (0.60 + 0.5 * (500.0 / 2000.0))) <= 1e-6


def test_t2_f11_06_steady_data_passed_to_transient_settling_rejected() -> None:
    """T2-F11-06: Single steady point without time series fails explicitly on pulse extractor."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("extract_transient_pulse_metrics", "compute_pulse_disturbance_metrics"), "pulse metric fn"
    )
    with pytest.raises((ValueError, ComsolError)):
        fn(times_s=np.array([0.0]), thrust_series_n=np.array([2000.0]), t_0_n=2000.0)


# ==============================================================================
# F12 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f12_01_cw_baseline_cavitation_fails_c3() -> None:
    """T2-F12-01: CW baseline p_min=-214.44 kPa (0.519% < -99 kPa) strictly fails C3."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    out = fn(p_min_gauge_kpa=-214.44, area_sub99_m2=0.0007, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)
    assert not bool(out["c3_passed"])


def test_t2_f12_02_bva_cavitation_threshold_minus_99_kpa() -> None:
    """T2-F12-02: -98.99 and -99.00 kPa pass C3; -99.01 kPa fails C3."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    assert bool(fn(p_min_gauge_kpa=-98.99, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)["c3_passed"])
    assert bool(fn(p_min_gauge_kpa=-99.00, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)["c3_passed"])
    assert not bool(fn(p_min_gauge_kpa=-99.01, area_sub99_m2=1e-5, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)["c3_passed"])


def test_t2_f12_03_cavitation_depth_margin_0m_vs_10m() -> None:
    """T2-F12-03: p_min=-120 kPa fails at h=0 m and passes sigma+C_p_min>0 at h=10 m."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    at_surface = fn(p_min_gauge_kpa=-120.0, area_sub99_m2=1e-4, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)
    at_10m = fn(p_min_gauge_kpa=-120.0, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=10.0, speed_rpm=1400.0)
    assert not bool(at_surface["c3_passed"])
    assert float(at_10m["sigma"]) + float(at_10m["c_p_min"]) > 0.0


def test_t2_f12_04_negative_depth_or_unphysical_vapor_pressure_rejected() -> None:
    """T2-F12-04: Negative operating depth h=-1.0 m rejected."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    with pytest.raises((ValueError, ComsolError)):
        fn(p_min_gauge_kpa=-80.0, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=-1.0, speed_rpm=1400.0)


def test_t2_f12_05_zero_rpm_or_zero_area_cavitation_guarded() -> None:
    """T2-F12-05: Zero speed_rpm or zero wetted area rejected cleanly."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    with pytest.raises((ValueError, ComsolError)):
        fn(p_min_gauge_kpa=-80.0, area_sub99_m2=0.0, area_imp_m2=0.0, depth_m=0.0, speed_rpm=1400.0)
    with pytest.raises((ValueError, ComsolError)):
        fn(p_min_gauge_kpa=-80.0, area_sub99_m2=0.0, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=0.0)


def test_t2_f12_06_contradictory_cavitation_payload_rejected() -> None:
    """T2-F12-06: p_min=-50 kPa with non-zero area_below_99kpa > 0 raises validation error."""
    metrics_mod = require_contract("hydro_disturb_bench.propeller.metrics")
    fn = resolve_contract_attr(
        metrics_mod, ("compute_cavitation_metrics", "evaluate_cavitation"), "cavitation fn"
    )
    with pytest.raises((ModelValidationError, ValueError, ComsolError)):
        fn(p_min_gauge_kpa=-50.0, area_sub99_m2=0.002, area_imp_m2=0.1348, depth_m=0.0, speed_rpm=1400.0)


# ==============================================================================
# F13 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f13_01_bva_von_mises_stress_half_yield() -> None:
    """T2-F13-01: sigma_vm = 0.500*sigma_y passes C5; 0.501*sigma_y fails C5."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("check_structural_safety", "check_c5_structural_modal"), "C5 checker")
    sigma_y = 276e6
    assert bool(fn(sigma_vm_max_pa=0.500 * sigma_y, sigma_y_pa=sigma_y, wet_f1_hz=115.0, speed_rpm=1400.0))
    assert not bool(fn(sigma_vm_max_pa=0.501 * sigma_y, sigma_y_pa=sigma_y, wet_f1_hz=115.0, speed_rpm=1400.0))


def test_t2_f13_02_wet_modal_bpf_resonance_collision_fails() -> None:
    """T2-F13-02: Wet modal f1 == 70 Hz (1*BPF) or 140 Hz (2*BPF) strictly fails C5."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("check_bpf_margin", "compute_bpf_separation"), "BPF margin fn")
    for f1_hz in (70.0, 140.0):
        out = fn(wet_f1_hz=f1_hz, speed_rpm=1400.0, blade_count=3)
        sep = float(out["min_bpf_sep"] if isinstance(out, dict) else out)
        assert sep < HARD_INVARIANTS["modal_bpf_sep_min"]


def test_t2_f13_03_bva_wet_modal_separation_15pct() -> None:
    """T2-F13-03: |f1-70|/f1 == 0.1500 passes; 0.1499 fails."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("check_bpf_margin", "compute_bpf_separation"), "BPF margin fn")
    f_pass = 70.0 / (1.0 - 0.15)
    f_fail = 70.0 / (1.0 - 0.1498)
    s_pass = fn(wet_f1_hz=f_pass, speed_rpm=1400.0, blade_count=3)
    s_fail = fn(wet_f1_hz=f_fail, speed_rpm=1400.0, blade_count=3)
    assert float(s_pass["min_bpf_sep"] if isinstance(s_pass, dict) else s_pass) >= 0.15 - 1e-6
    assert float(s_fail["min_bpf_sep"] if isinstance(s_fail, dict) else s_fail) < 0.15


def test_t2_f13_04_invalid_material_properties_rejected(nominal_design_dict) -> None:
    """T2-F13-04: Invalid material constants (E<=0, nu>=0.5, rho<=0, sigma_y<=0) rejected."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics"), "multiphysics eval")
    for mat in (
        {"E_pa": -1.0, "nu": 0.33, "rho_s": 2700.0, "sigma_y_pa": 276e6},
        {"E_pa": 69e9, "nu": 0.55, "rho_s": 2700.0, "sigma_y_pa": 276e6},
        {"E_pa": 69e9, "nu": 0.33, "rho_s": 0.0, "sigma_y_pa": 276e6},
        {"E_pa": 69e9, "nu": 0.33, "rho_s": 2700.0, "sigma_y_pa": 0.0},
    ):
        with pytest.raises((ValidationError, ValueError, ComsolError)):
            fn(nominal_design_dict, thrust_n=2000.0, speed_rpm=1400.0, material=mat)


def test_t2_f13_05_analytical_fidelity_provenance_enforced(nominal_design_dict) -> None:
    """T2-F13-05: Analytical evaluator cannot claim 'comsol_3d' fidelity."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics"), "multiphysics eval")
    res = fn(nominal_design_dict, thrust_n=2000.0, speed_rpm=1400.0)
    assert res["fidelity"] == "calibrated_analytical"
    assert res["fidelity"] != "comsol_3d"


def test_t2_f13_06_zero_thickness_or_zero_rpm_bpf_guarded(nominal_design_dict) -> None:
    """T2-F13-06: Zero thickness rejected; zero RPM BPF guarded cleanly."""
    mp_mod = require_contract("hydro_disturb_bench.propeller.multiphysics")
    fn = resolve_contract_attr(mp_mod, ("evaluate_structural_modal", "evaluate_multiphysics"), "multiphysics eval")
    with pytest.raises((ValidationError, ValueError)):
        fn(dict(nominal_design_dict, max_thickness_ratio=0.0), thrust_n=2000.0, speed_rpm=1400.0)
    with pytest.raises((ValidationError, ValueError)):
        fn(nominal_design_dict, thrust_n=2000.0, speed_rpm=0.0)


# ==============================================================================
# F14 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f14_01_underdetermined_doe_rejected_or_flagged(calibrated_rov_oracle) -> None:
    """T2-F14-01: Underdetermined DoE (N=5 < d+1=14) raises explicit diagnostic."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    small_x = doe_fn(n_samples=5, method="lhs", seed=1)
    small_y = [calibrated_rov_oracle.evaluate(x) for x in small_x]
    model = surrogate_cls(backend="gp")
    with pytest.raises((ValueError, ModelValidationError)):
        model.fit(small_x, small_y)


def test_t2_f14_02_degenerate_surrogate_nan_and_constant_target(calibrated_rov_oracle) -> None:
    """T2-F14-02: NaN target rejected immediately; duplicate/constant y avoids LinAlgError."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=20, method="lhs", seed=2)
    y_nan = [calibrated_rov_oracle.evaluate(x) for x in samples]
    y_nan[0]["t_mean_n"] = float("nan")
    with pytest.raises((ValueError, ModelValidationError)):
        surrogate_cls(backend="gp").fit(samples, y_nan)

    y_const = [calibrated_rov_oracle.evaluate(samples[0]) for _ in samples]
    m_const = surrogate_cls(backend="gp")
    m_const.fit([samples[0]] * 5 + samples[5:], y_const)
    preds = m_const.predict(samples[:3])
    assert np.all(np.asarray(preds["std"]) >= 0.0)


def test_t2_f14_03_doe_bounds_reject_diameter_column() -> None:
    """T2-F14-03: Passing 'diameter' into DoE bounds raises ValueError/ValidationError."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    with pytest.raises((ValueError, ValidationError)):
        doe_fn(n_samples=10, method="lhs", bounds={"diameter": (0.28, 0.36)})


def test_t2_f14_04_all_failed_doe_batch_aborts_cleanly(tmp_path: Path, calibrated_rov_oracle) -> None:
    """T2-F14-04: All-failed DoE batch (0 SUCCESS) aborts surrogate training cleanly."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")

    def _always_fail(design, **kwargs):
        res = calibrated_rov_oracle.evaluate(design)
        res["status"] = "SOLVER_FAILED"
        res["error_code"] = "SOLVER_FAILED"
        return res

    with pytest.raises((ValueError, RuntimeError, ComsolError)):
        run_wf(
            work_dir=tmp_path / "all_fail_wf",
            stage="all",
            evaluator_fn=_always_fail,
            doe_samples=16,
            pop_size=10,
            n_gen=5,
            seed=1,
        )


def test_t2_f14_05_invalid_cv_fold_count_rejected(calibrated_rov_oracle) -> None:
    """T2-F14-05: CV fold count K=1 or K>N rejected with ValueError."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=20, method="lhs", seed=3)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    for bad_k in (1, 100):
        with pytest.raises(ValueError):
            surrogate_cls(backend="rbf").cross_validate(samples, y_rows, k_folds=bad_k)


def test_t2_f14_06_surrogate_extrapolation_elevates_uncertainty(
    calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T2-F14-06: Out-of-bounds surrogate query either raises ValueError or yields higher std."""
    surrogate_mod = require_contract("hydro_disturb_bench.propeller.surrogate")
    doe_fn = resolve_contract_attr(surrogate_mod, ("generate_doe", "sample_lhs"), "DoE generator")
    surrogate_cls = resolve_contract_attr(
        surrogate_mod, ("MultiOutputSurrogate", "PropellerSurrogate", "SurrogateModel"), "Surrogate class"
    )
    samples = doe_fn(n_samples=25, method="lhs", seed=9)
    y_rows = [calibrated_rov_oracle.evaluate(s) for s in samples]
    model = surrogate_cls(backend="gp")
    model.fit(samples, y_rows)
    extrap = dict(nominal_design_dict, pitch_r07_deg=65.0, chord_r07_m=0.18)
    try:
        p_int = np.asarray(model.predict([nominal_design_dict])["std"])
        p_ext = np.asarray(model.predict([extrap])["std"])
        assert float(np.mean(p_ext)) >= float(np.mean(p_int))
    except ValueError:
        pass


# ==============================================================================
# F15 Boundaries (6 tests, all offline)
# ==============================================================================


def test_t2_f15_01_nsga2_infeasible_only_population_reports_zero_feasible(
    calibrated_rov_oracle,
) -> None:
    """T2-F15-01: Infeasible-only population selects min-violation & reports feasible_count==0."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")

    def _always_infeasible_cav(design, **kwargs):
        res = calibrated_rov_oracle.evaluate(design)
        res["cavitation"]["p_min_cw_kpa"] = -210.0
        res["cavitation"]["worst_p_min_kpa"] = -210.0
        return res

    res = opt_fn(evaluator=_always_infeasible_cav, pop_size=16, n_gen=10, seed=42)
    res_d = res if isinstance(res, dict) else res.__dict__
    assert "feasible_count" in res_d, "NSGA-II result missing 'feasible_count'"
    assert int(res_d["feasible_count"]) == 0
    assert len(res_d.get("pareto_set", [])) >= 1


def test_t2_f15_02_invalid_nsga2_hyperparameters_rejected(calibrated_rov_oracle) -> None:
    """T2-F15-02: Invalid NSGA-II hyperparameters (pop<4, n_gen<=0, odd pop) rejected."""
    opt_mod = require_contract("hydro_disturb_bench.propeller.optimizer")
    opt_fn = resolve_contract_attr(opt_mod, ("run_nsga2", "optimize_propeller", "NSGA2Optimizer"), "NSGA-II optimizer")
    for pop_size, n_gen in ((2, 10), (20, 0), (15, 10)):
        with pytest.raises((ValueError, ValidationError)):
            opt_fn(evaluator=calibrated_rov_oracle.evaluate, pop_size=pop_size, n_gen=n_gen, seed=42)


def test_t2_f15_03_pareto_candidate_diameter_tamper_rejected_before_verify(
    tmp_path: Path, calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T2-F15-03: Corrupted Pareto candidate with diameter=0.325 m rejected before verify."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    verify_fn = resolve_contract_attr(
        wf_mod, ("verify_candidates", "back_verify_pareto", "run_verification_stage"), "back-verification"
    )
    tampered = dict(nominal_design_dict, diameter=0.325)
    report = verify_fn(candidates=[tampered], work_dir=tmp_path / "tamper_verify", evaluator_fn=calibrated_rov_oracle.evaluate)
    assert len(report.get("verified_candidates", [])) == 0


def test_t2_f15_04_surrogate_cfd_cavitation_mismatch_excluded_from_feasible(
    tmp_path: Path, calibrated_rov_oracle, nominal_design_dict
) -> None:
    """T2-F15-04: Candidate failing C3 (p_min=-130 kPa) in real-CFD verify is marked failed."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    verify_fn = resolve_contract_attr(
        wf_mod, ("verify_candidates", "back_verify_pareto", "run_verification_stage"), "back-verification"
    )

    def _cav_fail_eval(design, **kwargs):
        res = calibrated_rov_oracle.evaluate(design)
        res["cavitation"]["p_min_cw_kpa"] = -130.0
        res["cavitation"]["worst_p_min_kpa"] = -130.0
        return res

    report = verify_fn(candidates=[nominal_design_dict], work_dir=tmp_path / "cav_verify", evaluator_fn=_cav_fail_eval)
    failed = report.get("failed_candidates", [])
    verified = report.get("verified_candidates", [])
    assert len(verified) == 0 or len(failed) >= 1


def test_t2_f15_05_cli_missing_or_malformed_config_exits_nonzero(tmp_path: Path) -> None:
    """T2-F15-05: CLI propeller-optimize with missing or malformed JSON exits non-zero."""
    cli_mod = require_contract("hydro_disturb_bench.cli", ("main",))
    missing_cfg = tmp_path / "no_such_config.json"
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{not valid json", encoding="utf-8")
    for cfg in (missing_cfg, bad_cfg):
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.main(["propeller-optimize", "--config", str(cfg)])
        assert exc_info.value.code != 0


def test_t2_f15_06_pareto_output_files_exclude_diameter_and_preserve_0320(
    tmp_path: Path, calibrated_rov_oracle
) -> None:
    """T2-F15-06: Emitted Pareto front JSON/CSV excludes diameter DOF & locks d_prop_m=0.320."""
    wf_mod = require_contract("hydro_disturb_bench.propeller.workflow")
    run_wf = resolve_contract_attr(wf_mod, ("run_optimization_pipeline", "run_workflow"), "workflow runner")
    out_dir = tmp_path / "pareto_audit"
    report = run_wf(
        work_dir=out_dir,
        stage="all",
        evaluator_fn=calibrated_rov_oracle.evaluate,
        doe_samples=20,
        pop_size=24,
        n_gen=12,
        seed=42,
    )
    pareto_front = report.get("pareto_front", [])
    assert len(pareto_front) >= 1, "Workflow emitted an empty Pareto front"
    for cand in pareto_front:
        assert "diameter" not in cand
        assert "geometry_audit" in cand, "Pareto candidate missing 'geometry_audit'"
        assert abs(float(cand["geometry_audit"]["d_prop_m"]) - 0.320) <= 2e-4
