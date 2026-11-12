"""Model Lifecycle, Tree Introspection, and Expression Evaluation Tools."""

import json
from pathlib import Path
from typing import Any

from hydro_disturb_bench.comsol_java import inspect_model as _legacy_inspect_model

from .executor import create_blank_model, execute_java_code
from .safety import ERR_MODEL_NOT_FOUND, format_error


def comsol_create_model(dim: int, name: str, output_model: str) -> dict[str, Any]:
    """Create a brand new COMSOL Multiphysics model from scratch with 1D, 2D, or 3D geometry sequence.

    Args:
        dim: Geometric space dimension (1 for 1D, 2 for 2D, 3 for 3D).
        name: Model title/label.
        output_model: Target file path to save the new .mph model.
    """
    if dim not in (1, 2, 3):
        return format_error("INVALID_DIMENSION", "Dimension must be 1, 2, or 3.")
    return create_blank_model(dim=dim, name=name, output_model=output_model)


def comsol_inspect_model(model_path: str) -> dict[str, Any]:
    """Read the complete hierarchy and structure of a COMSOL model (.mph) without modifying it.

    Returns:
        - Components, Geometries, Mesh tags
        - Active Physics interfaces and features
        - Global and local Parameters with expressions and evaluated values
        - Studies, Solvers, Datasets, and Plot Groups
    Always invoke this before mutating a model to discover active tags instead of guessing.
    """
    p = Path(model_path).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Model file not found: {p}")
    try:
        return _legacy_inspect_model(p)
    except Exception as e:
        return format_error("INSPECTION_FAILED", f"Failed to inspect model {p}: {e}")


def comsol_inspect_node(model_path: str, node_type: str, node_tag: str) -> dict[str, Any]:
    """Inspect detailed properties, enabled options, and allowed values of a specific node in the model.

    Args:
        model_path: Path to .mph file.
        node_type: Node domain, e.g. "param", "geom", "physics", "mesh", "study", "sol", "result".
        node_tag: Specific node tag or path, e.g. "comp1/geom1", "comp1/spf", "std1".
    """
    p = Path(model_path).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Model file not found: {p}")

    snippet = f"""
        StringBuilder sb = new StringBuilder();
        sb.append("{{\\"node_type\\":\\"{node_type}\\",\\"node_tag\\":\\"{node_tag}\\",\\"properties\\":{{");

        if ("physics".equalsIgnoreCase("{node_type}")) {{
            // Inspect physics features
            String[] parts = "{node_tag}".split("/");
            String compTag = parts.length > 1 ? parts[0] : "comp1";
            String physTag = parts.length > 1 ? parts[1] : parts[0];
            Physics phys = model.component(compTag).physics(physTag);
            sb.append("\\"type\\":\\"").append(phys.getType()).append("\\",");
            sb.append("\\"tag\\":\\"").append(phys.tag()).append("\\",");
            sb.append("\\"features\\":[");
            String[] ftags = phys.feature().tags();
            for (int i = 0; i < ftags.length; i++) {{
                if (i > 0) sb.append(",");
                PhysicsFeature f = phys.feature(ftags[i]);
                sb.append("{{\\"tag\\":\\"").append(ftags[i]).append("\\",\\"type\\":\\"").append(f.getType()).append("\\"}}");
            }}
            sb.append("]");
        }} else if ("geom".equalsIgnoreCase("{node_type}")) {{
            String[] parts = "{node_tag}".split("/");
            String compTag = parts.length > 1 ? parts[0] : "comp1";
            String geomTag = parts.length > 1 ? parts[1] : parts[0];
            GeomSequence g = model.component(compTag).geom(geomTag);
            sb.append("\\"dimension\\":").append(g.dimension()).append(",");
            sb.append("\\"features\\":[");
            String[] ftags = g.feature().tags();
            for (int i = 0; i < ftags.length; i++) {{
                if (i > 0) sb.append(",");
                sb.append("{{\\"tag\\":\\"").append(ftags[i]).append("\\",\\"type\\":\\"").append(g.feature(ftags[i]).getType()).append("\\"}}");
            }}
            sb.append("]");
        }} else {{
            sb.append("\\"info\\":\\"General node inspection ready\\"");
        }}
        sb.append("}}");
        System.out.println("NODE_INSPECT_RESULT=" + sb.toString());
    """
    res = execute_java_code(java_snippet=snippet, source_model=p, output_model=None)
    if res.get("status") == "SUCCESS":
        out = res.get("console_output", "")
        for line in out.splitlines():
            if line.startswith("NODE_INSPECT_RESULT="):
                try:
                    return {
                        "status": "SUCCESS",
                        "operation": "inspect_node",
                        "model_path": str(p),
                        "node_info": json.loads(line.replace("NODE_INSPECT_RESULT=", "")),
                    }
                except Exception:
                    pass
    return res


def comsol_evaluate_expressions(model_path: str, expressions: list[str]) -> dict[str, Any]:
    """Evaluate mathematical or physical expressions within the model's global scope.

    Supports checking unit conversions, parameter formulas, or algebraic combinations.
    """
    p = Path(model_path).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Model file not found: {p}")

    expr_array_str = ", ".join(f'"{e}"' for e in expressions)
    snippet = f"""
        String[] exprs = new String[]{{{expr_array_str}}};
        StringBuilder sb = new StringBuilder();
        sb.append("{{\\"evaluations\\":{{");
        for (int i = 0; i < exprs.length; i++) {{
            if (i > 0) sb.append(",");
            String expr = exprs[i];
            try {{
                double val = model.param().evaluate(expr);
                String unit = model.param().evaluateUnit(expr);
                sb.append("\\"").append(expr).append("\\":{{\\"value\\":").append(val).append(",\\"unit\\":\\"").append(unit).append("\\"}}");
            }} catch (Throwable t) {{
                String msg = (t.getMessage() != null) ? t.getMessage().replace('"', ' ') : "error";
                sb.append("\\"").append(expr).append("\\":{{\\"error\\":\\"").append(msg).append("\\"}}");
            }}
        }}
        sb.append("}}}}");
        System.out.println("EXPR_EVAL_RESULT=" + sb.toString());
    """
    res = execute_java_code(java_snippet=snippet, source_model=p, output_model=None)
    if res.get("status") == "SUCCESS":
        out = res.get("console_output", "")
        for line in out.splitlines():
            if line.startswith("EXPR_EVAL_RESULT="):
                try:
                    return {
                        "status": "SUCCESS",
                        "operation": "evaluate_expressions",
                        "results": json.loads(line.replace("EXPR_EVAL_RESULT=", "")).get("evaluations", {}),
                    }
                except Exception:
                    pass
    return res
