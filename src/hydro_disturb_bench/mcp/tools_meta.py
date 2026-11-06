"""Meta & Dynamic Execution Tools for COMSOL MCP."""

from pathlib import Path
from typing import Any, Dict, Optional

from hydro_disturb_bench.comsol_java import find_comsol_root, inspect_api
from .executor import execute_java_code, export_model_to_java


def comsol_inspect_environment() -> Dict[str, Any]:
    """Inspect local COMSOL 6.4 installation, verified toolchains, and available Java API methods.

    Always run this first to verify the COMSOL installation root and toolchain readiness.
    """
    try:
        api_info = inspect_api()
        root = find_comsol_root()
        java_bin = root / "java" / "win64" / "jre" / "bin"
        return {
            "status": "SUCCESS",
            "comsol_root": str(root),
            "comsol_version": api_info.get("comsol_version", "6.4"),
            "openjdk_path": str(java_bin / "java.exe"),
            "javac_path": str(java_bin / "javac.exe"),
            "entry_point": "com.comsol.model.util.ModelUtil",
            "capabilities": [
                "Full Java API coverage (com.comsol.model.*)",
                "Dynamic Java code compilation and in-memory execution",
                "Reverse engineering (.mph -> Java source export)",
                "From-scratch 1D/2D/3D parametric modeling",
                "Offscreen headless software rendering (-Dcs.3drend=sw)",
                "Copy-on-Write and SHA-256 baseline immutability defense",
            ],
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "code": "ENVIRONMENT_ERROR",
            "message": f"Failed to inspect COMSOL environment: {e}",
        }


def comsol_execute_java(
    java_code: str,
    source_model: Optional[str] = None,
    output_model: Optional[str] = None,
    is_graphics: bool = False,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Turing-complete universal COMSOL Java execution engine.

    Executes arbitrary COMSOL Java API code directly against a `model` object.
    If source_model is None or empty, starts from a blank Model.
    If output_model is provided, safely saves the result using Copy-on-Write.

    Use this tool whenever you need advanced, specialized, or custom COMSOL features
    (e.g., custom PDEs, ALE moving mesh, exotic boundary conditions, nonlinear solver settings).

    Example java_code:
        model.param().set("inlet_velocity", "2.5[m/s]");
        model.component("comp1").geom("geom1").create("c1", "Circle");
        model.component("comp1").geom("geom1").feature("c1").set("r", "0.05");
        model.component("comp1").geom("geom1").run();
    """
    return execute_java_code(
        java_snippet=java_code,
        source_model=source_model,
        output_model=output_model,
        is_graphics=is_graphics,
        timeout=timeout,
    )


def comsol_export_to_java(source_model: str, output_java_path: str) -> Dict[str, Any]:
    """Reverse-engineers any existing COMSOL model (.mph) into full native Java source code.

    This allows you to inspect exactly how any complex model or official COMSOL Application
    Library tutorial was constructed at the API level, enabling precise code reuse and replication.
    """
    return export_model_to_java(source_model=source_model, output_java_path=output_java_path)
