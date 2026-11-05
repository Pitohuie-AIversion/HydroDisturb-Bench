"""Compare accepted Case C (M0) with native COMSOL mesh refinements (M1 at h0/1.4, M2 at h0/1.96).

M0 and M1 are read only when running M2. Each refinement is built directly from M0
in an isolated copy without double-applying the refinement factor onto M1.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
    export_s1_diagnostics,
    inspect_model,
    inspect_native_mesh,
    refine_native_mesh,
    run_study,
)
from hydro_disturb_bench.io import load_comsol_1d_timeseries
from scripts.verify_and_compare_s1b1 import analyze_case, compare_cases, compute_demeaned_rms
from scripts.verify_s1b1_prechecks import run_prechecks

M0_DIR = ROOT / "runs/re100_s1b1_case_c_20261104_verified"
M0_MODEL = M0_DIR / "re100_s1b1_case_c_solved.mph"
M1_DIR = ROOT / "runs/re100_s1b2_m1_20261105_verified"
M1_MODEL = M1_DIR / "re100_s1b2_m1_solved.mph"
DEFAULT_PLAN = ROOT / "configs/re100_s1b2_m1.json"
M2_PLAN = ROOT / "configs/re100_s1b2_m2.json"
SUPPORTED_MESH_LEVELS = {"M1": 1.4, "M2": 1.96}
PROTECTED_PATHS = (
    ROOT / "case/cylinder_flow.mph",
    ROOT / "models/cylinder_2d/re100_benchmark.mph",
    ROOT / "runs/re100_s0_verification",
    ROOT / "runs/re100_s1a_trial",
    ROOT / "runs/re100_s1a2_trial",
    ROOT / "runs/re100_s1b1_trial",
    ROOT / "runs/re100_s1b1_dt005_tol1e4",
    M0_DIR,
    ROOT / "runs/re100_s1b1_case_d_20261104_verified",
    M1_DIR,
    ROOT / "docs/UNIT_SOURCE_CASE_C_VERIFIED_20261104.md",
    ROOT / "docs/STAGE_S1B1_CASE_D_20261104.md",
    ROOT / "docs/STAGE_S1B2_M1_20261105.md",
    ROOT / "docs/evidence/unit_source_verified_20261104",
    ROOT / "docs/evidence/case_d_20261104",
    ROOT / "docs/evidence/s1b2_m1_20261105",
)


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def snapshot_protected_files() -> dict:
    files = set()
    for path in PROTECTED_PATHS:
        if not path.exists():
            raise FileNotFoundError(f"Missing protected historical path: {path}")
        if path.is_dir():
            files.update(item for item in path.rglob("*") if item.is_file())
        else:
            files.add(path)
    return {str(path): compute_file_hash(path) for path in sorted(files)}


def check_step_log(log_path: Path) -> dict:
    """Require a complete monotonic accepted-step sequence under the retained 0.05 s cap."""
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    pattern = re.compile(rf"^\s*(\d+)\s+({number})\s+({number})(?:\s|$)")
    rows = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match and int(match[1]) > 0:
            rows.append((int(match[1]), float(match[2]), float(match[3])))
    if not rows or rows[-1][1] < 200.0 - 1e-8:
        raise RuntimeError("Missing complete internal-step log through t=200 s")
    if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("Internal-step IDs must be contiguous and unique from 1")
    if any(not math.isfinite(t) or not math.isfinite(dt) for _, t, dt in rows):
        raise RuntimeError("Non-finite internal-step log value")
    if any(a[1] >= b[1] for a, b in zip(rows, rows[1:])):
        raise RuntimeError("Internal-step times must be strictly increasing")
    if any(dt <= 0 or dt > 0.0500001 for _, _, dt in rows):
        raise RuntimeError("Actual logged internal step violates 0 < dt <= 0.05 s")
    late = [dt for _, at_time, dt in rows if at_time >= 160.0]
    return {
        "source": str(log_path), "logged_rows": len(rows), "last_step_number": rows[-1][0],
        "last_logged_time": rows[-1][1],
        "all_dt_range": [min(row[2] for row in rows), max(row[2] for row in rows)],
        "late_dt_range": [min(late), max(late)], "late_dt_mean": sum(late) / len(late),
        "sequential_unique_step_ids": True, "strictly_increasing_time": True, "cap_verified": True,
    }


def check_time_policy(solver: dict) -> None:
    """Require the selected C time policy; no time setting is a mesh-study variable."""
    try:
        valid = (
            float(solver["rtol"]) == 1e-4
            and solver["maxstepconstraintbdf"] == "const"
            and float(solver["maxstepbdf"]) == 0.05
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Missing or invalid actual time-policy readback") from exc
    if not valid:
        raise RuntimeError("Mesh study requires unchanged rtol=1e-4, const maximum step 0.05 s")


def check_invariants(before: dict, after: dict, tree_m0: dict, tree_m1: dict) -> dict:
    """Compare available nonmesh model and solver fields without guessing missing values."""
    check_time_policy(before["solver_details"])
    check_time_policy(after["solver_details"])
    changed = []
    for key in ("solver_details", "physics_details"):
        if before[key] != after[key]:
            changed.append(key)
    for key in ("parameters", "studies"):
        if tree_m0[key] != tree_m1[key]:
            changed.append(key)
    components = []
    for tree in (tree_m0, tree_m1):
        items = deepcopy(tree["components"])
        for component in items:
            component.pop("meshes", None)
        components.append(items)
    if components[0] != components[1]:
        changed.append("nonmesh component tree")
    for key in ("sdim", "is_complete", "features"):
        if before["mesh_details"][key] != after["mesh_details"][key]:
            changed.append(f"mesh_details.{key}")
    if changed:
        raise RuntimeError(f"Non-target setting mismatch: {changed}")
    return {
        "status": "PASS",
        "m0_solver": before["solver_details"],
        "m1_solver": after["solver_details"],
        "equal_readbacks": ["solver_details", "physics_details", "parameters", "studies", "nonmesh components"],
        "readback_limits": "General model inspection does not enumerate every BC/material property; unavailable physics fields remain UNOBTAINED. Native invariant evidence is checked separately.",
    }


def _native_mesh(native: dict, component_tag: str, mesh_tag: str) -> dict:
    components = [item for item in native["components"] if item["tag"] == component_tag]
    meshes = [item for item in components[0]["meshes"] if item["tag"] == mesh_tag] if len(components) == 1 else []
    if len(meshes) != 1:
        raise RuntimeError("Explicit native component/mesh target is missing or ambiguous")
    return meshes[0]


def _mesh_feature(mesh: dict, path: list[str]) -> dict:
    children = mesh["features"]
    if not path:
        raise RuntimeError("Refinement feature path must be nonempty")
    for tag in path:
        matching = [item for item in children if item["tag"] == tag]
        if len(matching) != 1:
            raise RuntimeError(f"Native mesh feature path not found: {path}")
        node = matching[0]
        children = node.get("features", [])
    return node


def _native_equal(a: object, b: object) -> bool:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0)
    return a == b


def _plan_level_and_factor(plan: dict) -> tuple[str, float]:
    """Resolve and validate the mesh refinement level (M1 at h0/1.4 or M2 at h0/1.96)."""
    source_case = plan.get("source_case")
    if source_case is not None and source_case != "Case C / M0":
        raise RuntimeError("Refinement plan must use Case C / M0 as source_case; never refine cumulatively from M1")
    factor = plan.get("refinement_factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor):
        raise RuntimeError("Refinement plan requires a finite numeric refinement_factor")
    level = plan.get("mesh_level")
    if level is None:
        for candidate_level, candidate_factor in SUPPORTED_MESH_LEVELS.items():
            if _native_equal(float(factor), candidate_factor):
                return candidate_level, candidate_factor
        raise RuntimeError("Supported mesh refinement factors are 1.4 (M1) and 1.96 (M2) relative to M0")
    if level not in SUPPORTED_MESH_LEVELS or not _native_equal(float(factor), SUPPORTED_MESH_LEVELS[level]):
        raise RuntimeError(f"Unsupported or mismatched mesh_level={level!r} and refinement_factor={factor!r}")
    return level, SUPPORTED_MESH_LEVELS[level]


def validate_refinement_plan(before: dict, plan: dict) -> dict:
    """Bind the explicit Size-only h0/factor plan to the measured M0 source model and controls."""
    if not plan.get("source_sha256") or plan["source_sha256"].lower() != before["sha256"].lower():
        raise RuntimeError("Refinement plan source SHA-256 does not match the inspected M0 model")
    level, factor = _plan_level_and_factor(plan)
    if not plan.get("changes"):
        raise RuntimeError(f"{level} requires an explicit nonempty h/{factor:g} refinement plan")
    mesh = _native_mesh(before, plan["component_tag"], plan["mesh_tag"])
    seen, length_changes = set(), 0
    for change in plan["changes"]:
        identity = (tuple(change["feature_path"]), change["property"])
        if identity in seen:
            raise RuntimeError("Duplicate refinement property change")
        seen.add(identity)
        node = _mesh_feature(mesh, change["feature_path"])
        prop = change["property"]
        if node["type"] not in {"MeshSizeDefault", "Size"} or prop not in {"hmax", "hmin", "custom"}:
            raise RuntimeError("Refinement may change only existing Size hmax/hmin/custom controls")
        if prop not in node["properties"] or not _native_equal(node["properties"][prop], change["before"]):
            raise RuntimeError(f"Refinement plan before-value does not match native readback: {identity}")
        if prop == "custom":
            if change["before"] != "off" or change["after"] != "on":
                raise RuntimeError("The only permitted custom change is off to on")
        else:
            old, new = change["before"], change["after"]
            if isinstance(old, bool) or isinstance(new, bool) or not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
                raise RuntimeError("Native Size lengths must be numeric SI readbacks")
            if not math.isfinite(old) or not math.isfinite(new) or old <= 0 or new <= 0 or not _native_equal(new, old / factor):
                raise RuntimeError(f"Size length change must equal the measured length divided by {factor:g}")
            length_changes += 1
    if not length_changes:
        raise RuntimeError("A refinement plan must change native mesh lengths")
    return mesh


def _validate_native_statistics(mesh: dict) -> None:
    if mesh.get("length_unit") != "m":
        raise RuntimeError("Native mesh coordinate length unit must be explicitly read as m")
    if not mesh["is_complete"] or mesh["sdim"] != 2:
        raise RuntimeError("Mesh must be a complete two-dimensional native mesh")
    n_vertex, n_elem = mesh["num_vertex"], mesh["num_elem"]
    if n_vertex <= 0 or n_elem <= 0:
        raise RuntimeError("Native mesh counts must be positive")
    quality = mesh.get("quality")
    if (not isinstance(quality, dict) or not quality.get("measure")
            or not all(isinstance(quality.get(k), (int, float)) and math.isfinite(quality[k]) for k in ("minimum", "mean"))
            or not 0 < quality["minimum"] <= quality["mean"] <= 1.0 + 1e-8):
        raise RuntimeError("Native mesh quality must be finite, positive and ordered within [0,1]")
    vertices = np.asarray(mesh["vertices"], dtype=float)
    if vertices.shape != (2, n_vertex) or not np.isfinite(vertices).all():
        raise RuntimeError("Native mesh vertices do not match the actual vertex count")
    elements = mesh["elements"]
    if not elements or sum(elements.get(tag, {}).get("count", 0) for tag in ("tri", "quad")) != n_elem:
        raise RuntimeError("Native surface element connectivity does not match the element count")
    for element in elements.values():
        count = element["count"]
        connectivity = np.asarray(element["connectivity"])
        if count == 0:
            continue
        if (connectivity.ndim != 2 or connectivity.shape[1] != count
                or not np.issubdtype(connectivity.dtype, np.integer)
                or np.any(connectivity < 0) or np.any(connectivity >= n_vertex)
                or len(element["entities"]) != count):
            raise RuntimeError("Invalid actual native element connectivity")


def check_mesh_refinement(
    before: dict, after: dict, plan: dict, *, prior_refined: dict | None = None,
) -> dict:
    """Verify the actual rebuilt mesh and all readable controls against the explicit plan."""
    level, _ = _plan_level_and_factor(plan)
    level_key = level.lower()
    a = validate_refinement_plan(before, plan)
    b = _native_mesh(after, plan["component_tag"], plan["mesh_tag"])
    for mesh in (a, b):
        _validate_native_statistics(mesh)
    if b["num_elem"] <= a["num_elem"] or b["num_vertex"] <= a["num_vertex"]:
        raise RuntimeError("Actual native mesh element and vertex counts must increase")
    if a["vertices"] == b["vertices"] or a["elements"] == b["elements"]:
        raise RuntimeError("Native mesh geometry/connectivity did not change")
    if a["quality"]["measure"] != b["quality"]["measure"]:
        raise RuntimeError("Mesh quality measure changed")
    if not before.get("non_mesh") or before["non_mesh"] != after.get("non_mesh"):
        raise RuntimeError("Native non-mesh geometry/physics/material settings changed or are missing")
    prior_mesh = None
    if prior_refined is not None:
        prior_mesh = _native_mesh(prior_refined, plan["component_tag"], plan["mesh_tag"])
        _validate_native_statistics(prior_mesh)
        if b["num_elem"] <= prior_mesh["num_elem"] or b["num_vertex"] <= prior_mesh["num_vertex"]:
            raise RuntimeError(f"Actual {level} native mesh element and vertex counts must exceed M1")
        if prior_mesh["vertices"] == b["vertices"] or prior_mesh["elements"] == b["elements"]:
            raise RuntimeError(f"Native {level} mesh geometry/connectivity did not change relative to M1")
        if prior_mesh["quality"]["measure"] != b["quality"]["measure"]:
            raise RuntimeError("Mesh quality measure changed relative to M1")
        if not prior_refined.get("non_mesh") or before["non_mesh"] != prior_refined.get("non_mesh"):
            raise RuntimeError("M1 native non-mesh geometry/physics/material settings do not match M0")
    expected, actual = deepcopy(before["components"]), deepcopy(after["components"])
    expected_mesh = _native_mesh({"components": expected}, plan["component_tag"], plan["mesh_tag"])
    actual_mesh = _native_mesh({"components": actual}, plan["component_tag"], plan["mesh_tag"])
    for change in plan["changes"]:
        target = _mesh_feature(actual_mesh, change["feature_path"])
        if not _native_equal(target["properties"].get(change["property"]), change["after"]):
            raise RuntimeError("Requested refinement was not reflected in actual native settings")
        _mesh_feature(expected_mesh, change["feature_path"])["properties"][change["property"]] = target["properties"][change["property"]]
    derived = []
    generated = {"buildinfo", "buildoutput", "builddetails", "buildtime", "buildnote"}

    def remove_generated(nodes: list[dict], mesh_label: str) -> None:
        for node in nodes:
            props = node["properties"]
            for key in generated:
                props.pop(key, None)
            if node["type"] == "BndLayerProp" and props.get("inittype") == "blhminfact":
                values = {key: props.pop(key) for key in ("blhmin", "blhtot") if key in props}
                derived.append({"mesh": mesh_label, "path": node["path"], "inactive_defaults": values})
            remove_generated(node.get("features", []), mesh_label)

    for mesh, label in ((expected_mesh, "m0"), (actual_mesh, level_key)):
        for key in ("num_elem", "num_vertex", "quality", "area", "vertices", "elements"):
            mesh.pop(key)
        remove_generated(mesh["features"], label)
    if expected != actual:
        raise RuntimeError("Unplanned native mesh control, selection or feature change")
    result = {
        "status": "PASS", "mesh_level": level, "source_sha256": before["sha256"], "plan": plan,
        "m0": {key: a[key] for key in ("num_elem", "num_vertex", "quality", "area")},
        level_key: {key: b[key] for key in ("num_elem", "num_vertex", "quality", "area")},
        "element_count_ratio": b["num_elem"] / a["num_elem"],
        "equivalent_h_ratio_from_counts": math.sqrt(a["num_elem"] / b["num_elem"]),
        "native_non_mesh_equal": True,
        "ignored_generated_properties": sorted(generated),
        "derived_inactive_defaults": derived,
        "readback_limits": "Only readable native properties are compared; unreadable-property records remain in raw evidence. Inactive blhmin/blhtot defaults in blhminfact mode are not measured first-layer height or total thickness.",
    }
    if prior_mesh is not None and level != "M1":
        result["m1"] = {key: prior_mesh[key] for key in ("num_elem", "num_vertex", "quality", "area")}
        result["m1_to_m2_element_count_ratio"] = b["num_elem"] / prior_mesh["num_elem"]
        result["m1_to_m2_equivalent_h_ratio_from_counts"] = math.sqrt(prior_mesh["num_elem"] / b["num_elem"])
    return result


def _distribution(values: list[float] | np.ndarray) -> dict | None:
    data = np.asarray(values, dtype=float)
    if not data.size:
        return None
    if not np.isfinite(data).all() or np.any(data <= 0):
        raise RuntimeError("Native mesh length measurements must be finite and positive")
    return {"minimum": float(data.min()), "mean": float(data.mean()), "maximum": float(data.max())}


def summarize_native_mesh(native: dict, tree: dict) -> dict:
    """Measure wall and fixed spatial-region resolution from native mesh connectivity."""
    targets = [(component["tag"], mesh) for component in native["components"] for mesh in component["meshes"]]
    if len(targets) != 1:
        raise RuntimeError("Native resolution summary requires one unambiguous mesh")
    component_tag, mesh = targets[0]
    _validate_native_statistics(mesh)
    parameters = tree["parameters"]
    diameter, radius, xc, yc = [float(parameters[key]["value"]) for key in ("D", "R", "x_c", "y_c")]
    if not all(math.isfinite(value) for value in (diameter, radius, xc, yc)) or diameter <= 0 or radius <= 0:
        raise RuntimeError("Missing valid actual cylinder geometry parameters")
    component = next(item for item in tree["components"] if item["tag"] == component_tag)
    walls = [feature for physics in component["physics"] for feature in physics["features"] if feature["type"] == "WallBC"]
    if len(walls) != 1 or not walls[0]["selection_entities"]:
        raise RuntimeError("Cylinder wall selection is missing or ambiguous")
    wall_entities = walls[0]["selection_entities"]
    vertices = np.asarray(mesh["vertices"], dtype=float).T
    edges = mesh["elements"].get("edg")
    quads = mesh["elements"].get("quad")
    if not edges or not quads:
        raise RuntimeError("Actual wall edges and boundary-layer quadrilaterals are required")
    wall_edges = np.asarray(edges["connectivity"], dtype=int).T[np.isin(edges["entities"], wall_entities)]
    quad_nodes = np.asarray(quads["connectivity"], dtype=int).T
    if not len(wall_edges):
        raise RuntimeError("Native cylinder wall selection contains no edges")
    wall_vertices = vertices[np.unique(wall_edges)]
    if np.max(np.abs(np.linalg.norm(wall_vertices - [xc, yc], axis=1) - radius)) > max(1e-9, radius * 1e-5):
        raise RuntimeError("Selected wall mesh vertices do not match the actual cylinder parameters")
    node_quads: dict[int, set[int]] = {}
    for index, cell in enumerate(quad_nodes):
        for node in cell:
            node_quads.setdefault(int(node), set()).add(index)
    chords, first_heights, adjacent_quads = [], [], set()
    for edge in wall_edges:
        matches = node_quads.get(int(edge[0]), set()) & node_quads.get(int(edge[1]), set())
        if len(matches) != 1:
            raise RuntimeError("Each cylinder wall edge must have one adjacent boundary-layer quad")
        index = next(iter(matches))
        adjacent_quads.add(index)
        outer = [node for node in quad_nodes[index] if node not in edge]
        if len(outer) != 2:
            raise RuntimeError("Invalid native wall-adjacent quadrilateral")
        delta = vertices[edge[1]] - vertices[edge[0]]
        chord = float(np.linalg.norm(delta))
        if chord <= 0:
            raise RuntimeError("Zero-length native cylinder wall edge")
        tangent = delta / chord
        offsets = vertices[outer] - vertices[edge[0]]
        normal_distances = np.abs(tangent[0] * offsets[:, 1] - tangent[1] * offsets[:, 0])
        chords.append(chord)
        first_heights.append(float(normal_distances.mean()))
    centroids, max_edges, cell_diameters = [], [], []
    for element_type in ("tri", "quad"):
        for cell in np.asarray(mesh["elements"].get(element_type, {}).get("connectivity", []), dtype=int).T:
            points = vertices[cell]
            center = points.mean(axis=0)
            # Geometric angular ordering avoids assuming COMSOL's quadrilateral node order.
            angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
            ordered = points[np.argsort(angles)]
            max_edges.append(float(np.linalg.norm(np.roll(ordered, -1, axis=0) - ordered, axis=1).max()))
            cell_diameters.append(float(np.linalg.norm(points[:, None] - points[None, :], axis=2).max()))
            centroids.append(center)
    centers, max_edges, cell_diameters = np.asarray(centroids), np.asarray(max_edges), np.asarray(cell_diameters)
    definitions = {
        "near_cylinder": {"kind": "radial", "center_m": [xc, yc], "radius_m": 2 * diameter},
        "near_wake": {"kind": "box", "x_m": [xc + radius, xc + 5 * diameter], "y_m": [yc - 1.5 * diameter, yc + 1.5 * diameter]},
        "far_wake": {"kind": "box", "x_m": [xc + 5 * diameter, xc + 20 * diameter], "y_m": [yc - 2 * diameter, yc + 2 * diameter]},
    }
    regions = {}
    for name, definition in definitions.items():
        if definition["kind"] == "radial":
            mask = np.linalg.norm(centers - [xc, yc], axis=1) <= definition["radius_m"]
        else:
            mask = ((centers[:, 0] >= definition["x_m"][0]) & (centers[:, 0] <= definition["x_m"][1])
                    & (centers[:, 1] >= definition["y_m"][0]) & (centers[:, 1] <= definition["y_m"][1]))
        regions[name] = {"definition": definition, "cell_count": int(mask.sum()),
                         "max_edge_m": _distribution(max_edges[mask]), "diameter_m": _distribution(cell_diameters[mask])}
    return {
        "component_tag": component_tag, "mesh_tag": mesh["tag"], "num_elem": mesh["num_elem"],
        "num_vertex": mesh["num_vertex"], "quality": mesh["quality"], "area": mesh["area"],
        "cylinder_boundary": {"selection_entities": wall_entities, "edge_count": len(wall_edges),
            "wall_adjacent_quad_count": len(adjacent_quads), "chord_m": _distribution(chords),
            "first_layer_normal_height_m": _distribution(first_heights),
            "height_definition": "Mean absolute perpendicular distance of the two nonwall quad vertices to its wall chord; measured from native coordinates."},
        "regions": regions,
        "region_definition": "Cell centroids select fixed geometric ROIs; these are not detected separation or wake-flow boundaries. Diameter is maximum vertex-to-vertex distance; max_edge uses geometric polygon ordering.",
    }


def classify_comparison(stats_m0: dict, stats_m1: dict) -> dict:
    """Use existing stable-cycle/four-metric gates for preliminary sensitivity only."""
    if any(stats.get("num_cycles", 0) < 5 for stats in (stats_m0, stats_m1)):
        comparison = {
            "can_compare": False, "conclusion": "INSUFFICIENT_CYCLES",
            "message": "M0/M1 comparison requires at least five stable complete cycles per mesh",
            "case_a": stats_m0, "case_b": stats_m1,
        }
        acceptance = "INSUFFICIENT_CYCLES"
    elif not stats_m0.get("is_stable", False) or not stats_m1.get("is_stable", False):
        comparison = compare_cases(stats_m0, stats_m1)
        acceptance = "UNSTABLE"
    else:
        comparison = compare_cases(stats_m0, stats_m1)
        acceptance = "PASS" if comparison["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE" else "SENSITIVE"
    comparison["acceptance_status"] = acceptance
    comparison["mesh_independence_established"] = False
    comparison["grid_independence_phase_can_close"] = False
    comparison["scientific_summary"] = (
        "Preliminary M0/M1 native-mesh sensitivity at unchanged rtol=1e-4 and const maximum "
        "internal time step 0.05 s, using the existing four-metric 1% thresholds. "
        + comparison["conclusion"]
        + ". Two meshes do not establish grid independence, observed convergence order, "
        "or an error bound relative to the exact solution."
    )
    return comparison


def classify_three_mesh_comparison(stats_m0: dict, stats_m1: dict, stats_m2: dict) -> dict:
    """Compare M0/M1/M2 using M1/M2 as the primary adjacent 1% gate while retaining three-level trends."""
    meshes = (stats_m0, stats_m1, stats_m2)
    if any(stats.get("num_cycles", 0) < 5 for stats in meshes):
        return {
            "can_compare": False,
            "conclusion": "INSUFFICIENT_CYCLES",
            "acceptance_status": "INSUFFICIENT_CYCLES",
            "message": "M0/M1/M2 comparison requires at least five stable complete cycles per mesh",
            "case_m0": stats_m0,
            "case_m1": stats_m1,
            "case_m2": stats_m2,
            "case_a": stats_m1,
            "case_b": stats_m2,
            "adjacent_m1_m2_within_1pct": False,
            "all_primary_deltas_reduced": False,
            "mesh_independence_established": False,
            "grid_independence_phase_can_close": False,
            "scientific_summary": (
                "Three-mesh M0/M1/M2 study cannot compare accuracy because at least one mesh "
                "has fewer than five complete cycles."
            ),
        }
    if not all(stats.get("is_stable", False) for stats in meshes):
        primary = compare_cases(stats_m1, stats_m2)
        primary.update(
            can_compare=False,
            conclusion="UNSTABLE",
            acceptance_status="UNSTABLE",
            message="M0/M1/M2 comparison requires all three meshes to pass the late-window periodic stability gate",
            case_m0=stats_m0,
            case_m1=stats_m1,
            case_m2=stats_m2,
            adjacent_m1_m2_within_1pct=False,
            all_primary_deltas_reduced=False,
            mesh_independence_established=False,
            grid_independence_phase_can_close=False,
            scientific_summary=(
                "Three-mesh M0/M1/M2 study halted at periodic stability check before accuracy comparison."
            ),
        )
        return primary

    m0_vs_m1 = compare_cases(stats_m0, stats_m1)
    m1_vs_m2 = compare_cases(stats_m1, stats_m2)
    m0_vs_m2 = compare_cases(stats_m0, stats_m2)
    metric_fields = {
        "St_zc": ("St_zc_mean", "st_tol_pct"),
        "Cd_mean": ("Cd_mean", "cd_tol_pct"),
        "Cl_amp": ("Cl_amp_mean", "amp_tol_pct"),
        "Cl_rms": ("Cl_demeaned_rms", "rms_tol_pct"),
    }
    trends = {}
    for key, (field, tol_key) in metric_fields.items():
        q0, q1, q2 = float(stats_m0[field]), float(stats_m1[field]), float(stats_m2[field])
        d01, d12, d02 = q1 - q0, q2 - q1, q2 - q0
        r01 = (d01 / abs(q0)) * 100.0
        r12 = (d12 / abs(q1)) * 100.0
        r02 = (d02 / abs(q0)) * 100.0
        tol_pct = float(m1_vs_m2["thresholds_pct"][tol_key])
        if d01 * d12 > 0:
            direction = "monotonic"
        elif d01 == 0.0 or d12 == 0.0:
            direction = "unchanged"
        else:
            direction = "reversing"
        trends[key] = {
            "m0": q0,
            "m1": q1,
            "m2": q2,
            "delta_01": d01,
            "signed_rel_diff_01_pct": r01,
            "abs_rel_diff_01_pct": abs(r01),
            "delta_12": d12,
            "signed_rel_diff_12_pct": r12,
            "abs_rel_diff_12_pct": abs(r12),
            "delta_02": d02,
            "signed_rel_diff_02_pct": r02,
            "abs_rel_diff_02_pct": abs(r02),
            "abs_delta_reduced": abs(d12) < abs(d01),
            "abs_rel_diff_reduced": abs(r12) < abs(r01),
            "direction": direction,
            "threshold_pct": tol_pct,
            "within_1pct_m0_m1": abs(r01) <= tol_pct,
            "within_1pct_m1_m2": abs(r12) <= tol_pct,
        }
    adjacent_ok = m1_vs_m2["conclusion"] == "INSENSITIVE_WITHIN_TESTED_RANGE"
    all_reduced = all(item["abs_rel_diff_reduced"] for item in trends.values())
    acceptance = "PASS" if adjacent_ok else "SENSITIVE"
    result = deepcopy(m1_vs_m2)
    result.update(
        primary_pair="M1_vs_M2",
        acceptance_status=acceptance,
        case_m0=stats_m0,
        case_m1=stats_m1,
        case_m2=stats_m2,
        m0_vs_m1=m0_vs_m1,
        m1_vs_m2=m1_vs_m2,
        m0_vs_m2=m0_vs_m2,
        three_mesh_trends=trends,
        adjacent_m1_m2_within_1pct=adjacent_ok,
        all_primary_deltas_reduced=all_reduced,
        mesh_independence_established=False,
        grid_independence_phase_can_close=bool(adjacent_ok and all_reduced),
        scientific_summary=(
            "Three-mesh M0/M1/M2 native-mesh sensitivity at unchanged rtol=1e-4 and const maximum "
            "internal time step 0.05 s, with M1/M2 as the primary adjacent 1% gate: "
            + m1_vs_m2["conclusion"]
            + ". Non-uniform unstructured mesh scaling does not establish a formal asymptotic GCI or "
            "an exact-solution error bound; three-level trends are reported directly without extrapolation."
        ),
    )
    return result


def analyze_wake(trial_dir: Path, window: list[float], u_inf: float) -> dict:
    """Integrate verified SI probe channels over the same case's exact lift-cycle window."""
    if not math.isfinite(u_inf) or u_inf <= 0:
        raise ValueError("U_inf must be finite and positive")
    if len(window) != 2 or not all(math.isfinite(value) for value in window) or window[1] <= window[0]:
        raise ValueError("Wake window must have finite, increasing bounds")
    result = {"window": list(window), "u_inf_m_s": float(u_inf), "channels": {}}
    for channel in ("u", "v"):
        t, y = load_comsol_1d_timeseries(Path(trial_dir) / f"wake_{channel}.txt")[:2]
        t, y = np.asarray(t, dtype=float), np.asarray(y, dtype=float)
        if t.ndim != 1 or y.shape != t.shape or len(t) < 2:
            raise ValueError(f"Invalid wake {channel} time series shape")
        if not np.isfinite(t).all() or not np.isfinite(y).all() or not np.all(np.diff(t) > 0):
            raise ValueError(f"Wake {channel} requires finite values and strictly increasing times")
        if t[0] > window[0] or t[-1] < window[1]:
            raise ValueError(f"Wake {channel} does not cover the complete statistical window")
        mean, rms = compute_demeaned_rms(t, y, window[0], window[1])
        result["channels"][channel] = {
            "mean_m_s": mean,
            "demeaned_rms_m_s": rms,
            "mean_over_u_inf": mean / u_inf,
            "rms_over_u_inf": rms / u_inf,
        }
    return result


def compare_wake_statistics(wake_m0: dict, wake_m1: dict) -> dict:
    """Normalize differences by freestream speed, including near-zero mean v."""
    u_inf = wake_m0["u_inf_m_s"]
    if not math.isfinite(u_inf) or u_inf <= 0 or u_inf != wake_m1["u_inf_m_s"]:
        raise ValueError("Wake comparison requires the same finite positive U_inf")
    result = {
        "normalization": "U_inf", "u_inf_m_s": u_inf,
        "diagnostic_only": True, "mesh_independence_established": False,
        "windows": {"m0": wake_m0["window"], "m1": wake_m1["window"]},
        "channels": {},
    }
    for channel in ("u", "v"):
        a, b = wake_m0["channels"][channel], wake_m1["channels"][channel]
        if not all(math.isfinite(item[key]) for item in (a, b) for key in ("mean_m_s", "demeaned_rms_m_s")):
            raise ValueError("Wake comparison requires finite moments")
        d_mean, d_rms = b["mean_m_s"] - a["mean_m_s"], b["demeaned_rms_m_s"] - a["demeaned_rms_m_s"]
        result["channels"][channel] = {
            "m0": a, "m1": b, "delta_mean_m_s": d_mean, "delta_rms_m_s": d_rms,
            "delta_mean_over_u_inf": d_mean / u_inf,
            "delta_rms_over_u_inf": d_rms / u_inf,
        }
    return result


def compare_three_wake_statistics(wake_m0: dict, wake_m1: dict, wake_m2: dict) -> dict:
    """Normalize M0/M1/M2 wake probe differences by freestream speed (diagnostic only)."""
    m0_vs_m1 = compare_wake_statistics(wake_m0, wake_m1)
    m1_vs_m2 = compare_wake_statistics(wake_m1, wake_m2)
    m0_vs_m2 = compare_wake_statistics(wake_m0, wake_m2)
    u_inf = wake_m0["u_inf_m_s"]
    channels = {}
    for channel in ("u", "v"):
        c01 = m0_vs_m1["channels"][channel]
        c12 = m1_vs_m2["channels"][channel]
        c02 = m0_vs_m2["channels"][channel]
        channels[channel] = {
            "m0": wake_m0["channels"][channel],
            "m1": wake_m1["channels"][channel],
            "m2": wake_m2["channels"][channel],
            "delta_01_mean_m_s": c01["delta_mean_m_s"],
            "delta_01_rms_m_s": c01["delta_rms_m_s"],
            "delta_01_mean_over_u_inf": c01["delta_mean_over_u_inf"],
            "delta_01_rms_over_u_inf": c01["delta_rms_over_u_inf"],
            "delta_12_mean_m_s": c12["delta_mean_m_s"],
            "delta_12_rms_m_s": c12["delta_rms_m_s"],
            "delta_12_mean_over_u_inf": c12["delta_mean_over_u_inf"],
            "delta_12_rms_over_u_inf": c12["delta_rms_over_u_inf"],
            "delta_02_mean_m_s": c02["delta_mean_m_s"],
            "delta_02_rms_m_s": c02["delta_rms_m_s"],
            "delta_02_mean_over_u_inf": c02["delta_mean_over_u_inf"],
            "delta_02_rms_over_u_inf": c02["delta_rms_over_u_inf"],
        }
    return {
        "normalization": "U_inf",
        "u_inf_m_s": u_inf,
        "diagnostic_only": True,
        "mesh_independence_established": False,
        "windows": {"m0": wake_m0["window"], "m1": wake_m1["window"], "m2": wake_m2["window"]},
        "m0_vs_m1": m0_vs_m1,
        "m1_vs_m2": m1_vs_m2,
        "m0_vs_m2": m0_vs_m2,
        "channels": channels,
    }


def _check_solution_stats(stats: dict) -> None:
    if (stats.get("has_nan_or_inf") is not False or stats.get("has_physical_field") is not True
            or stats.get("num_timesteps") != 1001 or stats.get("time_min") != 0.0
            or stats.get("time_max") != 200.0 or stats.get("num_dofs", 0) <= 0):
        raise RuntimeError("Solution must contain finite physical fields and 1001 output times over 0–200 s")


def _load_accepted_m1_reference(
    output_dir: Path, report: dict, tree_m0: dict, native_m0: dict, pre_m0: dict, m0_dofs: int,
    *, precheck_subdir: str = "precheck_m1",
) -> tuple[dict, dict, dict, dict]:
    """Read and verify the accepted M1 reference artifacts without re-solving M1."""
    print("Reading accepted M1 reference; no M1 solve...", flush=True)
    tree_m1 = inspect_model(M1_MODEL)
    native_m1 = inspect_native_mesh(M1_MODEL)
    save_json(output_dir / "m1_model_inspection.json", tree_m1)
    save_json(output_dir / "m1_native_mesh.json", native_m1)
    m1_plan = json.loads(DEFAULT_PLAN.read_text(encoding="utf-8"))
    check_mesh_refinement(native_m0, native_m1, m1_plan)
    pre_m1 = run_prechecks(
        model_path=M1_MODEL, wake_u_path=M1_DIR / "wake_u.txt",
        wake_v_path=M1_DIR / "wake_v.txt", output_dir=output_dir / precheck_subdir,
    )
    report["m1_prechecks"] = pre_m1
    if pre_m1["overall_prechecks_status"] != "PASS":
        raise RuntimeError("Accepted M1 prechecks failed; M2 was not started")
    check_invariants(pre_m0["precheck_b_solver_model"], pre_m1["precheck_b_solver_model"], tree_m0, tree_m1)
    m1_response = json.loads((M1_DIR / "solve_response.json").read_text(encoding="utf-8"))
    if m1_response["output_sha256"].lower() != native_m1["sha256"].lower():
        raise RuntimeError("Accepted M1 solution statistics are not bound to the inspected M1 model hash")
    _check_solution_stats(m1_response["solution_stats"])
    if m1_response["solution_stats"]["num_dofs"] <= m0_dofs:
        raise RuntimeError("Accepted M1 solution must have more DOFs than M0")
    stats_m1 = analyze_case("M1 (native mesh h/1.4)", M1_DIR)
    if not classify_comparison(stats_m1, stats_m1)["can_compare"]:
        raise RuntimeError("Accepted M1 must pass the five-complete-cycle stability gate before M2")
    report["m1_mesh_summary"] = summarize_native_mesh(native_m1, tree_m1)
    report["m1_solution_stats"] = m1_response["solution_stats"]
    report["m1_solution_stats_source"] = {
        "path": str(M1_DIR / "solve_response.json"),
        "model_sha256": m1_response["output_sha256"],
        "readback_scope": "Previously executed accepted M1 solve response, bound to current M1 model SHA-256; M1 is not solved again.",
    }
    return tree_m1, native_m1, pre_m1, stats_m1


def _finish_postprocess(
    output_dir: Path, report: dict, solution: dict, elapsed: float | str,
    tree_m0: dict, native_m0: dict, native_prepared: dict, pre_m0: dict, stats_m0: dict,
    *, m1_context: tuple[dict, dict, dict, dict] | None = None,
) -> dict:
    """Export and verify one already solved M1 or M2; this helper never builds or solves."""
    plan = report["refinement_plan"]
    level, factor = _plan_level_and_factor(plan)
    level_key = level.lower()
    prepared = output_dir / f"re100_s1b2_{level_key}_mesh.mph"
    solved = output_dir / f"re100_s1b2_{level_key}_solved.mph"
    export_response = export_s1_diagnostics(
        model_path=solved, output_dir=output_dir,
        expected_steps=1001, expected_dt=0.20, expected_t_final=200.0,
        snapshot_times=[10.0, 40.0, 100.0, 195.0, 200.0], allow_overwrite=False,
    )
    save_json(output_dir / "export_response.json", export_response)
    pre_target = run_prechecks(
        model_path=solved, wake_u_path=output_dir / "wake_u.txt",
        wake_v_path=output_dir / "wake_v.txt", output_dir=output_dir / f"precheck_{level_key}",
    )
    report[f"{level_key}_prechecks"] = pre_target
    if pre_target["overall_prechecks_status"] != "PASS":
        raise RuntimeError(f"{level} strict unit/probe prechecks failed")
    tree_target = inspect_model(solved)
    native_target = inspect_native_mesh(solved)
    save_json(output_dir / f"{level_key}_model_inspection.json", tree_target)
    save_json(output_dir / f"{level_key}_native_mesh.json", native_target)
    if (solution["source_sha256"].lower() != native_prepared["sha256"].lower()
            or solution["output_sha256"].lower() != native_target["sha256"].lower()):
        raise RuntimeError(f"{level} solve hash chain does not match prepared mesh and solved model")
    prior_native = m1_context[1] if m1_context is not None else None
    report["native_mesh_comparison"] = check_mesh_refinement(
        native_m0, native_target, plan, prior_refined=prior_native,
    )
    if native_prepared["components"] != native_target["components"]:
        raise RuntimeError(f"{level} native mesh changed between preparation and solve")
    settings_cmp = check_invariants(
        pre_m0["precheck_b_solver_model"], pre_target["precheck_b_solver_model"], tree_m0, tree_target,
    )
    if m1_context is not None:
        tree_m1, _, pre_m1, _ = m1_context
        check_invariants(
            pre_m1["precheck_b_solver_model"], pre_target["precheck_b_solver_model"], tree_m1, tree_target,
        )
        settings_cmp["m1_solver"] = pre_m1["precheck_b_solver_model"]["solver_details"]
        settings_cmp["m2_solver"] = pre_target["precheck_b_solver_model"]["solver_details"]
    report["settings_comparison"] = settings_cmp
    report[f"{level_key}_mesh_summary"] = summarize_native_mesh(native_target, tree_target)
    save_json(output_dir / "solver_metadata.json", {
        "case_id": f"S1-B2 {level}",
        "solve_duration_sec": elapsed,
        "comsol_elapsed_ms": solution.get("elapsed_ms", "UNOBTAINED"),
        "solve_duration_scope": "run_study call wall clock" if isinstance(elapsed, (int, float)) else "UNOBTAINED: original call wall clock was not saved before export failed",
        "source_model": str(prepared),
        "source_sha256": solution["source_sha256"],
        "solved_model_sha256": solution["output_sha256"],
        **pre_target["precheck_b_solver_model"],
    })
    stats_target = analyze_case(f"{level} (native mesh h/{factor:g})", output_dir)
    u_inf = float(tree_m0["parameters"]["U_inf"]["value"])
    if m1_context is None:
        comparison = classify_comparison(stats_m0, stats_target)
        report["physical_comparison"] = comparison
        if not comparison["can_compare"]:
            raise RuntimeError(comparison.get("message", "M0/M1 stable-cycle comparison failed"))
        report["wake_comparison"] = compare_wake_statistics(
            analyze_wake(M0_DIR, stats_m0["window"], u_inf),
            analyze_wake(output_dir, stats_target["window"], u_inf),
        )
        summary_label = "M0/M1 preliminary sensitivity"
    else:
        stats_m1 = m1_context[3]
        comparison = classify_three_mesh_comparison(stats_m0, stats_m1, stats_target)
        report["physical_comparison"] = comparison
        if not comparison["can_compare"]:
            raise RuntimeError(comparison.get("message", "M0/M1/M2 stable-cycle comparison failed"))
        report["wake_comparison"] = compare_three_wake_statistics(
            analyze_wake(M0_DIR, stats_m0["window"], u_inf),
            analyze_wake(M1_DIR, stats_m1["window"], u_inf),
            analyze_wake(output_dir, stats_target["window"], u_inf),
        )
        summary_label = "M0/M1/M2 sensitivity (primary M1/M2)"
    report.pop("error", None)
    report.pop("error_details", None)
    report["execution_status"] = "COMPLETE"
    report["status"] = comparison["acceptance_status"]
    print(json.dumps(comparison["comparisons"], indent=2), flush=True)
    print(f"{summary_label}: {report['status']}; grid independence not established", flush=True)
    return report


def run_mesh_m1(output_dir: Path, refinement_plan: dict) -> dict:
    """Read M0 (and M1 for M2 plans), rebuild a separate native mesh from M0, solve once, and compare."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    plan = deepcopy(refinement_plan)
    level, factor = _plan_level_and_factor(plan)
    level_key = level.lower()
    hashes_before = snapshot_protected_files()
    report = {
        "status": "RUNNING", "mesh_level": level, "output_dir": str(output_dir),
        "refinement_plan": plan, "mesh_independence_established": False,
        "protected_hashes_before": hashes_before,
    }
    save_json(output_dir / "mesh_report.json", report)
    try:
        tree_m0 = inspect_model(M0_MODEL)
        native_m0 = inspect_native_mesh(M0_MODEL)
        save_json(output_dir / "m0_model_inspection.json", tree_m0)
        save_json(output_dir / "m0_native_mesh.json", native_m0)
        validate_refinement_plan(native_m0, plan)
        transient_studies = [item["tag"] for item in tree_m0["studies"]
                             if any(feature["type"] == "Transient" for feature in item["features"])]
        if len(transient_studies) != 1:
            raise RuntimeError(f"Expected one transient study; found {transient_studies}")
        print("Reading accepted Case C/M0; no M0 solve...", flush=True)
        pre_m0 = run_prechecks(
            model_path=M0_MODEL, wake_u_path=M0_DIR / "wake_u.txt",
            wake_v_path=M0_DIR / "wake_v.txt", output_dir=output_dir / "precheck_m0",
        )
        report["m0_prechecks"] = pre_m0
        if pre_m0["overall_prechecks_status"] != "PASS":
            raise RuntimeError(f"M0 prechecks failed; {level} was not started")
        check_time_policy(pre_m0["precheck_b_solver_model"]["solver_details"])
        source_response = json.loads((M0_DIR / "solve_response.json").read_text(encoding="utf-8"))
        if source_response["output_sha256"].lower() != native_m0["sha256"].lower():
            raise RuntimeError("Accepted M0 solution statistics are not bound to the inspected model hash")
        _check_solution_stats(source_response["solution_stats"])
        stats_m0 = analyze_case("M0 (accepted Case C native mesh)", M0_DIR)
        if not classify_comparison(stats_m0, stats_m0)["can_compare"]:
            raise RuntimeError(f"M0 must pass the existing five-complete-cycle stability gate before {level}")
        report["m0_mesh_summary"] = summarize_native_mesh(native_m0, tree_m0)
        report["m0_solution_stats"] = source_response["solution_stats"]
        report["m0_solution_stats_source"] = {
            "path": str(M0_DIR / "solve_response.json"),
            "model_sha256": source_response["output_sha256"],
            "readback_scope": "Previously executed accepted M0 solve response, bound to current model SHA-256; M0 is not solved again.",
        }
        m1_context = None
        if level == "M2":
            m1_context = _load_accepted_m1_reference(
                output_dir, report, tree_m0, native_m0, pre_m0,
                source_response["solution_stats"]["num_dofs"],
            )
        prepared = output_dir / f"re100_s1b2_{level_key}_mesh.mph"
        start = time.perf_counter()
        refinement = refine_native_mesh(model_path=M0_MODEL, output_path=prepared, plan=plan, allow_overwrite=False)
        report["mesh_rebuild_duration_sec"] = time.perf_counter() - start
        save_json(output_dir / "native_refinement_response.json", refinement)
        if refinement.get("stored_solution_data_empty") is not True:
            raise RuntimeError(f"{level} mesh preparation must clear inherited solution data in its isolated copy")
        native_prepared = inspect_native_mesh(prepared)
        save_json(output_dir / f"{level_key}_prepared_native_mesh.json", native_prepared)
        if (refinement["source_sha256"].lower() != native_m0["sha256"].lower()
                or refinement["output_sha256"].lower() != native_prepared["sha256"].lower()):
            raise RuntimeError("Native mesh refinement source/output hash chain does not match actual files")
        prior_native = m1_context[1] if m1_context is not None else None
        report["prepared_mesh_comparison"] = check_mesh_refinement(
            native_m0, native_prepared, plan, prior_refined=prior_native,
        )
        solved = output_dir / f"re100_s1b2_{level_key}_solved.mph"
        print(f"Solving only {level} (h0/{factor:g}) on the native refined mesh, with unchanged accepted C time settings...", flush=True)
        start = time.perf_counter()
        solution = run_study(
            model_path=prepared, output_path=solved,
            study_tag=transient_studies[0], allow_overwrite=False,
        )
        elapsed = time.perf_counter() - start
        report["solve_duration_sec"] = elapsed
        report["comsol_elapsed_ms"] = solution.get("elapsed_ms", "UNOBTAINED")
        save_json(output_dir / "mesh_report.json", report)
        save_json(output_dir / "solve_response.json", solution)
        log_path = output_dir / "solver_run.log"
        log_path.write_text(solution["log"], encoding="utf-8")
        report["actual_step_log"] = check_step_log(log_path)
        _check_solution_stats(solution["solution_stats"])
        min_dofs = (
            report["m1_solution_stats"]["num_dofs"]
            if m1_context is not None
            else source_response["solution_stats"]["num_dofs"]
        )
        prior_label = "M1" if m1_context is not None else "M0"
        if solution["solution_stats"]["num_dofs"] <= min_dofs:
            raise RuntimeError(f"The actual refined {level} solution must have more DOFs than {prior_label}")
        report[f"{level_key}_solution_stats"] = solution["solution_stats"]
        return _finish_postprocess(
            output_dir, report, solution, elapsed, tree_m0, native_m0,
            native_prepared, pre_m0, stats_m0, m1_context=m1_context,
        )
    except Exception as exc:
        report.update(status="FAIL", error=str(exc), error_details=getattr(exc, "details", ""))
        (output_dir / "failure.log").write_text(traceback.format_exc() + "\n" + report["error_details"], encoding="utf-8")
        raise
    finally:
        hashes_after = snapshot_protected_files()
        report["protected_hashes_after"] = hashes_after
        report["protected_files_unchanged"] = hashes_before == hashes_after
        if hashes_before != hashes_after:
            report.update(status="FAIL", immutability_error="A protected historical file changed")
        save_json(output_dir / "mesh_report.json", report)
        if hashes_before != hashes_after:
            raise RuntimeError("A protected historical file changed")


def run_mesh_m2(output_dir: Path, refinement_plan: dict) -> dict:
    """Explicit entry point for M2 (h0/1.96) native mesh refinement and three-level M0/M1/M2 comparison."""
    level, _ = _plan_level_and_factor(refinement_plan)
    if level != "M2":
        raise RuntimeError("run_mesh_m2 requires an explicit M2 (h0/1.96) refinement plan")
    return run_mesh_m1(output_dir, refinement_plan)


def _preserve_failed_artifact(output_dir: Path, name: str, archive_name: str) -> str | None:
    source = output_dir / name
    if not source.is_file():
        return None
    destination = output_dir / archive_name
    if destination.exists():
        destination = destination.with_name(f"{destination.stem}_{uuid.uuid4().hex[:8]}{destination.suffix}")
    with destination.open("xb") as handle:
        handle.write(source.read_bytes())
    return str(destination)


def verify_saved_mesh_m1(output_dir: Path) -> dict:
    """Recover only postprocessing for an existing M1 or M2 solve, preserving the failed attempt."""
    output_dir = output_dir.resolve()
    report_path = output_dir / "mesh_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    plan = report["refinement_plan"]
    level, _ = _plan_level_and_factor(plan)
    level_key = level.lower()
    if report.get("execution_status") == "COMPLETE":
        raise RuntimeError(f"{level} postprocessing is already complete; no recovery is needed")
    preserved_report = _preserve_failed_artifact(output_dir, "mesh_report.json", "mesh_report_initial_export_failure.json")
    preserved_failure = _preserve_failed_artifact(output_dir, "failure.log", "failure_initial_export_failure.log")
    recovery_id = uuid.uuid4().hex[:8]
    report["postprocess_recovery"] = {
        "operation": "verify_saved_mesh_m1", "refine_or_solve_called": False,
        "preserved_report": preserved_report, "preserved_failure_log": preserved_failure,
        "initial_status": report.get("status"), "initial_error": report.get("error"),
    }
    hashes_before = report["protected_hashes_before"]
    try:
        if snapshot_protected_files() != hashes_before:
            raise RuntimeError(f"A protected historical file differs from the original {level} run")
        prepared = output_dir / f"re100_s1b2_{level_key}_mesh.mph"
        solved = output_dir / f"re100_s1b2_{level_key}_solved.mph"
        solution = json.loads((output_dir / "solve_response.json").read_text(encoding="utf-8"))
        refinement = json.loads((output_dir / "native_refinement_response.json").read_text(encoding="utf-8"))
        source_response = json.loads((M0_DIR / "solve_response.json").read_text(encoding="utf-8"))
        actual_m0, actual_prepared, actual_solved = [compute_file_hash(path).lower() for path in (M0_MODEL, prepared, solved)]
        if (actual_m0 != plan["source_sha256"].lower()
                or actual_m0 != source_response["output_sha256"].lower()
                or actual_m0 != refinement["source_sha256"].lower()
                or actual_prepared != refinement["output_sha256"].lower()
                or actual_prepared != solution["source_sha256"].lower()
                or actual_solved != solution["output_sha256"].lower()):
            raise RuntimeError(f"Saved M0/prepared {level}/solved {level} hash chain failed before export")
        if refinement.get("stored_solution_data_empty") is not True:
            raise RuntimeError(f"Saved refinement evidence does not confirm isolated {level} solution clearing")
        _check_solution_stats(source_response["solution_stats"])
        _check_solution_stats(solution["solution_stats"])
        if solution["solution_stats"]["num_dofs"] <= source_response["solution_stats"]["num_dofs"]:
            raise RuntimeError(f"Saved {level} solution has no actual DOF increase")
        report["actual_step_log"] = check_step_log(output_dir / "solver_run.log")
        tree_m0, native_m0 = inspect_model(M0_MODEL), inspect_native_mesh(M0_MODEL)
        native_prepared = inspect_native_mesh(prepared)
        validate_refinement_plan(native_m0, plan)
        pre_m0 = run_prechecks(
            model_path=M0_MODEL, wake_u_path=M0_DIR / "wake_u.txt",
            wake_v_path=M0_DIR / "wake_v.txt", output_dir=output_dir / f"precheck_m0_recovery_{recovery_id}",
        )
        if pre_m0["overall_prechecks_status"] != "PASS":
            raise RuntimeError(f"M0 recovery readback failed before {level} export")
        check_time_policy(pre_m0["precheck_b_solver_model"]["solver_details"])
        report["m0_prechecks"] = pre_m0
        stats_m0 = analyze_case("M0 (accepted Case C native mesh)", M0_DIR)
        if not classify_comparison(stats_m0, stats_m0)["can_compare"]:
            raise RuntimeError("Saved M0 fails the five-cycle stability gate")
        m1_context = None
        if level == "M2":
            m1_context = _load_accepted_m1_reference(
                output_dir, report, tree_m0, native_m0, pre_m0,
                source_response["solution_stats"]["num_dofs"],
                precheck_subdir=f"precheck_m1_recovery_{recovery_id}",
            )
            if solution["solution_stats"]["num_dofs"] <= report["m1_solution_stats"]["num_dofs"]:
                raise RuntimeError("Saved M2 solution does not have more DOFs than M1")
        prior_native = m1_context[1] if m1_context is not None else None
        report["prepared_mesh_comparison"] = check_mesh_refinement(
            native_m0, native_prepared, plan, prior_refined=prior_native,
        )
        elapsed = report.get("solve_duration_sec", "UNOBTAINED")
        if elapsed != "UNOBTAINED" and (not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0):
            raise RuntimeError("Invalid saved solve call duration")
        report["comsol_elapsed_ms"] = solution.get("elapsed_ms", "UNOBTAINED")
        report["status"] = "POSTPROCESS_RECOVERY"
        save_json(report_path, report)
        return _finish_postprocess(
            output_dir, report, solution, elapsed, tree_m0, native_m0,
            native_prepared, pre_m0, stats_m0, m1_context=m1_context,
        )
    except Exception as exc:
        report.update(status="FAIL", error=str(exc), error_details=getattr(exc, "details", ""))
        recovery_log = output_dir / f"postprocess_recovery_failure_{recovery_id}.log"
        recovery_log.write_text(traceback.format_exc() + "\n" + report["error_details"], encoding="utf-8")
        report["postprocess_recovery"]["failure_log"] = str(recovery_log)
        raise
    finally:
        hashes_after = snapshot_protected_files()
        report["protected_hashes_after"] = hashes_after
        report["protected_files_unchanged"] = hashes_before == hashes_after
        if hashes_before != hashes_after:
            report.update(status="FAIL", immutability_error="A protected historical file changed")
        save_json(report_path, report)
        if hashes_before != hashes_after:
            raise RuntimeError("A protected historical file changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--verify-saved", type=Path, help="Finish exports/checks for an already solved M1/M2; never rebuild or solve")
    args = parser.parse_args()
    if args.verify_saved is not None:
        if args.output_dir is not None:
            parser.error("--verify-saved and --output-dir are mutually exclusive")
        report = verify_saved_mesh_m1(args.verify_saved)
        return 0 if report["status"] == "PASS" else 2
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    level, _ = _plan_level_and_factor(plan)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or ROOT / "runs" / f"re100_s1b2_{level.lower()}_{stamp}_{uuid.uuid4().hex[:8]}"
    print(f"{level} output: {output_dir}", flush=True)
    report = run_mesh_m1(output_dir, plan)
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
