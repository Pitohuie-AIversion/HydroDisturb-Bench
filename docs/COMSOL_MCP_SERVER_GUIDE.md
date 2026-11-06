# COMSOL Multiphysics Full-Capability MCP Server (`comsol-multiphysics`)

This Model Context Protocol (MCP) server provides complete, unconstrained, and industrial-grade automation of **COMSOL Multiphysics 6.4** for LLM agents (Claude Desktop, Antigravity IDE, Cursor, Gemini, GPT-4o).

---

## 1. Core Architectural Highlights

1. **100% Full-Capability API Coverage (Turing Complete)**:
   - Includes `execute_java`: direct dynamic execution of native COMSOL Java API statements against live `model` objects inside crash-isolated subprocesses.
   - Any physical interface, moving mesh ALE, custom weak-form PDE, or specialized solver setting can be manipulated without waiting for pre-packaged wrappers.
2. **Reverse Engineering (`export_to_java`)**:
   - One-click export of any `.mph` file into full native Java source code, enabling LLMs to learn and replicate models directly from COMSOL Application Libraries.
3. **From-Scratch 1D/2D/3D Modeling**:
   - Supports creating models from a blank canvas, adding geometric primitives (2D/3D), performing Boolean operations, and defining custom selections.
4. **Multimodal Visual Feedback**:
   - Integrated offscreen software renderer (`-Dcs.3drend=sw`) produces high-resolution 2D/3D surface plots, streamlines, and stress distributions (PNG) for visual inspection by vision-enabled models.
5. **Industrial Safety Guardrails**:
   - **Baseline Immutability**: Source models are verified with SHA-256 before and after operations.
   - **Copy-on-Write (CoW)**: Mutations are strictly executed on isolated working copies.
   - **Concurrency Governance**: Integrated `FileLock` prevents commercial floating license token contention.

---

## 2. Client Setup Guide

### 2.1 Claude Desktop Configuration
Add the following to your `claude_desktop_config.json` (located at `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "comsol": {
      "command": "python",
      "args": [
        "-m",
        "hydro_disturb_bench.mcp"
      ],
      "cwd": "d:\\📁 01_Workspaces (代码与工程区)\\HydroDisturb-Bench"
    }
  }
}
```

### 2.2 Antigravity IDE / Cursor Configuration
In your project or workspace settings:

```json
{
  "mcpServers": {
    "comsol-multiphysics": {
      "command": "comsol-mcp",
      "args": []
    }
  }
}
```

---

## 3. Tool Reference (26 Full Tools)

### Meta & Turing-Complete Engine
- `inspect_environment`: Audits local COMSOL 6.4 installation, JRE, and toolchains.
- `execute_java`: Turing-complete executor for arbitrary COMSOL Java API code.
- `export_to_java`: Reverse-engineers any `.mph` model to Java source code.

### Model Lifecycle & Inspection
- `create_model`: Creates a blank 1D, 2D, or 3D model.
- `inspect_model`: Deep tree introspection (parameters, physics, mesh, study, solvers).
- `inspect_node`: Detailed property inspection of specific nodes.
- `evaluate_expressions`: Global expression evaluator with unit handling.

### Parameters & Functions
- `set_parameters`: Batch parameter mutation with units and descriptions.
- `add_function`: Adds Analytic, Step, Ramp, or Interpolation functions.

### Geometry & Selections
- `geom_primitive`: Adds Circle, Rectangle, Block, Cylinder, Sphere, Polygon.
- `geom_boolean`: Union, Difference, Intersection on geometric objects.
- `geom_build`: Rebuilds geometry sequence and queries topology counts.
- `create_selection`: Explicit, Box, Ball, Cylinder geometric selections.

### Materials
- `add_material`: Built-in library or custom material assignment.
- `set_material_properties`: Defines density, viscosity, thermal conductivity, Young's modulus, etc.

### Physics & Multiphysics
- `add_physics`: Adds any physics (CFD, Heat, Solid, EM, Acoustics, Chemical, PDE).
- `add_physics_feature`: Adds boundary conditions (Inlet, Outlet, Wall, HeatFlux, Force, Voltage) or sources.
- `add_multiphysics`: Fluid-Structure Interaction (FSI), Non-isothermal flow, Joule heating.

### Meshing
- `mesh_build`: Generates mesh and queries element count & quality statistics.
- `mesh_feature`: Adds FreeTri, FreeTet, Quad, Swept, BoundaryLayers, Size.

### Studies & Solvers
- `create_study`: Stationary, Transient, Frequency, Eigenfrequency, Parametric sweep.
- `configure_solver`: Configures time stepping, tolerance, and solver bounds.
- `solve_study`: Async solver execution under license lock.

### Results & Multimodal Vision
- `create_plot_group`: Surface, Streamline, Contour, Slice, Arrow plots.
- `export_image`: Offscreen high-resolution PNG rendering.
- `evaluate_numerical`: Point, Line, Surface, Volume integrals/max/min.
