"""Production-Grade Universal COMSOL Multiphysics MCP Server.

Provides 100% full-capability API coverage, from-scratch 1D/2D/3D modeling,
dynamic Java execution, reverse-engineering, offscreen rendering, and safety guardrails.
"""

import sys
from typing import Any, Dict, List, Optional, Union

from mcp.server.mcpserver import MCPServer

from .resources import SOP_CFD_MODELING, get_environment_resource, get_model_tree_resource
from .tools_geometry import (
    comsol_create_selection,
    comsol_geom_boolean,
    comsol_geom_build,
    comsol_geom_primitive,
)
from .tools_materials import (
    comsol_add_material,
    comsol_set_material_properties,
)
from .tools_mesh import (
    comsol_mesh_build,
    comsol_mesh_feature,
)
from .tools_meta import (
    comsol_execute_java,
    comsol_export_to_java,
    comsol_inspect_environment,
)
from .tools_model import (
    comsol_create_model,
    comsol_evaluate_expressions,
    comsol_inspect_model,
    comsol_inspect_node,
)
from .tools_params import (
    comsol_add_function,
    comsol_set_parameters,
)
from .tools_physics import (
    comsol_add_multiphysics,
    comsol_add_physics,
    comsol_add_physics_feature,
)
from .tools_results import (
    comsol_create_plot_group,
    comsol_evaluate_numerical,
    comsol_export_image,
)
from .tools_study import (
    comsol_configure_solver,
    comsol_create_study,
    comsol_solve_study,
)

# Initialize MCP Server
server = MCPServer(
    name="comsol-multiphysics",
    instructions=(
        "Universal full-capability COMSOL Multiphysics MCP server. "
        "Provides 100% coverage of COMSOL simulation capabilities: from-scratch geometry modeling, "
        "materials, any physics (CFD, Solid Mechanics, Heat Transfer, Electromagnetics, Acoustics, PDEs), "
        "multiphysics couplings, meshing, solvers, offscreen rendering, and dynamic Java code execution."
    ),
)


# ============================================================================
# Group 1: Meta & Dynamic Java Execution (100% Capability Guarantee)
# ============================================================================

@server.tool()
def inspect_environment() -> Dict[str, Any]:
    """Inspect local COMSOL 6.4 installation, verified toolchains, and available Java API methods."""
    return comsol_inspect_environment()


@server.tool()
def execute_java(
    java_code: str,
    source_model: Optional[str] = None,
    output_model: Optional[str] = None,
    is_graphics: bool = False,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Turing-complete universal COMSOL Java execution engine.

    Executes arbitrary COMSOL Java API code directly against a `model` object inside a crash-isolated subprocess.
    If source_model is None or empty, starts with a blank Model.
    If output_model is provided, safely saves the result using Copy-on-Write.
    Use this for any specialized or custom feature not covered by standard tools.
    """
    return comsol_execute_java(
        java_code=java_code,
        source_model=source_model,
        output_model=output_model,
        is_graphics=is_graphics,
        timeout=timeout,
    )


@server.tool()
def export_to_java(source_model: str, output_java_path: str) -> Dict[str, Any]:
    """Reverse-engineers any existing COMSOL model (.mph) into full native Java source code.

    Enables inspecting how official COMSOL Application Libraries tutorials or existing models
    were built under the hood.
    """
    return comsol_export_to_java(source_model=source_model, output_java_path=output_java_path)


# ============================================================================
# Group 2: Model Lifecycle & Introspection
# ============================================================================

@server.tool()
def create_model(dim: int, name: str, output_model: str) -> Dict[str, Any]:
    """Create a brand new COMSOL model from scratch with 1D, 2D, or 3D geometry sequence."""
    return comsol_create_model(dim=dim, name=name, output_model=output_model)


@server.tool()
def inspect_model(model_path: str) -> Dict[str, Any]:
    """Read the complete hierarchy and structure of a COMSOL model (.mph) without modifying it.

    Always run this first on an existing model to discover active tags rather than guessing.
    """
    return comsol_inspect_model(model_path=model_path)


@server.tool()
def inspect_node(model_path: str, node_type: str, node_tag: str) -> Dict[str, Any]:
    """Inspect detailed properties and features of a specific node (e.g. physics, geom, study)."""
    return comsol_inspect_node(model_path=model_path, node_type=node_type, node_tag=node_tag)


@server.tool()
def evaluate_expressions(model_path: str, expressions: List[str]) -> Dict[str, Any]:
    """Evaluate mathematical or physical expressions within the model's global scope."""
    return comsol_evaluate_expressions(model_path=model_path, expressions=expressions)


# ============================================================================
# Group 3: Parameters & Mathematical Functions
# ============================================================================

@server.tool()
def set_parameters(
    source_model: str,
    output_model: str,
    parameters: Dict[str, Union[str, Dict[str, str]]],
) -> Dict[str, Any]:
    """Batch sets or updates global parameters in a COMSOL model using Copy-on-Write."""
    return comsol_set_parameters(
        source_model=source_model,
        output_model=output_model,
        parameters=parameters,
    )


@server.tool()
def add_function(
    source_model: str,
    output_model: str,
    func_type: str,
    func_name: str,
    expression: str,
    arguments: str = "t",
) -> Dict[str, Any]:
    """Adds an analytic, step, or ramp function to global definitions."""
    return comsol_add_function(
        source_model=source_model,
        output_model=output_model,
        func_type=func_type,
        func_name=func_name,
        expression=expression,
        arguments=arguments,
    )


# ============================================================================
# Group 4: Geometry Modeling & Selections
# ============================================================================

@server.tool()
def geom_primitive(
    source_model: str,
    output_model: str,
    shape: str,
    tag: str,
    params: Dict[str, Union[str, float, int, List[float]]],
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a 2D or 3D geometric primitive (Circle, Rectangle, Block, Cylinder, Sphere, Polygon)."""
    return comsol_geom_primitive(
        source_model=source_model,
        output_model=output_model,
        shape=shape,
        tag=tag,
        params=params,
        geom_tag=geom_tag,
        comp_tag=comp_tag,
    )


@server.tool()
def geom_boolean(
    source_model: str,
    output_model: str,
    op_type: str,
    tag: str,
    input_tags: List[str],
    subtract_tags: Optional[List[str]] = None,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Performs Boolean operations (Union, Difference, Intersection) on geometric objects."""
    return comsol_geom_boolean(
        source_model=source_model,
        output_model=output_model,
        op_type=op_type,
        tag=tag,
        input_tags=input_tags,
        subtract_tags=subtract_tags,
        geom_tag=geom_tag,
        comp_tag=comp_tag,
    )


@server.tool()
def geom_build(
    source_model: str,
    output_model: str,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Rebuilds the entire geometry sequence and returns topology entity counts."""
    return comsol_geom_build(
        source_model=source_model,
        output_model=output_model,
        geom_tag=geom_tag,
        comp_tag=comp_tag,
    )


@server.tool()
def create_selection(
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
    """Creates geometric entity selections (Explicit, Box, Ball, Cylinder)."""
    return comsol_create_selection(
        source_model=source_model,
        output_model=output_model,
        sel_type=sel_type,
        sel_tag=sel_tag,
        entity_dim=entity_dim,
        entities=entities,
        filter_params=filter_params,
        geom_tag=geom_tag,
        comp_tag=comp_tag,
    )


# ============================================================================
# Group 5: Materials & Physical Properties
# ============================================================================

@server.tool()
def add_material(
    source_model: str,
    output_model: str,
    mat_tag: str,
    material_name: str = "Common",
    selection: Optional[List[int]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a material to the model component and assigns it to geometric domains."""
    return comsol_add_material(
        source_model=source_model,
        output_model=output_model,
        mat_tag=mat_tag,
        material_name=material_name,
        selection=selection,
        comp_tag=comp_tag,
    )


@server.tool()
def set_material_properties(
    source_model: str,
    output_model: str,
    mat_tag: str,
    properties: Dict[str, str],
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Configures physical material properties (density, dynamicviscosity, thermalconductivity, etc.)."""
    return comsol_set_material_properties(
        source_model=source_model,
        output_model=output_model,
        mat_tag=mat_tag,
        properties=properties,
        comp_tag=comp_tag,
    )


# ============================================================================
# Group 6: All Physics & Multiphysics
# ============================================================================

@server.tool()
def add_physics(
    source_model: str,
    output_model: str,
    physics_type: str,
    physics_tag: str,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds ANY physics interface (CFD, Heat, Solid Mechanics, Electromagnetics, Acoustics, PDEs)."""
    return comsol_add_physics(
        source_model=source_model,
        output_model=output_model,
        physics_type=physics_type,
        physics_tag=physics_tag,
        geom_tag=geom_tag,
        comp_tag=comp_tag,
    )


@server.tool()
def add_physics_feature(
    source_model: str,
    output_model: str,
    physics_tag: str,
    feature_type: str,
    feature_tag: str,
    selection: Optional[List[int]] = None,
    properties: Optional[Dict[str, Union[str, List[str], float, int]]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a boundary condition (Inlet, Outlet, Wall, HeatFlux, Force) or domain feature."""
    return comsol_add_physics_feature(
        source_model=source_model,
        output_model=output_model,
        physics_tag=physics_tag,
        feature_type=feature_type,
        feature_tag=feature_tag,
        selection=selection,
        properties=properties,
        comp_tag=comp_tag,
    )


@server.tool()
def add_multiphysics(
    source_model: str,
    output_model: str,
    coupling_type: str,
    coupling_tag: str,
    coupled_physics: Optional[List[str]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Establishes multiphysics couplings (FSI, Non-Isothermal Flow, Joule Heating, etc.)."""
    return comsol_add_multiphysics(
        source_model=source_model,
        output_model=output_model,
        coupling_type=coupling_type,
        coupling_tag=coupling_tag,
        coupled_physics=coupled_physics,
        comp_tag=comp_tag,
    )


# ============================================================================
# Group 7: Mesh Engineering
# ============================================================================

@server.tool()
def mesh_build(
    source_model: str,
    output_model: str,
    mesh_tag: str = "mesh1",
    preset_size: Optional[str] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Generates mesh and queries mesh statistics (element counts, minimum quality)."""
    return comsol_mesh_build(
        source_model=source_model,
        output_model=output_model,
        mesh_tag=mesh_tag,
        preset_size=preset_size,
        comp_tag=comp_tag,
    )


@server.tool()
def mesh_feature(
    source_model: str,
    output_model: str,
    feature_type: str,
    feature_tag: str,
    mesh_tag: str = "mesh1",
    selection: Optional[List[int]] = None,
    properties: Optional[Dict[str, Union[str, float, int]]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds local mesh refinement or feature (FreeTri, FreeTet, Map, Swept, BoundaryLayers, Size)."""
    return comsol_mesh_feature(
        source_model=source_model,
        output_model=output_model,
        feature_type=feature_type,
        feature_tag=feature_tag,
        mesh_tag=mesh_tag,
        selection=selection,
        properties=properties,
        comp_tag=comp_tag,
    )


# ============================================================================
# Group 8: Study & Solvers
# ============================================================================

@server.tool()
def create_study(
    source_model: str,
    output_model: str,
    study_tag: str,
    study_type: str = "Stationary",
    physics_list: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Creates a Study sequence (Stationary, Transient, Frequency, Eigenfrequency)."""
    return comsol_create_study(
        source_model=source_model,
        output_model=output_model,
        study_tag=study_tag,
        study_type=study_type,
        physics_list=physics_list,
    )


@server.tool()
def configure_solver(
    source_model: str,
    output_model: str,
    study_tag: str = "std1",
    tlist: Optional[str] = None,
    rtol: Optional[str] = None,
    maxstep: Optional[str] = None,
) -> Dict[str, Any]:
    """Configures transient time stepping, relative tolerance, and solver bounds."""
    return comsol_configure_solver(
        source_model=source_model,
        output_model=output_model,
        study_tag=study_tag,
        tlist=tlist,
        rtol=rtol,
        maxstep=maxstep,
    )


@server.tool()
def solve_study(
    source_model: str,
    output_model: str,
    study_tag: str = "std1",
    tlist_override: Optional[str] = None,
    rtol_override: Optional[str] = None,
    maxstep_override: Optional[str] = None,
    timeout_sec: int = 1800,
) -> Dict[str, Any]:
    """Runs the COMSOL solver for the specified study under the license queue lock."""
    return comsol_solve_study(
        source_model=source_model,
        output_model=output_model,
        study_tag=study_tag,
        tlist_override=tlist_override,
        rtol_override=rtol_override,
        maxstep_override=maxstep_override,
        timeout_sec=timeout_sec,
    )


# ============================================================================
# Group 9: Results & Multimodal Vision
# ============================================================================

@server.tool()
def create_plot_group(
    source_model: str,
    output_model: str,
    plot_group_tag: str,
    plot_type: str = "PlotGroup2D",
    dset_tag: str = "dset1",
    plots: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Creates a 1D, 2D, or 3D Plot Group (Surface, Streamline, Contour, Slice, Arrow)."""
    return comsol_create_plot_group(
        source_model=source_model,
        output_model=output_model,
        plot_group_tag=plot_group_tag,
        plot_type=plot_type,
        dset_tag=dset_tag,
        plots=plots,
    )


@server.tool()
def export_image(
    model_path: str,
    output_image_path: str,
    plot_tag: str,
    width: int = 1200,
    height: int = 800,
) -> Dict[str, Any]:
    """Renders any 2D/3D Plot Group to a PNG image using the offscreen software renderer."""
    return comsol_export_image(
        model_path=model_path,
        output_image_path=output_image_path,
        plot_tag=plot_tag,
        width=width,
        height=height,
    )


@server.tool()
def evaluate_numerical(
    model_path: str,
    eval_type: str,
    expressions: List[str],
    selection: Optional[List[int]] = None,
    dataset_tag: str = "dset1",
) -> Dict[str, Any]:
    """Computes numerical derived values (integrals, point evaluations, averages, max/min)."""
    return comsol_evaluate_numerical(
        model_path=model_path,
        eval_type=eval_type,
        expressions=expressions,
        selection=selection,
        dataset_tag=dataset_tag,
    )


# ============================================================================
# Resources & Prompts
# ============================================================================

@server.resource("comsol://models/{path}")
def model_tree_resource(path: str) -> str:
    """Reads live model hierarchy as structured JSON."""
    return get_model_tree_resource(path)


@server.resource("comsol://env")
def environment_resource() -> str:
    """Reads local COMSOL 6.4 runtime environment."""
    return get_environment_resource()


@server.prompt("cfd-workflow-sop")
def cfd_workflow_prompt() -> str:
    """Golden Loop Standard Operating Procedure for building and solving CFD cases."""
    return SOP_CFD_MODELING


def main():
    """Main entry point for starting the COMSOL MCP Server via stdio."""
    # Never write application logs to stdout; stdout is reserved for JSON-RPC in stdio mode
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
