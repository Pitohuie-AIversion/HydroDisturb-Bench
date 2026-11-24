"""Automated CW/CCW baseline reproduction and KPI verification for V10f 3D propeller."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..comsol_java import (
    ModelNotFoundError,
    ModelValidationError,
    compute_file_hash,
    inspect_propeller,
    run_propeller_baseline,
)
from ..io import atomic_json

DEFAULT_CW_MODEL = Path("models/prop test2/COMSOL_CW_1400rpm/V10f-StaticFine-1400rpm.mph")
DEFAULT_CCW_MODEL = Path("models/prop test2/COMSOL_CCW_1400rpm/V10f-CCWFine-1400rpm.mph")
DEFAULT_CAD_PATH = Path("models/prop test2/3_320kgf_v10f.x_t")
DEFAULT_OUT_DIR = Path("runs/propeller_opt/baseline")

FROZEN_SHA256: dict[str, str] = {
    "cw_mph": "C8968A46A4BE1398AAAB377CCA21CE1BF6E581CFACE1710774B676E5D04E41D4",
    "ccw_mph": "194072E12EF86E433B3F857257A8EAD598BEF82E17E1403D547BE247178B1205",
    "cad_xt": "0340421D166328C83B7961346867F98F728ADABDD3832F9632A8D2AD8CB48A99",
}

REFERENCE_KPIS: dict[str, Any] = {
    "cw": {
        "speed_rpm": 1400.0,
        "F_rotor_x_N": 679.774302,
        "F_duct_x_N": 582.581660,
        "F_total_x_N": 1262.35596,
        "thrust_N": 1262.35596,
        "drive_torque_x_Nm": 55.1072794,
        "shaft_power_kW": 8.07914913,
        "thrust_per_power_N_per_kW": 156.2486,
        "p_min_imp_Pa": -214435.0843,
        "cav_area_99_pct": 0.5188,
        "mass_imbalance_plane_pct": 0.29978,
        "mass_imbalance_gross_pct": 0.028056,
        "momentum_closure_pct": 4.0306,
    },
    "ccw": {
        "speed_rpm": -1400.0,
        "F_rotor_x_N": -1387.09990,
        "F_duct_x_N": -1088.27139,
        "F_total_x_N": -2475.37129,
        "thrust_N": 2475.37129,
        "drive_torque_x_Nm": -84.0689701,
        "shaft_power_kW": 12.3251547,
        "thrust_per_power_N_per_kW": 200.8390,
        "p_min_imp_Pa": -84763.43641,
        "cav_area_99_pct": 0.0,
        "mass_imbalance_plane_pct": 0.000567,
        "mass_imbalance_gross_pct": 0.000070,
        "momentum_closure_pct": 0.5201,
    },
    "symmetry_ratio": 0.5099663,
}


def _write_json_safe(path: Path, data: Any, *, allow_overwrite: bool) -> None:
    if allow_overwrite and path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    else:
        atomic_json(path, data)


def _check_finite(value: Any, label: str, failures: list[str]) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        failures.append(f"{label}: non-numeric value {value!r}")
        return None
    if not math.isfinite(num):
        failures.append(f"{label}: non-finite value {value!r}")
        return None
    return num


def verify_baseline_kpis(
    cw_res: dict[str, Any],
    ccw_res: dict[str, Any],
    *,
    rel_tol_force: float = 0.02,
    rel_tol_pmin: float = 0.05,
    max_solver_err: float = 0.0025,
    max_mass_imbalance_pct: float = 1.0,
    max_momentum_closure_pct: float = 6.0,
    sym_target: float = 0.5100,
    sym_tol: float = 0.015,
) -> dict[str, Any]:
    """Verify CW/CCW baseline KPIs, signs, BC coupling, conservation, and symmetry."""
    failures: list[str] = []
    checks: dict[str, Any] = {}

    for label, res, ref in (
        ("cw", cw_res, REFERENCE_KPIS["cw"]),
        ("ccw", ccw_res, REFERENCE_KPIS["ccw"]),
    ):
        kpis = res.get("kpis", {})
        flow = res.get("flow_audit", {})
        cav = res.get("cavitation", {})
        solver = res.get("solver_stats", {})
        bc = res.get("bc_selection", {})

        rpm = _check_finite(res.get("speed_rpm"), f"{label}.speed_rpm", failures)
        expected_up = [29] if label == "cw" else [1]
        expected_open = [1, 2, 3] if label == "cw" else [2, 3, 29]
        if bc.get("upstream") != expected_up or bc.get("open") != expected_open:
            failures.append(
                f"{label}.bc_selection mismatch: got upstream={bc.get('upstream')}, "
                f"open={bc.get('open')}, expected upstream={expected_up}, open={expected_open}"
            )

        if rpm is not None:
            if label == "cw" and rpm <= 0.0:
                failures.append(f"cw.speed_rpm must be positive, got {rpm}")
            if label == "ccw" and rpm >= 0.0:
                failures.append(f"ccw.speed_rpm must be negative, got {rpm}")

        rel_errors: dict[str, float] = {}
        for metric in (
            "F_rotor_x_N",
            "F_duct_x_N",
            "F_total_x_N",
            "thrust_N",
            "drive_torque_x_Nm",
            "shaft_power_kW",
            "thrust_per_power_N_per_kW",
        ):
            val = _check_finite(kpis.get(metric), f"{label}.kpis.{metric}", failures)
            ref_val = float(ref[metric])
            if val is not None:
                rel_err = abs(val - ref_val) / max(1e-12, abs(ref_val))
                rel_errors[metric] = rel_err
                if rel_err > rel_tol_force:
                    failures.append(
                        f"{label}.kpis.{metric}={val:.6g} differs from reference {ref_val:.6g} "
                        f"by {rel_err * 100:.3f}% (> {rel_tol_force * 100:.2f}%)"
                    )

        p_min = _check_finite(cav.get("p_min_imp_Pa"), f"{label}.cavitation.p_min_imp_Pa", failures)
        ref_pmin = float(ref["p_min_imp_Pa"])
        if p_min is not None:
            pmin_rel = abs(p_min - ref_pmin) / max(1e-12, abs(ref_pmin))
            rel_errors["p_min_imp_Pa"] = pmin_rel
            if pmin_rel > rel_tol_pmin:
                failures.append(
                    f"{label}.cavitation.p_min_imp_Pa={p_min:.3f} differs from {ref_pmin:.3f} "
                    f"by {pmin_rel * 100:.3f}% (> {rel_tol_pmin * 100:.2f}%)"
                )

        imb_plane = _check_finite(
            flow.get("mass_imbalance_plane_pct"), f"{label}.flow_audit.mass_imbalance_plane_pct", failures
        )
        if imb_plane is not None and imb_plane >= max_mass_imbalance_pct:
            failures.append(
                f"{label}.flow_audit.mass_imbalance_plane_pct={imb_plane:.4f}% >= {max_mass_imbalance_pct:.2f}%"
            )

        imb_gross = _check_finite(
            flow.get("mass_imbalance_gross_pct"), f"{label}.flow_audit.mass_imbalance_gross_pct", failures
        )
        if imb_gross is not None and imb_gross >= 0.1:
            failures.append(
                f"{label}.flow_audit.mass_imbalance_gross_pct={imb_gross:.5f}% >= 0.1%"
            )

        mom_err = _check_finite(
            flow.get("momentum_closure_pct"), f"{label}.flow_audit.momentum_closure_pct", failures
        )
        if mom_err is not None and mom_err >= max_momentum_closure_pct:
            failures.append(
                f"{label}.flow_audit.momentum_closure_pct={mom_err:.3f}% >= {max_momentum_closure_pct:.2f}%"
            )

        s_err = _check_finite(solver.get("max_solver_err"), f"{label}.solver_stats.max_solver_err", failures)
        if s_err is not None and s_err > max_solver_err:
            failures.append(
                f"{label}.solver_stats.max_solver_err={s_err} > {max_solver_err}"
            )
        if solver.get("converged") is not True:
            failures.append(f"{label}.solver_stats.converged is not True")

        checks[label] = {
            "rel_errors": rel_errors,
            "max_solver_err": s_err,
            "mass_imbalance_plane_pct": imb_plane,
            "mass_imbalance_gross_pct": imb_gross,
            "momentum_closure_pct": mom_err,
        }

    cw_t = _check_finite(cw_res.get("kpis", {}).get("thrust_N"), "cw.kpis.thrust_N", failures)
    ccw_t = _check_finite(ccw_res.get("kpis", {}).get("thrust_N"), "ccw.kpis.thrust_N", failures)
    eta_sym: float | None = None
    if cw_t is not None and ccw_t is not None and max(cw_t, ccw_t) > 0.0:
        eta_sym = min(cw_t, ccw_t) / max(cw_t, ccw_t)
        if abs(eta_sym - sym_target) > sym_tol:
            failures.append(
                f"eta_sym={eta_sym:.5f} outside expected {sym_target:.4f} ± {sym_tol:.4f}"
            )

    return {
        "passed": len(failures) == 0,
        "eta_sym": eta_sym,
        "checks": checks,
        "failures": failures,
    }


def reproduce_propeller_baselines(
    *,
    cw_model: Path | str = DEFAULT_CW_MODEL,
    ccw_model: Path | str = DEFAULT_CCW_MODEL,
    cad_path: Path | str | None = DEFAULT_CAD_PATH,
    output_dir: Path | str = DEFAULT_OUT_DIR,
    solver_mode: str = "warm_fine",
    save_mph: bool = False,
    allow_overwrite: bool = False,
    inspect_only: bool = False,
    pin_hashes: bool = True,
    lock_dir: Path | str | None = None,
    root: Path | None = None,
    process_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Inspect and reproduce CW (+1400 rpm) & CCW (-1400 rpm) baselines safely via CoW."""
    cw_path = Path(cw_model)
    ccw_path = Path(ccw_model)
    cad_file = Path(cad_path) if cad_path is not None else None
    out_dir = Path(output_dir)

    if not cw_path.is_file():
        raise ModelNotFoundError(f"CW baseline model not found: {cw_path}")
    if not ccw_path.is_file():
        raise ModelNotFoundError(f"CCW baseline model not found: {ccw_path}")
    if cad_file is not None and not cad_file.is_file():
        raise ModelNotFoundError(f"Baseline CAD file not found: {cad_file}")

    pre_hashes = {
        "cw_mph": compute_file_hash(cw_path),
        "ccw_mph": compute_file_hash(ccw_path),
    }
    if cad_file is not None:
        pre_hashes["cad_xt"] = compute_file_hash(cad_file)

    if pin_hashes:
        for key, actual in pre_hashes.items():
            expected = FROZEN_SHA256[key]
            if actual != expected:
                raise ModelValidationError(
                    f"Baseline SHA-256 mismatch for {key}: expected {expected}, got {actual}"
                )

    out_dir.mkdir(parents=True, exist_ok=True)
    effective_lock_dir = Path(lock_dir) if lock_dir is not None else out_dir.parent / ".locks"

    cw_inspect = inspect_propeller(
        cw_path,
        expected_sha256=pre_hashes["cw_mph"],
        root=root,
        process_runner=process_runner,
    )
    ccw_inspect = inspect_propeller(
        ccw_path,
        expected_sha256=pre_hashes["ccw_mph"],
        root=root,
        process_runner=process_runner,
    )
    _write_json_safe(
        out_dir / "cw_1400rpm_inspect.json", cw_inspect, allow_overwrite=allow_overwrite
    )
    _write_json_safe(
        out_dir / "ccw_1400rpm_inspect.json", ccw_inspect, allow_overwrite=allow_overwrite
    )

    cw_run: dict[str, Any] | None = None
    ccw_run: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None

    if not inspect_only:
        cw_run = run_propeller_baseline(
            cw_path,
            out_dir / "cw_1400rpm_repro.mph",
            direction="cw",
            speed_rpm=1400.0,
            solver_mode=solver_mode,
            save_model=save_mph,
            allow_overwrite=allow_overwrite,
            expected_source_sha256=pre_hashes["cw_mph"],
            lock_dir=effective_lock_dir,
            root=root,
            process_runner=process_runner,
        )
        ccw_run = run_propeller_baseline(
            ccw_path,
            out_dir / "ccw_1400rpm_repro.mph",
            direction="ccw",
            speed_rpm=-1400.0,
            solver_mode=solver_mode,
            save_model=save_mph,
            allow_overwrite=allow_overwrite,
            expected_source_sha256=pre_hashes["ccw_mph"],
            lock_dir=effective_lock_dir,
            root=root,
            process_runner=process_runner,
        )
        verification = verify_baseline_kpis(cw_run, ccw_run)

    post_hashes = {
        "cw_mph": compute_file_hash(cw_path),
        "ccw_mph": compute_file_hash(ccw_path),
    }
    if cad_file is not None:
        post_hashes["cad_xt"] = compute_file_hash(cad_file)

    if post_hashes != pre_hashes:
        raise ModelValidationError(
            f"Safety violation: baseline asset hashes changed! Before={pre_hashes}, After={post_hashes}"
        )

    summary: dict[str, Any] = {
        "status": "SUCCESS" if (verification is None or verification["passed"]) else "FAILED",
        "inspect_only": inspect_only,
        "solver_mode": solver_mode,
        "immutability_verified": True,
        "pre_sha256": pre_hashes,
        "post_sha256": post_hashes,
        "cw_inspect": {
            "model_path": str(cw_path),
            "sha256": cw_inspect.get("sha256"),
            "geometry_audit": cw_inspect.get("geometry_audit"),
            "physics_bcs": cw_inspect.get("physics_bcs"),
        },
        "ccw_inspect": {
            "model_path": str(ccw_path),
            "sha256": ccw_inspect.get("sha256"),
            "geometry_audit": ccw_inspect.get("geometry_audit"),
            "physics_bcs": ccw_inspect.get("physics_bcs"),
        },
        "cw_baseline": cw_run,
        "ccw_baseline": ccw_run,
        "reference_kpis": REFERENCE_KPIS,
        "verification": verification,
    }
    _write_json_safe(
        out_dir / "baseline_summary.json", summary, allow_overwrite=allow_overwrite
    )
    summary["summary_json"] = str((out_dir / "baseline_summary.json").resolve())
    return summary
