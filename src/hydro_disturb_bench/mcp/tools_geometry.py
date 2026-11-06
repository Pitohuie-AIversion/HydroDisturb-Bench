"""Geometry Modeling, CAD Operations, and Geometric Selections Tools."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .executor import execute_java_code
from .safety import format_error, validate_cow_paths, ERR_MODEL_NOT_FOUND


def comsol_geom_primitive(
    source_model: str,
    output_model: str,
    shape: str,
    tag: str,
    params: Dict[str, Union[str, float, int, List[float]]],
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a 2D or 3D geometric primitive to the geometry sequence.

    Supported shapes:
        2D: "Circle", "Rectangle", "Square", "Ellipse", "Polygon"
        3D: "Block", "Cylinder", "Sphere", "Cone", "Torus"

    Example params:
        - Circle: {"r": "0.05", "pos": ["0.15", "0.12"]}
        - Rectangle: {"size": ["2.2", "0.41"], "pos": ["0", "0"]}
        - Block: {"size": ["1.0", "0.5", "0.2"], "pos": ["0", "0", "0"]}
        - Cylinder: {"r": "0.1", "h": "0.5", "axis": ["0", "0", "1"]}
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    setters = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            arr_str = ", ".join(f'"{x}"' for x in v)
            setters.append(f'f.set("{k}", new String[]{{{arr_str}}});')
        else:
            setters.append(f'f.set("{k}", "{v}");')

    setters_code = "\n        ".join(setters)
    snippet = f"""
        GeomSequence geom = model.component("{comp_tag}").geom("{geom_tag}");
        GeomFeature f = geom.create("{tag}", "{shape}");
        {setters_code}
        geom.run("{tag}");
        System.out.println("GEOM_PRIMITIVE_CREATED: {tag} ({shape})");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "geom_primitive"
        res["shape"] = shape
        res["tag"] = tag
    return res


def comsol_geom_boolean(
    source_model: str,
    output_model: str,
    op_type: str,
    tag: str,
    input_tags: List[str],
    subtract_tags: Optional[List[str]] = None,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Performs Boolean operations (Union, Difference, Intersection) on geometric objects.

    Args:
        op_type: "Union", "Difference", "Intersection", "Compose".
        tag: New feature tag (e.g. "dif1", "uni1").
        input_tags: List of primary geometric object tags.
        subtract_tags: For "Difference", list of objects to subtract from input_tags.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    inp_str = ", ".join(f'"{t}"' for t in input_tags)
    extra = ""
    if op_type.lower() == "difference" and subtract_tags:
        sub_str = ", ".join(f'"{t}"' for t in subtract_tags)
        extra = f'f.selection("input2").set(new String[]{{{sub_str}}});'

    snippet = f"""
        GeomSequence geom = model.component("{comp_tag}").geom("{geom_tag}");
        GeomFeature f = geom.create("{tag}", "{op_type}");
        f.selection("input").set(new String[]{{{inp_str}}});
        {extra}
        geom.run("{tag}");
        System.out.println("GEOM_BOOLEAN_SUCCESS: {tag} ({op_type})");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "geom_boolean"
        res["op_type"] = op_type
        res["tag"] = tag
    return res


def comsol_geom_build(
    source_model: str,
    output_model: str,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Rebuilds the entire geometry sequence and returns topology counts (domains, boundaries, vertices)."""
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    snippet = f"""
        GeomSequence geom = model.component("{comp_tag}").geom("{geom_tag}");
        geom.run();
        int domCount = geom.getNDomains();
        int bndCount = geom.getNBoundaries();
        int edgCount = geom.getNEdges();
        int vtxCount = geom.getNVertices();
        System.out.println("GEOM_BUILD_INFO={{\\"domains\\":" + domCount + ",\\"boundaries\\":" + bndCount +
            ",\\"edges\\":" + edgCount + ",\\"vertices\\":" + vtxCount + "}}");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "geom_build"
        for line in res.get("console_output", "").splitlines():
            if line.startswith("GEOM_BUILD_INFO="):
                try:
                    res["topology"] = json.loads(line.replace("GEOM_BUILD_INFO=", ""))
                except Exception:
                    pass
    return res


def comsol_create_selection(
    source_model: str,
    output_model: str,
    sel_type: str,
    sel_tag: str,
    entity_dim: int,
    entities: Optional[List[int]] = None,
    filter_params: Optional[Dict[str, Any]] = None,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Creates geometric entity selections (Explicit, Box, Ball, Cylinder).

    Args:
        sel_type: "Explicit", "Box", "Ball", "Cylinder", "Adjacent".
        sel_tag: New selection tag (e.g. "sel_inlet", "sel_cylinder").
        entity_dim: Geometric entity dimension: 0=vertex, 1=edge, 2=boundary, 3=domain.
        entities: List of entity IDs (for Explicit selection).
        filter_params: Coordinate bounds for Box/Ball/Cylinder filters.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    code_lines = [
        f'Selection sel = model.component("{comp_tag}").selection().create("{sel_tag}", "{sel_type}");',
        f'sel.set("entitydim", {entity_dim});',
    ]
    if entities and sel_type.lower() == "explicit":
        ent_str = ", ".join(str(e) for e in entities)
        code_lines.append(f'sel.set(new int[]{{{ent_str}}});')

    if filter_params:
        for k, v in filter_params.items():
            code_lines.append(f'sel.set("{k}", "{v}");')

    snippet = "\n        ".join(code_lines) + f'\n        System.out.println("SELECTION_CREATED: {sel_tag}");'
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "create_selection"
        res["selection_tag"] = sel_tag
        res["selection_type"] = sel_type
        res["entity_dim"] = entity_dim
    return res
