"""Run genuine serial CW (+1400 rpm) and CCW (-1400 rpm) live COMSOL verification.

Executes cold-start steady-state CFD solves with full native COMSOL parametric
Parasolid geometry + mesh reconstruction, baseline SHA-256 immutability guards,
strict serial execution (`max_workers=1` via `comsol_solver_lock`), and
COMSOL-integrated field/KPI/cavitation/surface-CSV extraction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hydro_disturb_bench.comsol_java.bridge import (
    compute_file_hash,
    ensure_bridge_compiled,
    eval_propeller,
)
from hydro_disturb_bench.propeller.geometry_guard import GeometryGuard
from hydro_disturb_bench.propeller.metrics import (
    aggregate_bidirectional_cavitation,
    check_cfd_quality,
    compute_bollard_kpis,
    compute_cavitation_metrics,
    compute_thrust_symmetry,
)

PINNED_CW_MODEL = Path("models/prop test2/COMSOL_CW_1400rpm/V10f-StaticFine-1400rpm.mph")
PINNED_CW_SHA256 = "C8968A46A4BE1398AAAB377CCA21CE1BF6E581CFACE1710774B676E5D04E41D4"

PINNED_CCW_MODEL = Path("models/prop test2/COMSOL_CCW_1400rpm/V10f-CCWFine-1400rpm.mph")
PINNED_CCW_SHA256 = "194072E12EF86E433B3F857257A8EAD598BEF82E17E1403D547BE247178B1205"

DEFAULT_OUTPUT_DIR = Path("runs/propeller_opt/v10f_opt/live_comsol_eval")

# Exact 13 numerical parameters specified in ORIGINAL_REQUEST.md (R1).
# Provenance note: in runs/propeller_opt/v10f_opt/pareto_front.csv, these exact
# 13 values appear in Row 2 (candidate_id = case_1794962444842), whereas the
# prompt label `case_1794962444986` corresponds to Row 23.
R1_CANDIDATE_PARAMS: dict[str, float] = {
    "pitch_r04_deg": 27.556,
    "pitch_r07_deg": 38.586,
    "pitch_r10_deg": 32.486,
    "chord_r04_m": 0.0802,
    "chord_r07_m": 0.0599,
    "chord_r10_m": 0.0706,
    "max_camber_ratio": 0.0325,
    "max_thickness_ratio": 0.0912,
    "skew_tip_deg": 20.507,
    "rake_tip_m": 0.00946,
    "tip_clearance_m": 0.00193,
    "duct_inlet_lip_ratio": 1.080,
    "duct_diffuser_angle_deg": -0.672,
}

PROVENANCE_NOTE = {
    "prompt_label": "case_1794962444986",
    "matched_pareto_row": 2,
    "matched_pareto_candidate_id": "case_1794962444842",
    "source_csv": "runs/propeller_opt/v10f_opt/pareto_front.csv",
    "note": (
        "Evaluated with the exact 13 numerical parameter values from ORIGINAL_REQUEST.md R1. "
        "Those exact 13 values match Row 2 (`case_1794962444842`) in `pareto_front.csv`, "
        "while the prompt label `case_1794962444986` refers to Row 23."
    ),
}

BASELINE_REFERENCE = {
    "cw_thrust_N": 1262.36,
    "ccw_thrust_N": 2475.37,
    "symmetry_ratio": 0.5100,
}

DEFAULT_SOLVER_OVERRIDES: dict[str, Any] = {
    "warm_start": "init",
    "stol": 0.0025,
    "segcflaa": "on",
    "submincfl": 10000.0,
    "subdamp": 0.45,
    "maxiter": 400,
    "strict_convergence": True,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarize_surface_csv(csv_path: Path) -> dict[str, Any]:
    """Read COMSOL ExportFeature Data CSV and verify coordinate/pressure statistics."""
    if not csv_path.is_file():
        return {"exists": False, "path": str(csv_path)}

    n_nodes = 0
    x_min = math.inf
    x_max = -math.inf
    r_min = math.inf
    r_max = -math.inf
    p_min_pa = math.inf
    p_max_pa = -math.inf
    header_line = ""

    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("%"):
                header_line = line
                continue
            parts = next(csv.reader([line]))
            if len(parts) < 4:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
                z = float(parts[2])
                p = float(parts[3])
            except ValueError:
                continue
            r = math.hypot(y, z)
            x_min = min(x_min, x)
            x_max = max(x_max, x)
            r_min = min(r_min, r)
            r_max = max(r_max, r)
            p_min_pa = min(p_min_pa, p)
            p_max_pa = max(p_max_pa, p)
            n_nodes += 1

    return {
        "exists": True,
        "path": str(csv_path),
        "size_bytes": csv_path.stat().st_size,
        "header": header_line,
        "node_count": n_nodes,
        "x_min_m": x_min if n_nodes else None,
        "x_max_m": x_max if n_nodes else None,
        "r_min_m": r_min if n_nodes else None,
        "r_max_m": r_max if n_nodes else None,
        "p_min_Pa": p_min_pa if n_nodes else None,
        "p_max_Pa": p_max_pa if n_nodes else None,
    }


def _audit_direction(direction_label: str, res: dict[str, Any], speed_rpm: float) -> dict[str, Any]:
    """Compute canonical metrics, CFD quality audit, cavitation metrics, and geometry guard."""
    kpis_raw = res["kpis"]
    flow = res["flow_audit"]
    cav_raw = res["cavitation"]
    solver_stats = res["solver_stats"]
    geom_audit = dict(res.get("geometry_audit") or {})
    geom_audit.setdefault("tip_clearance_m", R1_CANDIDATE_PARAMS["tip_clearance_m"])
    if "hub_x_span" not in geom_audit and "hub_x_min_m" in geom_audit and "hub_x_max_m" in geom_audit:
        geom_audit["hub_x_span"] = [geom_audit["hub_x_min_m"], geom_audit["hub_x_max_m"]]

    bollard = compute_bollard_kpis(
        f_rotor_x=float(kpis_raw["F_rotor_x_N"]),
        f_duct_x=float(kpis_raw["F_duct_x_N"]),
        torque_nm=float(kpis_raw["drive_torque_x_Nm"]),
        speed_rpm=speed_rpm,
    )
    cfd_quality = check_cfd_quality(
        mass_imbalance_pct=float(flow["mass_imbalance_gross_pct"]),
        momentum_closure_pct=float(flow["momentum_closure_pct"]),
        y_plus_imp=float(kpis_raw["y_plus_imp"]),
        y_plus_duct=float(kpis_raw["y_plus_duct"]),
        rel_err=float(solver_stats["max_solver_err"]) if solver_stats.get("max_solver_err") is not None else None,
    )
    cav_metrics = compute_cavitation_metrics(
        p_min_gauge_kpa=float(cav_raw["p_min_imp_Pa"]) / 1000.0,
        area_sub99_m2=float(cav_raw["cav_area_99_m2"]),
        area_imp_m2=float(cav_raw["impeller_area_m2"]),
        depth_m=0.0,
        speed_rpm=speed_rpm,
    )
    guard_audit = GeometryGuard.validate(R1_CANDIDATE_PARAMS, measured_bbox=geom_audit).model_dump()

    surf_exports = res.get("surface_exports") or {}
    imp_csv = Path(surf_exports["impeller_csv"]) if surf_exports.get("impeller_csv") else Path()
    noz_csv = Path(surf_exports["nozzle_csv"]) if surf_exports.get("nozzle_csv") else Path()

    return {
        "direction": direction_label,
        "speed_rpm": speed_rpm,
        "bollard_kpis": bollard,
        "force_torque_split": {
            "F_rotor_x_N": kpis_raw["F_rotor_x_N"],
            "F_rotor_pressure_x_N": kpis_raw["F_rotor_pressure_x_N"],
            "F_rotor_viscous_x_N": kpis_raw["F_rotor_viscous_x_N"],
            "F_duct_x_N": kpis_raw["F_duct_x_N"],
            "F_duct_pressure_x_N": kpis_raw["F_duct_pressure_x_N"],
            "F_duct_viscous_x_N": kpis_raw["F_duct_viscous_x_N"],
            "F_total_x_N": kpis_raw["F_total_x_N"],
            "drive_torque_x_Nm": kpis_raw["drive_torque_x_Nm"],
            "drive_torque_pressure_x_Nm": kpis_raw["drive_torque_pressure_x_Nm"],
            "drive_torque_viscous_x_Nm": kpis_raw["drive_torque_viscous_x_Nm"],
        },
        "cfd_quality": cfd_quality,
        "flow_audit": flow,
        "cavitation_metrics": cav_metrics,
        "cavitation_multiband": cav_raw,
        "solver_stats": solver_stats,
        "mesh": res.get("mesh"),
        "geometry_guard": guard_audit,
        "geometry_audit": geom_audit,
        "bc_selection": res.get("bc_selection"),
        "surface_csv_summaries": {
            "impeller": _summarize_surface_csv(imp_csv) if surf_exports.get("impeller_csv") else {"exists": False},
            "nozzle": _summarize_surface_csv(noz_csv) if surf_exports.get("nozzle_csv") else {"exists": False},
        },
        "output_model": res.get("output_model"),
        "output_sha256": res.get("output_sha256"),
        "wall_time_s": res.get("wall_time_s"),
    }


def _render_markdown_report(summary: dict[str, Any]) -> str:
    cw = summary["cw"]
    ccw = summary["ccw"]
    bi = summary["bidirectional"]
    bl = summary["baseline_verification"]

    cw_t = cw["bollard_kpis"]["thrust_n"]
    ccw_t = ccw["bollard_kpis"]["thrust_n"]
    cw_q = cw["bollard_kpis"]["torque_nm"]
    ccw_q = ccw["bollard_kpis"]["torque_nm"]
    cw_p = cw["bollard_kpis"]["power_kw"]
    ccw_p = ccw["bollard_kpis"]["power_kw"]
    cw_cav = cw["cavitation_multiband"]
    ccw_cav = ccw["cavitation_multiband"]

    lines = [
        "# Live COMSOL Verification Report — `case_1794962444986` (R1–R4)",
        "",
        f"- **Generated (UTC)**: `{summary['generated_at_utc']}`",
        f"- **Provenance**: Prompt label `{PROVENANCE_NOTE['prompt_label']}`; exact 13 R1 values match `{PROVENANCE_NOTE['source_csv']}` Row `{PROVENANCE_NOTE['matched_pareto_row']}` (`{PROVENANCE_NOTE['matched_pareto_candidate_id']}`).",
        f"- **Execution Mode**: Strict serial (`max_workers=1`) under `comsol_solver_lock`, cold start (`initmethod=init`), `stol=0.0025`, `strict_convergence=true`.",
        "",
        "## 1. Baseline Immutability Verification (SHA-256)",
        "",
        "| Model | Pinned Expected SHA-256 | Pre-Run SHA-256 | Post-Run SHA-256 | Unchanged |",
        "|---|---|---|---|---|",
        f"| CW (`{bl['cw']['path']}`) | `{bl['cw']['expected_sha256']}` | `{bl['cw']['pre_sha256']}` | `{bl['cw']['post_sha256']}` | `{bl['cw']['unchanged']}` |",
        f"| CCW (`{bl['ccw']['path']}`) | `{bl['ccw']['expected_sha256']}` | `{bl['ccw']['pre_sha256']}` | `{bl['ccw']['post_sha256']}` | `{bl['ccw']['unchanged']}` |",
        "",
        "## 2. Parametric Geometry & Mesh Verification",
        "",
        "| Metric | CW (+1400 rpm) | CCW (-1400 rpm) | Constraint / Target |",
        "|---|---|---|---|",
        f"| GeometryGuard valid | `{cw['geometry_guard']['is_valid']}` | `{ccw['geometry_guard']['is_valid']}` | `True` |",
        f"| `R_prop_max` (m) | `{cw['geometry_audit']['r_prop_max_m']:.6f}` | `{ccw['geometry_audit']['r_prop_max_m']:.6f}` | `0.160000 +/- 0.000100` |",
        f"| `D_hub` (m) | `{cw['geometry_audit']['d_hub_m']:.6f}` | `{ccw['geometry_audit']['d_hub_m']:.6f}` | `0.084000` (`x in [0.065, 0.130]`) |",
        f"| `D_duct,out,max` (m) | `{cw['geometry_audit']['duct_outer_max_d_m']:.6f}` | `{ccw['geometry_audit']['duct_outer_max_d_m']:.6f}` | `<= 0.388856` |",
        f"| `r_rotorflow` (m) | `{cw['geometry_audit']['moving_domain_bounds']['r_max_m']:.6f}` | `{ccw['geometry_audit']['moving_domain_bounds']['r_max_m']:.6f}` | `0.160965` (`R_prop + 0.5*tip_clearance`) |",
        f"| Mesh Complete | `{cw['mesh']['is_complete']}` | `{ccw['mesh']['is_complete']}` | `True` |",
        f"| Mesh Elements / Vertices | `{cw['mesh']['num_elem']}` / `{cw['mesh']['num_vertex']}` | `{ccw['mesh']['num_elem']}` / `{ccw['mesh']['num_vertex']}` | `> 0` |",
        f"| Mesh Min / Mean Quality | `{cw['mesh']['min_quality']:.6e}` / `{cw['mesh']['mean_quality']:.4f}` | `{ccw['mesh']['min_quality']:.6e}` / `{ccw['mesh']['mean_quality']:.4f}` | `> 0` |",
        "",
        "## 3. Hydrodynamic Performance & Solver Convergence",
        "",
        "| Metric | CW (+1400 rpm) | CCW (-1400 rpm) | V10f Baseline Reference |",
        "|---|---|---|---|",
        f"| Total Thrust `|F_total_x|` (N) | **{cw_t:.2f}** | **{ccw_t:.2f}** | CW `{BASELINE_REFERENCE['cw_thrust_N']:.2f}`, CCW `{BASELINE_REFERENCE['ccw_thrust_N']:.2f}` |",
        f"| Rotor Thrust `F_rotor_x` (N) [P / Visc] | `{cw['force_torque_split']['F_rotor_x_N']:.2f}` [`{cw['force_torque_split']['F_rotor_pressure_x_N']:.2f}` / `{cw['force_torque_split']['F_rotor_viscous_x_N']:.2f}`] | `{ccw['force_torque_split']['F_rotor_x_N']:.2f}` [`{ccw['force_torque_split']['F_rotor_pressure_x_N']:.2f}` / `{ccw['force_torque_split']['F_rotor_viscous_x_N']:.2f}`] | — |",
        f"| Duct Thrust `F_duct_x` (N) [P / Visc] | `{cw['force_torque_split']['F_duct_x_N']:.2f}` [`{cw['force_torque_split']['F_duct_pressure_x_N']:.2f}` / `{cw['force_torque_split']['F_duct_viscous_x_N']:.2f}`] | `{ccw['force_torque_split']['F_duct_x_N']:.2f}` [`{ccw['force_torque_split']['F_duct_pressure_x_N']:.2f}` / `{ccw['force_torque_split']['F_duct_viscous_x_N']:.2f}`] | — |",
        f"| Drive Torque `|M_x|` (N*m) [P / Visc] | `{cw_q:.2f}` [`{cw['force_torque_split']['drive_torque_pressure_x_Nm']:.2f}` / `{cw['force_torque_split']['drive_torque_viscous_x_Nm']:.2f}`] | `{ccw_q:.2f}` [`{ccw['force_torque_split']['drive_torque_pressure_x_Nm']:.2f}` / `{ccw['force_torque_split']['drive_torque_viscous_x_Nm']:.2f}`] | — |",
        f"| Shaft Power (kW) | `{cw_p:.3f}` | `{ccw_p:.3f}` | — |",
        f"| Thrust / Power (N/kW) | `{cw['bollard_kpis']['t_over_p']:.2f}` | `{ccw['bollard_kpis']['t_over_p']:.2f}` | — |",
        f"| Thrust Symmetry `min(CW,CCW)/max(CW,CCW)` | **{bi['thrust_symmetry_ratio']:.4f}** | **{bi['thrust_symmetry_ratio']:.4f}** | `{BASELINE_REFERENCE['symmetry_ratio']:.4f}` |",
        f"| Solver Converged / Iterations | `{cw['solver_stats']['converged']}` / `{cw['solver_stats']['iterations']}` | `{ccw['solver_stats']['converged']}` / `{ccw['solver_stats']['iterations']}` | `stol=0.0025` |",
        f"| Max Solver Rel. Error | `{cw['solver_stats']['max_solver_err']:.6f}` | `{ccw['solver_stats']['max_solver_err']:.6f}` | `<= 0.0025` |",
        f"| Mass Imbalance Gross (%) | `{cw['flow_audit']['mass_imbalance_gross_pct']:.4f}%` | `{ccw['flow_audit']['mass_imbalance_gross_pct']:.4f}%` | `< 1.0%` |",
        f"| Momentum Closure (%) | `{cw['flow_audit']['momentum_closure_pct']:.4f}%` | `{ccw['flow_audit']['momentum_closure_pct']:.4f}%` | `< 5.0%` |",
        f"| Wall `y+` (impeller / nozzle) | `{cw['cfd_quality']['y_plus_imp']:.2f}` / `{cw['cfd_quality']['y_plus_duct']:.2f}` | `{ccw['cfd_quality']['y_plus_imp']:.2f}` / `{ccw['cfd_quality']['y_plus_duct']:.2f}` | `[11, 300]` |",
        "",
        "## 4. Cavitation & Multi-Band Negative Pressure Distribution",
        "",
        "| Metric | CW (+1400 rpm) | CCW (-1400 rpm) |",
        "|---|---|---|",
        f"| `p_min_imp` (kPa) | `{cw['cavitation_metrics']['p_min_gauge_kpa']:.3f}` | `{ccw['cavitation_metrics']['p_min_gauge_kpa']:.3f}` |",
        f"| `p_max_imp` (kPa) | `{cw_cav['p_max_imp_Pa'] / 1000.0:.3f}` | `{ccw_cav['p_max_imp_Pa'] / 1000.0:.3f}` |",
        f"| Impeller Area (`m^2`) | `{cw_cav['impeller_area_m2']:.6f}` | `{ccw_cav['impeller_area_m2']:.6f}` |",
        f"| `p < 0 kPa` area (`m^2` / %) | `{cw_cav['cav_area_0_m2']:.6f}` ({cw_cav['cav_area_0_pct']:.2f}%) | `{ccw_cav['cav_area_0_m2']:.6f}` ({ccw_cav['cav_area_0_pct']:.2f}%) |",
        f"| `p < -25 kPa` area (`m^2` / %) | `{cw_cav['cav_area_25_m2']:.6f}` ({cw_cav['cav_area_25_pct']:.2f}%) | `{ccw_cav['cav_area_25_m2']:.6f}` ({ccw_cav['cav_area_25_pct']:.2f}%) |",
        f"| `p < -50 kPa` area (`m^2` / %) | `{cw_cav['cav_area_50_m2']:.6f}` ({cw_cav['cav_area_50_pct']:.2f}%) | `{ccw_cav['cav_area_50_m2']:.6f}` ({ccw_cav['cav_area_50_pct']:.2f}%) |",
        f"| `p < -75 kPa` area (`m^2` / %) | `{cw_cav['cav_area_75_m2']:.6f}` ({cw_cav['cav_area_75_pct']:.2f}%) | `{ccw_cav['cav_area_75_m2']:.6f}` ({ccw_cav['cav_area_75_pct']:.2f}%) |",
        f"| `p < -99 kPa` area (`m^2` / %) | `{cw_cav['cav_area_99_m2']:.6f}` ({cw_cav['cav_area_99_pct']:.2f}%) | `{ccw_cav['cav_area_99_m2']:.6f}` ({ccw_cav['cav_area_99_pct']:.2f}%) |",
        f"| `p < -150 kPa` area (`m^2` / %) | `{cw_cav['cav_area_150_m2']:.6f}` ({cw_cav['cav_area_150_pct']:.2f}%) | `{ccw_cav['cav_area_150_m2']:.6f}` ({ccw_cav['cav_area_150_pct']:.2f}%) |",
        f"| `p < -99 kPa` zones (root / mid / tip `m^2`) | `{cw_cav['cav_area_99_root_r_lt_080_m2']:.6f}` / `{cw_cav['cav_area_99_mid_080_120_m2']:.6f}` / `{cw_cav['cav_area_99_tip_r_ge_120_m2']:.6f}` | `{ccw_cav['cav_area_99_root_r_lt_080_m2']:.6f}` / `{ccw_cav['cav_area_99_mid_080_120_m2']:.6f}` / `{ccw_cav['cav_area_99_tip_r_ge_120_m2']:.6f}` |",
        f"| `sigma` / `c_p_min` / `margin` | `{cw['cavitation_metrics']['sigma']:.4f}` / `{cw['cavitation_metrics']['c_p_min']:.4f}` / `{cw['cavitation_metrics']['margin']:.4f}` | `{ccw['cavitation_metrics']['sigma']:.4f}` / `{ccw['cavitation_metrics']['c_p_min']:.4f}` / `{ccw['cavitation_metrics']['margin']:.4f}` |",
        f"| C3 Cavitation Passed (`p_min >= -99 kPa`) | `{cw['cavitation_metrics']['c3_passed']}` | `{ccw['cavitation_metrics']['c3_passed']}` |",
        f"| Nozzle `p_min` / `p_max` (kPa) | `{cw_cav['p_min_nozzle_Pa'] / 1000.0:.3f}` / `{cw_cav['p_max_nozzle_Pa'] / 1000.0:.3f}` | `{ccw_cav['p_min_nozzle_Pa'] / 1000.0:.3f}` / `{ccw_cav['p_max_nozzle_Pa'] / 1000.0:.3f}` |",
        "",
        "## 5. Surface Pressure & Traction CSV Exports",
        "",
        f"- CW impeller CSV: `{cw['surface_csv_summaries']['impeller']['path']}` (`{cw['surface_csv_summaries']['impeller']['node_count']}` nodes)",
        f"- CW nozzle CSV: `{cw['surface_csv_summaries']['nozzle']['path']}` (`{cw['surface_csv_summaries']['nozzle']['node_count']}` nodes)",
        f"- CCW impeller CSV: `{ccw['surface_csv_summaries']['impeller']['path']}` (`{ccw['surface_csv_summaries']['impeller']['node_count']}` nodes)",
        f"- CCW nozzle CSV: `{ccw['surface_csv_summaries']['nozzle']['path']}` (`{ccw['surface_csv_summaries']['nozzle']['node_count']}` nodes)",
    ]
    return "\n".join(lines) + "\n"


def run_live_case(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    cw_model: Path = PINNED_CW_MODEL,
    ccw_model: Path = PINNED_CCW_MODEL,
    cw_expected_sha256: str = PINNED_CW_SHA256,
    ccw_expected_sha256: str = PINNED_CCW_SHA256,
    solver_overrides: dict[str, Any] | None = None,
    cleanup_preflight: bool = True,
) -> dict[str, Any]:
    """Execute serial live CW & CCW COMSOL rebuild + solve + extraction."""
    ensure_bridge_compiled()

    output_dir.mkdir(parents=True, exist_ok=True)
    surface_dir = output_dir / "surface_exports"
    surface_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = output_dir / ".locks"

    cw_pre = compute_file_hash(cw_model)
    ccw_pre = compute_file_hash(ccw_model)
    if cw_pre != cw_expected_sha256.upper():
        raise RuntimeError(f"CW baseline SHA-256 mismatch before run: {cw_pre} != {cw_expected_sha256}")
    if ccw_pre != ccw_expected_sha256.upper():
        raise RuntimeError(f"CCW baseline SHA-256 mismatch before run: {ccw_pre} != {ccw_expected_sha256}")

    overrides = dict(DEFAULT_SOLVER_OVERRIDES)
    if solver_overrides:
        overrides.update(solver_overrides)

    lock_events: list[dict[str, Any]] = []

    # 1. CW (+1400 rpm) — STRICT SERIAL STEP 1
    cw_start_utc = _utc_now()
    t0_cw = time.perf_counter()
    cw_res = eval_propeller(
        cw_model,
        output_dir,
        case_id="cw_1400rpm",
        speed_rpm=1400.0,
        design=R1_CANDIDATE_PARAMS,
        rebuild_geometry=True,
        solve=True,
        mesh_level="fine",
        export_surface_dir=surface_dir,
        solver_overrides=overrides,
        save_model=True,
        allow_overwrite=True,
        expected_source_sha256=cw_expected_sha256,
        lock_dir=lock_dir,
    )
    cw_end_utc = _utc_now()
    cw_elapsed_s = time.perf_counter() - t0_cw
    lock_events.append({
        "step": 1,
        "case_id": "cw_1400rpm",
        "speed_rpm": 1400.0,
        "lock_dir": str(lock_dir.resolve()),
        "lock_file": str((lock_dir / "comsol_solver.lock").resolve()),
        "started_at_utc": cw_start_utc,
        "finished_at_utc": cw_end_utc,
        "elapsed_wall_s": cw_elapsed_s,
        "source_sha256_verified": cw_pre,
        "output_model": cw_res.get("output_model"),
        "output_sha256": cw_res.get("output_sha256"),
    })

    # 2. CCW (-1400 rpm) — STRICT SERIAL STEP 2 (starts strictly after CW finishes)
    ccw_start_utc = _utc_now()
    t0_ccw = time.perf_counter()
    ccw_res = eval_propeller(
        ccw_model,
        output_dir,
        case_id="ccw_1400rpm",
        speed_rpm=-1400.0,
        design=R1_CANDIDATE_PARAMS,
        rebuild_geometry=True,
        solve=True,
        mesh_level="fine",
        export_surface_dir=surface_dir,
        solver_overrides=overrides,
        save_model=True,
        allow_overwrite=True,
        expected_source_sha256=ccw_expected_sha256,
        lock_dir=lock_dir,
    )
    ccw_end_utc = _utc_now()
    ccw_elapsed_s = time.perf_counter() - t0_ccw
    lock_events.append({
        "step": 2,
        "case_id": "ccw_1400rpm",
        "speed_rpm": -1400.0,
        "lock_dir": str(lock_dir.resolve()),
        "lock_file": str((lock_dir / "comsol_solver.lock").resolve()),
        "started_at_utc": ccw_start_utc,
        "finished_at_utc": ccw_end_utc,
        "elapsed_wall_s": ccw_elapsed_s,
        "source_sha256_verified": ccw_pre,
        "output_model": ccw_res.get("output_model"),
        "output_sha256": ccw_res.get("output_sha256"),
    })

    cw_post = compute_file_hash(cw_model)
    ccw_post = compute_file_hash(ccw_model)
    if cw_post != cw_pre or ccw_post != ccw_pre:
        raise RuntimeError(
            f"Baseline SHA-256 changed during execution! "
            f"CW: {cw_pre} -> {cw_post}; CCW: {ccw_pre} -> {ccw_post}"
        )

    if cleanup_preflight:
        preflight_dir = output_dir / "preflight"
        if preflight_dir.is_dir():
            shutil.rmtree(preflight_dir)

    cw_audit = _audit_direction("cw", cw_res, 1400.0)
    ccw_audit = _audit_direction("ccw", ccw_res, -1400.0)
    sym_ratio = compute_thrust_symmetry(
        cw_audit["bollard_kpis"]["thrust_n"],
        ccw_audit["bollard_kpis"]["thrust_n"],
    )
    bi_cav = aggregate_bidirectional_cavitation(
        p_min_ccw_kpa=ccw_audit["cavitation_metrics"]["p_min_gauge_kpa"],
        p_min_cw_kpa=cw_audit["cavitation_metrics"]["p_min_gauge_kpa"],
        cav_area_ccw_pct=float(ccw_res["cavitation"]["cav_area_99_pct"]),
        cav_area_cw_pct=float(cw_res["cavitation"]["cav_area_99_pct"]),
    )

    lock_log = {
        "max_workers": 1,
        "execution_policy": "strict_serial_under_comsol_solver_lock",
        "non_overlapping": lock_events[0]["finished_at_utc"] <= lock_events[1]["started_at_utc"],
        "events": lock_events,
    }
    (output_dir / "execution_lock_log.json").write_text(
        json.dumps(lock_log, indent=2),
        encoding="utf-8",
    )

    summary: dict[str, Any] = {
        "generated_at_utc": _utc_now(),
        "provenance": PROVENANCE_NOTE,
        "design_parameters": R1_CANDIDATE_PARAMS,
        "solver_overrides": overrides,
        "baseline_verification": {
            "cw": {
                "path": str(cw_model),
                "expected_sha256": cw_expected_sha256,
                "pre_sha256": cw_pre,
                "post_sha256": cw_post,
                "unchanged": cw_pre == cw_post == cw_expected_sha256,
            },
            "ccw": {
                "path": str(ccw_model),
                "expected_sha256": ccw_expected_sha256,
                "pre_sha256": ccw_pre,
                "post_sha256": ccw_post,
                "unchanged": ccw_pre == ccw_post == ccw_expected_sha256,
            },
        },
        "cw": cw_audit,
        "ccw": ccw_audit,
        "bidirectional": {
            "thrust_symmetry_ratio": sym_ratio,
            "cavitation": bi_cav,
            "delta_vs_v10f_baseline": {
                "cw_thrust_delta_N": cw_audit["bollard_kpis"]["thrust_n"] - BASELINE_REFERENCE["cw_thrust_N"],
                "ccw_thrust_delta_N": ccw_audit["bollard_kpis"]["thrust_n"] - BASELINE_REFERENCE["ccw_thrust_N"],
                "symmetry_delta": sym_ratio - BASELINE_REFERENCE["symmetry_ratio"],
            },
        },
        "execution_lock_log": lock_log,
    }

    (output_dir / "live_verification_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    (output_dir / "live_verification_report.md").write_text(
        _render_markdown_report(summary),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run serial live CW/CCW COMSOL verification")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cw-model", type=Path, default=PINNED_CW_MODEL)
    parser.add_argument("--ccw-model", type=Path, default=PINNED_CCW_MODEL)
    parser.add_argument("--keep-preflight", action="store_true")
    args = parser.parse_args()

    summary = run_live_case(
        output_dir=args.output_dir,
        cw_model=args.cw_model,
        ccw_model=args.ccw_model,
        cleanup_preflight=not args.keep_preflight,
    )
    cw_t = summary["cw"]["bollard_kpis"]["thrust_n"]
    ccw_t = summary["ccw"]["bollard_kpis"]["thrust_n"]
    sym = summary["bidirectional"]["thrust_symmetry_ratio"]
    print(f"LIVE COMSOL VERIFICATION COMPLETE: CW={cw_t:.2f} N, CCW={ccw_t:.2f} N, sym={sym:.4f}")


if __name__ == "__main__":
    main()
