"""Mesh Generation, Element Controls, and Mesh Quality Tools."""

import json
from typing import Any

from .executor import execute_java_code
from .safety import validate_cow_paths


def comsol_mesh_build(
    source_model: str,
    output_model: str,
    mesh_tag: str = "mesh1",
    preset_size: str | None = None,
    comp_tag: str = "comp1",
) -> dict[str, Any]:
    """Generates the mesh and queries mesh statistics (element counts, minimum quality, average quality).

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        mesh_tag: Mesh sequence tag (default "mesh1").
        preset_size: COMSOL predefined size preset:
                     "extremely_fine", "extra_fine", "finer", "fine", "normal", "coarse", "coarser", "extremely_coarse".
        comp_tag: Component tag (default "comp1").
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'MeshSequence mesh = model.component("{comp_tag}").mesh("{mesh_tag}");',
    ]
    if preset_size:
        # Predefined sizes map to 1..9 in COMSOL
        preset_map = {
            "extremely_fine": "1",
            "extra_fine": "2",
            "finer": "3",
            "fine": "4",
            "normal": "5",
            "coarse": "6",
            "coarser": "7",
            "extra_coarse": "8",
            "extremely_coarse": "9",
        }
        val = preset_map.get(preset_size.lower().replace("-", "_"), "5")
        lines.append('if (mesh.feature().tags().length == 0) mesh.autoMeshSize(' + val + ');')

    lines.append('mesh.run();')
    lines.append('int numElem = mesh.getNumElem();')
    lines.append('int numVtx = mesh.getNumVertex();')
    lines.append('double minQual = 0.0;')
    lines.append('try { minQual = mesh.getMinElemQuality(); } catch (Throwable t) {}')
    lines.append('System.out.println("MESH_STATS={{\\"element_count\\":" + numElem + ",\\"vertex_count\\":" + numVtx + ",\\"min_quality\\":" + minQual + "}}");')

    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "mesh_build"
        res["mesh_tag"] = mesh_tag
        for line in res.get("console_output", "").splitlines():
            if line.startswith("MESH_STATS="):
                try:
                    res["mesh_statistics"] = json.loads(line.replace("MESH_STATS=", ""))
                except Exception:
                    pass
    return res


def comsol_mesh_feature(
    source_model: str,
    output_model: str,
    feature_type: str,
    feature_tag: str,
    mesh_tag: str = "mesh1",
    selection: list[int] | None = None,
    properties: dict[str, str | float | int] | None = None,
    comp_tag: str = "comp1",
) -> dict[str, Any]:
    """Adds a mesh feature or local refinement (FreeTri, FreeTet, Map, Swept, BoundaryLayers, Size).

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        feature_type: "FreeTri", "FreeTet", "Map", "Swept", "BndLayer", "Size".
        feature_tag: Identifier tag for this mesh feature (e.g. "ftri1", "size1").
        selection: Entity IDs where this feature applies.
        properties: Detailed mesh properties (e.g. "hmax", "hmin", "hgrad").
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'MeshSequence mesh = model.component("{comp_tag}").mesh("{mesh_tag}");',
        f'MeshFeature f = mesh.create("{feature_tag}", "{feature_type}");',
    ]
    if selection:
        sel_str = ", ".join(str(s) for s in selection)
        lines.append(f'f.selection().set(new int[]{{{sel_str}}});')

    if properties:
        for k, v in properties.items():
            lines.append(f'f.set("{k}", "{v}");')

    lines.append(f'System.out.println("MESH_FEATURE_ADDED: {feature_tag} ({feature_type})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "mesh_feature"
        res["feature_tag"] = feature_tag
        res["feature_type"] = feature_type
    return res
