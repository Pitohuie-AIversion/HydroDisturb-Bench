"""Parameters, Global Variables, and Mathematical Function Tools."""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from .executor import execute_java_code
from .safety import format_error, validate_cow_paths, ERR_MODEL_NOT_FOUND


def comsol_set_parameters(
    source_model: str,
    output_model: str,
    parameters: Dict[str, Union[str, Dict[str, str]]],
) -> Dict[str, Any]:
    """Batch sets or updates global parameters in a COMSOL model.

    Enforces Copy-on-Write: source_model is strictly read-only and preserved.

    Args:
        source_model: Path to input .mph file.
        output_model: Path to target output .mph file.
        parameters: Dictionary mapping parameter names to expressions or detail dictionaries.
            Example:
            {
                "U_inlet": "1.5[m/s]",
                "cylinder_D": {"expr": "0.1[m]", "descr": "Cylinder diameter"},
                "nu": "1e-6[m^2/s]"
            }
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    p = Path(source_model).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Source model not found: {p}")

    setter_lines = []
    for name, spec in parameters.items():
        if isinstance(spec, dict):
            expr = spec.get("expr", "")
            descr = spec.get("descr", "")
            setter_lines.append(f'model.param().set("{name}", "{expr}", "{descr}");')
        else:
            setter_lines.append(f'model.param().set("{name}", "{spec}");')

    snippet = "\n        ".join(setter_lines) + '\n        System.out.println("PARAMETERS_UPDATED");'
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "set_parameters"
        res["updated_count"] = len(parameters)
        res["parameters"] = list(parameters.keys())
    return res


def comsol_add_function(
    source_model: str,
    output_model: str,
    func_type: str,
    func_name: str,
    expression: str,
    arguments: str = "t",
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Adds an analytic, step, or ramp function to the model's global definitions.

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        func_type: Function type: "Analytic", "Step", "Ramp", "Piecewise".
        func_name: Tag/Identifier for the function (e.g. "an1", "step1").
        expression: Mathematical expression (e.g. "sin(2*pi*f0*t)", "0.5*(1+tanh(t/0.1))").
        arguments: Comma-separated list of function arguments (default "t").
        options: Optional settings such as periodic boundary or smoothing.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    snippet = f"""
        FunctionFeature f = model.func().create("{func_name}", "{func_type}");
        f.set("funcname", "{func_name}");
        f.set("expr", "{expression}");
        f.set("args", new String[]{{"{arguments}"}});
        System.out.println("FUNCTION_CREATED: {func_name} ({func_type})");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "add_function"
        res["function_name"] = func_name
        res["function_type"] = func_type
    return res
