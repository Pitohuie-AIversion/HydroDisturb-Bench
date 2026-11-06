"""Post-processing, Numerical Evaluations, Offscreen Rendering, and Data Export Tools."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .executor import execute_java_code
from .safety import format_error, validate_cow_paths, ERR_MODEL_NOT_FOUND


def comsol_create_plot_group(
    source_model: str,
    output_model: str,
    plot_group_tag: str,
    plot_type: str = "PlotGroup2D",
    dset_tag: str = "dset1",
    plots: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Creates a 1D, 2D, or 3D Plot Group in COMSOL Results.

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        plot_group_tag: Identifier for the plot group (e.g. "pg_velocity", "pg_pressure").
        plot_type: "PlotGroup1D", "PlotGroup2D", "PlotGroup3D".
        dset_tag: Solution dataset tag (default "dset1").
        plots: List of sub-plot feature definitions, e.g.:
               [{"type": "Surface", "tag": "surf1", "expr": "spf.U"},
                {"type": "Streamline", "tag": "str1", "expr": ["u", "v"]}]
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'ResultFeature pg = model.result().create("{plot_group_tag}", "{plot_type}");',
        f'pg.set("data", "{dset_tag}");',
    ]
    if plots:
        for p in plots:
            p_type = p.get("type", "Surface")
            p_tag = p.get("tag", f"feat_{p_type.lower()}")
            lines.append(f'ResultFeature sub = pg.create("{p_tag}", "{p_type}");')
            if "expr" in p:
                expr = p["expr"]
                if isinstance(expr, list):
                    arr_str = ", ".join(f'"{x}"' for x in expr)
                    lines.append(f'sub.set("expr", new String[]{{{arr_str}}});')
                else:
                    lines.append(f'sub.set("expr", "{expr}");')

    lines.append(f'System.out.println("PLOT_GROUP_CREATED: {plot_group_tag} ({plot_type})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "create_plot_group"
        res["plot_group_tag"] = plot_group_tag
        res["plot_type"] = plot_type
    return res


def comsol_export_image(
    model_path: str,
    output_image_path: str,
    plot_tag: str,
    width: int = 1200,
    height: int = 800,
) -> Dict[str, Any]:
    """Renders any 2D/3D Plot Group to a high-resolution PNG image using the offscreen software renderer.

    Enables multimodal LLM agents to visually inspect flow structures, vortex shedding,
    temperature gradients, or stress concentrations.
    """
    p = Path(model_path).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Model file not found: {p}")

    out_img = Path(output_image_path).resolve()
    out_img.parent.mkdir(parents=True, exist_ok=True)

    snippet = f"""
        ExportFeature img = model.result().export().create("img_export", "Image");
        img.set("plotgroup", "{plot_tag}");
        img.set("size", "manual");
        img.set("unit", "px");
        img.set("width", {width});
        img.set("height", {height});
        img.set("pngimagetype", "png");
        img.set("filename", "{out_img.as_posix()}");
        img.run();
        model.result().export().remove("img_export");
        System.out.println("IMAGE_EXPORT_SUCCESS: {out_img.as_posix()}");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=p,
        output_model=None,
        is_graphics=True,
    )
    if res.get("status") == "SUCCESS" and out_img.is_file():
        return {
            "status": "SUCCESS",
            "operation": "export_image",
            "plot_tag": plot_tag,
            "image_path": str(out_img),
            "image_size_bytes": out_img.stat().st_size,
            "dimensions": {"width": width, "height": height},
        }
    return res


def comsol_evaluate_numerical(
    model_path: str,
    eval_type: str,
    expressions: List[str],
    selection: Optional[List[int]] = None,
    dataset_tag: str = "dset1",
) -> Dict[str, Any]:
    """Computes numerical derived values (integrals, point evaluations, averages, max/min).

    Args:
        model_path: Path to solved .mph model.
        eval_type: "IntSurface", "IntVolume", "IntLine", "EvalPoint", "EvalGlobal", "MaxSurface", "MinSurface".
        expressions: List of physical expressions to evaluate (e.g. ["spf.U", "p", "spf.T_stressy"]).
        selection: Entity IDs for integration/evaluation.
        dataset_tag: Solution dataset tag (default "dset1").
    """
    p = Path(model_path).resolve()
    if not p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Model file not found: {p}")

    expr_arr = ", ".join(f'"{e}"' for e in expressions)
    sel_code = ""
    if selection:
        sel_arr = ", ".join(str(s) for s in selection)
        sel_code = f'num.selection().set(new int[]{{{sel_arr}}});'

    snippet = f"""
        NumericalFeature num = model.result().numerical().create("eval1", "{eval_type}");
        num.set("data", "{dataset_tag}");
        num.set("expr", new String[]{{{expr_arr}}});
        {sel_code}
        double[][] resMatrix = num.getReal();
        StringBuilder sb = new StringBuilder();
        sb.append("NUMERICAL_RESULT={{\\"expressions\\":[");
        for (int i = 0; i < {len(expressions)}; i++) {{
            if (i > 0) sb.append(",");
            sb.append("\\"{expressions[0] if i == 0 else (expressions[i] if i < len(expressions) else "")}\\"");
        }}
        sb.append("],\\"values\\":[");
        for (int i = 0; i < resMatrix.length; i++) {{
            if (i > 0) sb.append(",");
            sb.append("[");
            for (int j = 0; j < resMatrix[i].length; j++) {{
                if (j > 0) sb.append(",");
                sb.append(resMatrix[i][j]);
            }}
            sb.append("]");
        }}
        sb.append("]}}");
        model.result().numerical().remove("eval1");
        System.out.println(sb.toString());
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=p,
        output_model=None,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "evaluate_numerical"
        res["eval_type"] = eval_type
        for line in res.get("console_output", "").splitlines():
            if line.startswith("NUMERICAL_RESULT="):
                try:
                    res["data"] = json.loads(line.replace("NUMERICAL_RESULT=", ""))
                except Exception:
                    pass
    return res
