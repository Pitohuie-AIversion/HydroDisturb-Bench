"""MCP Resources and Prompt Templates for COMSOL Multiphysics."""

from pathlib import Path
from typing import Any, Dict

from .safety import format_error, ERR_MODEL_NOT_FOUND
from .tools_meta import comsol_inspect_environment
from .tools_model import comsol_inspect_model


def get_model_tree_resource(model_path: str) -> str:
    """Reads a COMSOL model tree as a structured JSON resource."""
    p = Path(model_path).resolve()
    if not p.is_file():
        return f'{{"error": "Model file not found: {p}"}}'
    info = comsol_inspect_model(str(p))
    import json
    return json.dumps(info, indent=2, ensure_ascii=False)


def get_environment_resource() -> str:
    """Reads local COMSOL 6.4 runtime environment status."""
    info = comsol_inspect_environment()
    import json
    return json.dumps(info, indent=2, ensure_ascii=False)


SOP_CFD_MODELING = """You are an expert multiphysics modeling agent using COMSOL Multiphysics.
Follow this strict 6-step Golden Loop to build or solve a CFD simulation:

1. Environment & Baseline Audit:
   - Call comsol_inspect_environment to verify COMSOL 6.4 is ready.
   - If starting from an existing file, call comsol_inspect_model to discover tags. Never guess tags.

2. Geometry & Selections (From-Scratch or Import):
   - Create 2D/3D primitives (comsol_geom_primitive) or import CAD.
   - Run Boolean operations (comsol_geom_boolean) and build geometry (comsol_geom_build).
   - Create named boundaries: Inlet, Outlet, Obstacle Walls, Outer Walls (comsol_create_selection).

3. Materials & Physics:
   - Add Water or Air material (comsol_add_material, comsol_set_material_properties).
   - Add Laminar Flow or Turbulent Flow (comsol_add_physics).
   - Add boundary conditions: Inlet velocity, Outlet pressure, No-slip walls (comsol_add_physics_feature).

4. Meshing & Quality Gate:
   - Generate mesh with appropriate refinement (comsol_mesh_build, comsol_mesh_feature).
   - Ensure minimum element quality > 0.1 before solving.

5. Solver Execution (Copy-on-Write):
   - Configure transient or stationary study (comsol_create_study, comsol_configure_solver).
   - Run solver asynchronously (comsol_solve_study) into runs/working_copy.mph. Never overwrite baseline.

6. Multimodal Visual Evaluation:
   - Create 2D velocity surface and streamline plots (comsol_create_plot_group).
   - Export high-resolution PNG image (comsol_export_image).
   - Visually inspect boundary layers, vortex shedding, and separation points.
"""
