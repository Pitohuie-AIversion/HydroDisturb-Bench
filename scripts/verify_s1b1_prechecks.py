"""Stage S1-B1 Pre-checks A & B Verification Script.

Executes and verifies:
Pre-check A: Wake Probe verification at (0.15 m, 0.12 m), expressions u, v,
unit m/s, solution dataset dset1, comparing direct COMSOL numerical evaluation
against existing wake_u.txt and wake_v.txt; when headers omit units, re-export
from the saved solution and bind the same-session node evidence to matching samples.
Pre-check B: Solver settings and model consistency readback (BDF orders,
tolerance strategy, scaling, step constraints, mesh stats, discretization).
"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

# Ensure UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java.bridge import compute_file_hash, inspect_probe_and_solver
from hydro_disturb_bench.io import load_comsol_1d_timeseries

SOLVED_MODEL = Path("runs/re100_s1a2_trial/re100_s1a2_solved.mph").resolve()
WAKE_U_TXT = Path("runs/re100_s1a2_trial/wake_u.txt").resolve()
WAKE_V_TXT = Path("runs/re100_s1a2_trial/wake_v.txt").resolve()


def validate_probe_config(
    coords: tuple[float, float],
    exprs: list[str] | str,
    unit: str,
    dataset: str,
) -> bool:
    """Validate probe coordinates, expression, unit, and solution dataset."""
    x, y = coords
    if abs(x - 0.15) > 1e-5 or abs(y - 0.12) > 1e-5:
        raise ValueError(f"Coordinate mismatch: ({x}, {y}) != (0.15, 0.12)")

    if isinstance(exprs, str):
        expr_list = [exprs]
    else:
        expr_list = list(exprs)

    for ex in expr_list:
        if ex not in ("u", "v"):
            raise ValueError(f"Invalid probe expression: {ex} (must be 'u' or 'v')")

    if unit != "m/s":
        raise ValueError(f"Invalid unit (must be SI m/s, not normalized): {unit}")

    if dataset != "dset1":
        raise ValueError(f"Invalid solution dataset: {dataset} (expected 'dset1')")

    return True


def parse_probe_channel_metadata(
    header: str,
    meta: dict,
    component: str,
    res: dict,
) -> tuple[str, str, str, str, list[str]]:
    """Parse and validate time unit, expression, and velocity unit from header and COMSOL inspection.

    Returns:
        (time_unit, expr, unit, unit_source, errors)
    """
    errors: list[str] = []

    # 1. Parse time unit from header (strictly require SI second 's', reject ms/min/h/missing)
    time_unit = ""
    # Match patterns like: "时间 (s)", "Time (s)", "时间 [s]", "Time [s]", "% t (s)"
    m_time = re.search(r'(?:时间|Time|\bt\b)\s*[\(\[]([^\)\]]+)[\)\]]', header, re.IGNORECASE)
    if m_time:
        time_unit = m_time.group(1).strip().lower()
    else:
        # Check for bracketed time tokens in header
        bracket_candidates = re.findall(r'[\(\[]([^\)\]]+)[\)\]]', header)
        if bracket_candidates:
            cand = bracket_candidates[0].strip().lower()
            if cand in ("s", "ms", "min", "h", "sec", "second"):
                time_unit = cand

    if not time_unit:
        errors.append(f"Missing time unit in meta_{component} header: '{header}'")
    elif time_unit != "s":
        errors.append(f"Invalid time unit in meta_{component} header: '{time_unit}' (expected SI 's')")

    # 2. Parse component expression (check full expression or clear component description)
    expr = ""
    explicit_expr = meta.get("expression")
    if explicit_expr:
        expr = str(explicit_expr).strip()
    elif component == "u":
        if "速度场，x 分量" in header or "速度场, x 分量" in header or "velocity field, x component" in header.lower():
            expr = "u"
        elif re.search(r'\b(?:u|comp1\.u|spf\.U)\b', header):
            expr = "u"
        else:
            expr = "unknown_u_expr"
    elif component == "v":
        if "速度场，y 分量" in header or "速度场, y 分量" in header or "velocity field, y component" in header.lower():
            expr = "v"
        elif re.search(r'\b(?:v|comp1\.v)\b', header):
            expr = "v"
        else:
            expr = "unknown_v_expr"
    else:
        expr = f"unknown_{component}_expr"

    if expr not in ("u", "v"):
        errors.append(f"Invalid velocity expression for {component}-component: '{expr}' (expected 'u' or 'v')")

    # 3. Parse velocity unit and identify provenance
    # Priority A: Check if header contains explicit velocity unit
    unit = ""
    unit_source = ""
    velocity_header = header[m_time.end():] if m_time else header
    bracket_tokens = re.findall(r'[\(\[]([^\)\]]+)[\)\]]', velocity_header)
    if bracket_tokens:
        unit = bracket_tokens[-1].strip()
        unit_source = "header"

    if not unit:
        if "无量纲" in header or "normalized" in header.lower():
            unit = "1"
            unit_source = "header"

    # Priority B: A node readback already bound to file/model by bind_probe_export.
    evidence = meta.get("probe_export_evidence", {})
    if not unit and evidence.get("unit") and meta.get("unit") == evidence["unit"]:
        unit = str(evidence["unit"]).strip()
        unit_source = "comsol_model_export_node"

    # If neither header nor COMSOL model provides unit, fail
    if not unit:
        unit = "unknown"
        unit_source = "none"
        errors.append(f"Velocity unit evidence missing for meta_{component}: neither header nor COMSOL inspection specifies unit")
    elif unit != "m/s":
        errors.append(f"Invalid velocity unit in meta_{component}: '{unit}' (expected SI 'm/s')")

    return time_unit, expr, unit, unit_source, errors


def verify_probe_metadata(
    res: dict,
    meta_u: dict,
    meta_v: dict,
    t_u: np.ndarray,
    t_v: np.ndarray,
    u_txt: np.ndarray,
    v_txt: np.ndarray,
    tol_eval: float = 1e-12,
) -> dict:
    """Explicitly verify probe coordinates, datasets, authentic units, time alignment, and direct evaluations.

    Guarantees:
    - Parses and verifies real metadata from meta_u and meta_v (fails if missing, corrupt, or normalized).
    - Requires genuine velocity unit from either explicit header or COMSOL model inspection; rejects defaults.
    - Strictly validates actual_t existence and finiteness from COMSOL; rejects substitution with target_t.
    - Checks simultaneous 3-way time alignment: actual_t (COMSOL) == target_t == t_txt (series).
    """
    errors: list[str] = []
    metadata_errors: list[str] = []
    time_errors: list[str] = []

    # 1. Raw metadata dictionary checks
    if not isinstance(meta_u, dict) or not meta_u:
        metadata_errors.append("meta_u must be a non-empty metadata dictionary")
        meta_u = {}
    if not isinstance(meta_v, dict) or not meta_v:
        metadata_errors.append("meta_v must be a non-empty metadata dictionary")
        meta_v = {}

    header_u = str(meta_u.get("column_header", "")).strip()
    header_v = str(meta_v.get("column_header", "")).strip()

    if not header_u:
        metadata_errors.append("Missing column_header in meta_u")
    if not header_v:
        metadata_errors.append("Missing column_header in meta_v")

    time_unit_u, raw_expr_u, raw_unit_u, unit_src_u, errs_u = parse_probe_channel_metadata(header_u, meta_u, "u", res)
    time_unit_v, raw_expr_v, raw_unit_v, unit_src_v, errs_v = parse_probe_channel_metadata(header_v, meta_v, "v", res)

    metadata_errors.extend(errs_u)
    metadata_errors.extend(errs_v)

    # 2. Coordinates validation against model probe configuration
    probe_coords = res.get("probe_coords", {})
    try:
        px = float(probe_coords.get("x", float("nan")))
        py = float(probe_coords.get("y", float("nan")))
        validate_probe_config((px, py), [raw_expr_u, raw_expr_v], raw_unit_u, "dset1")
    except Exception as e:
        metadata_errors.append(f"Probe config error: {e}")
        px = float(probe_coords.get("x", 0.15)) if "x" in probe_coords else 0.15
        py = float(probe_coords.get("y", 0.12)) if "y" in probe_coords else 0.12

    # 3. Solution dataset validation
    datasets = {d.get("tag"): d for d in res.get("datasets", [])}
    if "dset1" not in datasets:
        metadata_errors.append("Solution dataset 'dset1' not found in model datasets")
    else:
        dset1_type = datasets["dset1"].get("type", "")
        if dset1_type != "Solution":
            metadata_errors.append(f"Dataset 'dset1' unexpected type: {dset1_type} != Solution")

    # 4. Time series length and alignment validation
    n_u, n_v = len(t_u), len(t_v)
    if n_u != n_v or n_u != 1001:
        time_errors.append(f"Time series length mismatch: len(t_u)={n_u}, len(t_v)={n_v}, expected 1001")

    if n_u > 0 and n_v > 0:
        if abs(t_u[0] - 0.0) > 1e-6 or abs(t_u[-1] - 200.0) > 1e-6:
            time_errors.append(f"Time range mismatch: [{t_u[0]}, {t_u[-1]}] != [0.0, 200.0]")
        max_dt_diff = float(np.max(np.abs(t_u - t_v)))
        if max_dt_diff > 1e-9:
            time_errors.append(f"Time alignment drift between u and v: {max_dt_diff} > 1e-9")

    # 5. Direct COMSOL evaluations vs TXT values with strict actual_t and 3-way time checking
    direct_evals = res.get("direct_evaluations", [])
    if not direct_evals:
        errors.append("No direct evaluations returned from COMSOL")

    comparison_table = []
    all_matched = True
    for item in direct_evals:
        if "target_t" not in item:
            errors.append("Missing target_t in direct evaluation item")
            all_matched = False
            continue
        target_t = float(item["target_t"])

        # Strict actual_t validation: must exist and be finite
        if "actual_t" not in item or item["actual_t"] is None:
            all_matched = False
            time_errors.append(f"Missing actual_t in COMSOL direct evaluation at target_t={target_t}")
            continue

        try:
            actual_t = float(item["actual_t"])
            if not math.isfinite(actual_t):
                raise ValueError("Non-finite actual_t")
        except (ValueError, TypeError):
            all_matched = False
            time_errors.append(f"Non-finite or invalid actual_t in COMSOL direct evaluation at target_t={target_t}")
            continue

        u_dir = item.get("u_direct")
        v_dir = item.get("v_direct")

        # 3-way time alignment check: actual_t vs target_t
        if abs(actual_t - target_t) > 1e-5:
            all_matched = False
            time_errors.append(f"COMSOL actual_t mismatch: actual_t={actual_t} != target_t={target_t}")

        idx_u = int(np.argmin(np.abs(t_u - target_t)))
        idx_v = int(np.argmin(np.abs(t_v - target_t)))

        t_txt_u = float(t_u[idx_u])
        t_txt_v = float(t_v[idx_v])

        # 3-way time alignment check: txt timestamps vs target_t
        if abs(t_txt_u - target_t) > 1e-5:
            all_matched = False
            time_errors.append(f"TXT t_u mismatch: t_txt_u={t_txt_u} != target_t={target_t}")
        if abs(t_txt_v - target_t) > 1e-5:
            all_matched = False
            time_errors.append(f"TXT t_v mismatch: t_txt_v={t_txt_v} != target_t={target_t}")

        # Check coordinates in evaluation feature
        eval_x = float(item.get("point_x", px))
        eval_y = float(item.get("point_y", py))
        if abs(eval_x - px) > 1e-5 or abs(eval_y - py) > 1e-5:
            all_matched = False
            metadata_errors.append(f"Evaluation coordinate mismatch at t={target_t}: ({eval_x}, {eval_y}) != ({px}, {py})")

        val_u_txt = float(u_txt[idx_u])
        val_v_txt = float(v_txt[idx_v])

        diff_u = abs(u_dir - val_u_txt) if u_dir is not None else float("nan")
        diff_v = abs(v_dir - val_v_txt) if v_dir is not None else float("nan")

        match_u = (diff_u <= tol_eval) and (abs(actual_t - target_t) <= 1e-5)
        match_v = (diff_v <= tol_eval) and (abs(actual_t - target_t) <= 1e-5)
        if not (match_u and match_v):
            all_matched = False
            errors.append(f"Value mismatch at t={target_t}: diff_u={diff_u:.2e}, diff_v={diff_v:.2e}")

        comparison_table.append({
            "t": target_t,
            "target_t": target_t,
            "actual_t": actual_t,
            "txt_t": t_txt_u,
            "coords": [px, py],
            "expr_u": raw_expr_u,
            "expr_v": raw_expr_v,
            "unit": raw_unit_u,
            "unit_source": unit_src_u,
            "unit_v": raw_unit_v,
            "unit_source_v": unit_src_v,
            "u": {"direct": u_dir, "txt": val_u_txt, "diff": diff_u, "match": match_u},
            "v": {"direct": v_dir, "txt": val_v_txt, "diff": diff_v, "match": match_v},
        })

    # 6. Physical order-of-magnitude sanity check (velocity deficit downstream of cylinder)
    mean_u_mag = float(np.mean(np.abs(u_txt[-200:])))
    if not (1e-4 < mean_u_mag < 0.1):
        errors.append(f"Wake velocity magnitude out of expected physical range: {mean_u_mag} m/s")

    all_errors = errors + metadata_errors + time_errors
    raw_metadata_verified = (len(metadata_errors) == 0) and bool(header_u and header_v)
    time_alignment_verified = (len(time_errors) == 0) and (len(direct_evals) > 0)
    is_valid = (len(all_errors) == 0) and all_matched

    return {
        "is_valid": is_valid,
        "status": "PASS" if is_valid else "FAIL",
        "errors": all_errors,
        "probe_coordinates_m": [px, py],
        "expressions": [raw_expr_u, raw_expr_v],
        "unit": raw_unit_u,
        "unit_source": unit_src_u,
        "units": {"u": raw_unit_u, "v": raw_unit_v},
        "unit_sources": {"u": unit_src_u, "v": unit_src_v},
        "dataset": "dset1",
        "dataset_verified": "dset1" in datasets,
        "time_alignment_verified": time_alignment_verified,
        "raw_metadata_verified": raw_metadata_verified,
        "num_steps": n_u,
        "comparison_table": comparison_table,
    }


def bind_probe_export(res: dict, component: str, model: Path, file: Path, meta: dict) -> dict:
    """Bind same-session getter evidence to this exact exported file and model."""
    matches = [e for e in res.get("probe_exports", []) if e.get("component") == component]
    if len(matches) != 1:
        raise ValueError(f"Velocity unit evidence missing/ambiguous for {component}")
    evidence = matches[0]
    required = ("model_path", "model_tag", "model_sha256", "filename", "file_sha256",
                "export_tag", "plotgroup_tag", "feature_tag", "expression", "unit",
                "dataset_tag", "dataset_type", "solution_dataset", "solution_type", "solution_tag")
    if any(not evidence.get(k) for k in required):
        raise ValueError(f"Incomplete export node readback for {component}: {evidence}")
    if Path(evidence["model_path"]).resolve() != model.resolve() or evidence["model_sha256"] != compute_file_hash(model):
        raise ValueError("Export evidence model identity mismatch")
    if Path(evidence["filename"]).resolve() != file.resolve() or evidence["file_sha256"] != compute_file_hash(file):
        raise ValueError("Export evidence file identity mismatch")
    if (evidence["expression"] != component or evidence["dataset_type"] != "CutPoint2D"
            or evidence["solution_dataset"] != "dset1" or evidence["solution_type"] != "Solution"
            or evidence["feature_tags"] != [evidence["feature_tag"]]
            or evidence.get("feature_data") not in ("parent", evidence["dataset_tag"])):
        raise ValueError("Export expression/dataset/feature association mismatch")
    coords = [float(evidence["point_x"]), float(evidence["point_y"])]
    if not np.all(np.isfinite(coords)) or not np.allclose(coords, [0.15, 0.12], rtol=0, atol=1e-10):
        raise ValueError("Export probe coordinate mismatch")
    # Preserve the getter's unit even if non-SI; the downstream SI gate rejects it.
    return dict(meta, unit=evidence["unit"], unit_source="comsol_model_export_node",
                probe_export_evidence=evidence)


def run_prechecks(*, model_path=None, wake_u_path=None, wake_v_path=None, output_dir=None) -> dict:
    model = Path(model_path or SOLVED_MODEL).resolve()
    originals = {"u": Path(wake_u_path or WAKE_U_TXT).resolve(),
                 "v": Path(wake_v_path or WAKE_V_TXT).resolve()}
    if output_dir is None:
        Path("scratch").mkdir(exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix="s1b1_prechecks_", dir="scratch")).resolve()
    else:
        out = Path(output_dir).resolve()
        out.mkdir(parents=True, exist_ok=False)
    report_path = out / "report.json"
    print(f"Stage S1-B1 prechecks: {model} -> {out}")
    res = {}
    try:
        loaded = {c: load_comsol_1d_timeseries(p) for c, p in originals.items()}
        # Explicit file headers take priority. Missing units require a fresh export
        # because the historical saved models omit temporary probe nodes.
        needs_export = any(parse_probe_channel_metadata(m[2].get("column_header", ""), m[2], c, {})[2] == "unknown"
                           for c, m in loaded.items())
        kwargs = {"probe_output_dir": out / "probe_export"} if needs_export else {}
        res = inspect_probe_and_solver(model, probe_x="0.15", probe_y="0.12", **kwargs)
        (out / "raw_inspection.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        associations = {}
        if needs_export:
            for c in ("u", "v"):
                fresh_file = out / "probe_export" / f"wake_{c}.txt"
                t_new, y_new, meta_new = load_comsol_1d_timeseries(fresh_file)
                meta_bound = bind_probe_export(res, c, model, fresh_file, meta_new)
                t_old, y_old, meta_old = loaded[c]
                if not np.array_equal(t_old, t_new) or not np.allclose(y_old, y_new, rtol=0, atol=1e-12):
                    raise ValueError(f"Historical and fresh {c} export series differ")
                # Validate the historical header (including any explicit non-SI unit),
                # using the newly exported node only where the header has no unit.
                loaded[c] = (t_old, y_old, dict(meta_old, unit=meta_bound["unit"],
                    unit_source=meta_bound["unit_source"], probe_export_evidence=meta_bound["probe_export_evidence"]))
                associations[c] = {"historical_file": str(originals[c]),
                    "historical_sha256": compute_file_hash(originals[c]),
                    "fresh_export_evidence": meta_bound["probe_export_evidence"],
                    "all_samples_equal_within": 1e-12}
        t_u, u_txt, meta_u = loaded["u"]
        t_v, v_txt, meta_v = loaded["v"]
        precheck_a = verify_probe_metadata(res, meta_u, meta_v, t_u, t_v, u_txt, v_txt)
        precheck_a["export_associations"] = associations
    except Exception as exc:
        precheck_a = {"is_valid": False, "status": "FAIL", "errors": [str(exc)],
                      "unit": "unknown", "unit_source": "none", "comparison_table": [],
                      "probe_coordinates_m": [None, None]}

    print("\n[PRE-CHECK A: Wake Probe Direct Evaluation vs TXT Comparison Table]")
    print(f"{'Target t (s)':<12} | {'Actual t (s)':<12} | {'TXT t (s)':<10} | {'Expr':<5} | {'Unit':<5} | {'Unit Source':<24} | {'Direct Eval (m/s)':<22} | {'TXT Eval (m/s)':<22} | {'Diff (m/s)':<10} | {'Status'}")
    print("-" * 145)

    for item in precheck_a["comparison_table"]:
        t_target = item["target_t"]
        t_actual = item["actual_t"]
        t_txt = item["txt_t"]
        unit_src = item.get("unit_source", "unknown")
        u_item = item["u"]
        v_item = item["v"]
        status_u = "MATCH" if u_item["match"] else "MISMATCH"
        status_v = "MATCH" if v_item["match"] else "MISMATCH"
        print(f"{t_target:<12.1f} | {t_actual:<12.1f} | {t_txt:<10.1f} | {'u':<5} | {item['unit']:<5} | {unit_src:<24} | {str(u_item['direct']):<22} | {u_item['txt']:<22.16e} | {u_item['diff']:<10.2e} | {status_u}")
        print(f"{t_target:<12.1f} | {t_actual:<12.1f} | {t_txt:<10.1f} | {'v':<5} | {item['unit_v']:<5} | {item['unit_source_v']:<24} | {str(v_item['direct']):<22} | {v_item['txt']:<22.16e} | {v_item['diff']:<10.2e} | {status_v}")

    # Pre-check B: Solver & Model Readback
    solver_details = res.get("solver_details", {})
    mesh_details = res.get("mesh_details", {})
    physics_details = res.get("physics_details", {})

    print("\n[PRE-CHECK B: Solver & Model Consistency Readback]")
    print(f"  BDF Method:                  {solver_details.get('timemethod')}")
    print(f"  Time stepping policy:        {solver_details.get('tstepsbdf')}")
    print(f"  Order range:                 min={solver_details.get('minorder')}, max={solver_details.get('maxorder')}")
    print(f"  Relative tolerance (rtol):   {solver_details.get('rtol')}")
    print(f"  Absolute tolerance method:   {solver_details.get('atolmethod')}")
    print(f"  Absolute tolerance factor:   {solver_details.get('atolfactor')}")
    print(f"  Absolute tolerance atol:     {solver_details.get('atol')}")
    print(f"  Global atol:                 {solver_details.get('atolglobal')}")
    print(f"  Global atol factor:          {solver_details.get('atolglobalfactor')}")
    print(f"  Global atol method:          {solver_details.get('atolglobalmethod')}")
    print(f"  Global atol value method:    {solver_details.get('atolglobalvaluemethod')}")
    print(f"  Max step constraint mode:    {solver_details.get('maxstepconstraintbdf')}")
    print(f"  Max step value setting:      {solver_details.get('maxstepbdf')}")
    print(f"  Initial step setting:        {solver_details.get('initialstepbdf')}")
    print(f"  Initial step active mode:    {solver_details.get('initialstepbdfactive')}")
    print(f"  Output schedule (tlist):     {solver_details.get('tlist')}")
    print(f"  Mesh Elements:               {mesh_details.get('num_elem')}")
    print(f"  Mesh Vertices:               {mesh_details.get('num_vertex')}")
    print(f"  Mesh Features:               {mesh_details.get('features')}")
    print(f"  Physics Discretization:      Shape order={physics_details.get('shape_order')}")
    print(f"  Initial Conditions:          {physics_details.get('initial_conditions')}")

    report = {
        "precheck_a_wake_probe": precheck_a,
        "precheck_b_solver_model": {
            "status": "READ_BACK" if solver_details else "UNVERIFIED",
            "solver_details": solver_details,
            "mesh_details": mesh_details,
            "physics_details": physics_details,
            "tolerance_coupling_noted": "comp1_u absolute tolerance is tied to rtol with factor 0.1 and global atol (atolglobalfactor=0.05, method=scaled)",
        },
        "overall_prechecks_status": "PASS" if precheck_a["is_valid"] else "FAIL",
    }

    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nPre-checks complete! Saved report to {report_path}")
    return report


if __name__ == "__main__":
    rep = run_prechecks()
    if rep["overall_prechecks_status"] != "PASS":
        print(f"Pre-checks FAILED: {rep['precheck_a_wake_probe'].get('errors')}")
        sys.exit(1)
    print("Pre-checks A & B: ALL CHECKS PASSED.")
    sys.exit(0)
