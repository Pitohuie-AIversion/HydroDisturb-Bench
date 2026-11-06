"""Material Library and Constitutive Property Tools for COMSOL MCP."""

from typing import Any, Dict, List, Optional, Union

from .executor import execute_java_code
from .safety import validate_cow_paths


def comsol_add_material(
    source_model: str,
    output_model: str,
    mat_tag: str,
    material_name: str = "Common",
    selection: Optional[List[int]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a material to the model component and assigns it to geometric domains.

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        mat_tag: Material tag (e.g. "mat1", "mat_water").
        material_name: Label/name of the material (e.g. "Water", "Air", "Structural Steel").
        selection: Domain entity IDs to which this material is assigned (e.g. [1]).
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'Material mat = model.component("{comp_tag}").material().create("{mat_tag}", "Common");',
        f'mat.label("{material_name}");',
    ]
    if selection:
        sel_str = ", ".join(str(s) for s in selection)
        lines.append(f'mat.selection().set(new int[]{{{sel_str}}});')

    lines.append(f'System.out.println("MATERIAL_CREATED: {mat_tag} ({material_name})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "add_material"
        res["material_tag"] = mat_tag
        res["material_name"] = material_name
    return res


def comsol_set_material_properties(
    source_model: str,
    output_model: str,
    mat_tag: str,
    properties: Dict[str, str],
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Configures physical material properties in the basic property group.

    Common property keys:
        - "density" (rho): "1000[kg/m^3]"
        - "dynamicviscosity" (mu): "1e-3[Pa*s]"
        - "thermalconductivity" (k): "0.6[W/(m*K)]"
        - "heatcapacity" (Cp): "4182[J/(kg*K)]"
        - "youngsmodulus" (E): "200e9[Pa]"
        - "poissonsratio" (nu): "0.33"
        - "relativepermittivity" (epsilon_r): "1.0"
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'Material mat = model.component("{comp_tag}").material("{mat_tag}");',
        'MaterialModel def = mat.materialModel("def");',
    ]
    for prop_name, prop_val in properties.items():
        lines.append(f'def.set("{prop_name}", "{prop_val}");')

    lines.append(f'System.out.println("MATERIAL_PROPERTIES_UPDATED: {mat_tag}");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "set_material_properties"
        res["material_tag"] = mat_tag
        res["properties"] = properties
    return res
