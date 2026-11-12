# Universal Software MCP Builder Plugin

**A production-grade, dual-compatible Agent Plugin for OpenAI Codex and Antigravity to scaffold, build, audit, and deploy Model Context Protocol (MCP) servers for ANY heavy desktop, scientific, CAD/EDA, and industrial software.**

---

## 🌟 The Universal Software Guarantee: Works for ANY Software

Whether your target software is:
- **CAE / CFD / FEA**: COMSOL, ANSYS Fluent/Mechanical, Abaqus, Star-CCM+, OpenFOAM, LS-DYNA, Nastran
- **CAD / 3D Modeling**: SolidWorks, AutoCAD, Rhino/Grasshopper, CATIA, FreeCAD, Blender
- **EDA / Electronics**: Altium Designer, Cadence Allegro/Virtuoso, KiCad
- **Math & Scientific**: MATLAB/Simulink, Mathematica, OriginLab, LabVIEW
- **Specialized / In-House Tools**: Custom C++/Fortran solvers, internal legacy CLI binaries

Every software in the world connects to an AI Agent via one of **4 Universal Interface Paradigms**:

| Paradigm | Characteristic Software | Native Bridge Mechanism |
| :--- | :--- | :--- |
| **1. CLI_SCRIPT** | COMSOL, Star-CCM+, FreeCAD, Blender, OpenFOAM | Subprocess headless batch runner + Base64 zero-escape IPC |
| **2. SDK_RPC** | ANSYS (PyMAPDL/PyFluent), MATLAB, SolidWorks (COM) | Isolated Python worker process + gRPC / COM / Named Pipes |
| **3. FILE_CARD** | Abaqus (.inp), NASTRAN, LS-DYNA, SPICE (.cir) | Input card parser + Solver exit code loop + CoW Sandbox |
| **4. LEGACY_IPC** | Abaqus (Python 2.7), AutoCAD (AutoLISP) | Decoupled IPC channel between modern Python 3.10+ and legacy runner |

---

## ⚡ Instant Scaffolding in 1 Command

Use the built-in Scaffolder CLI to generate a turnkey, production-ready MCP server project for any software:

```bash
# Interactive Mode (Prompts for software name and paradigm):
python scripts/scaffolder.py

# Declarative Mode (Zero-prompt):
python scripts/scaffolder.py --name ansys-fluent-mcp --software "ANSYS Fluent" --paradigm sdk
python scripts/scaffolder.py --name abaqus-fea-mcp --software "Abaqus Standard" --paradigm card
python scripts/scaffolder.py --name solidworks-cad-mcp --software "SolidWorks" --paradigm sdk
python scripts/scaffolder.py --name blender-render-mcp --software "Blender" --paradigm cli
```

Every generated project includes:
1. `src/server.py`: Complete FastMCP server with `inspect_environment`, `inspect_model`, `update_parameter`, and `solve_job`.
2. `src/safety/immutability.py`: SHA-256 pre/post hashing asserting source model protection.
3. `src/safety/license_lock.py`: Floating license queue management via `FileLock`.
4. `src/diagnostics/downsampler.py`: Heavy simulation data downsampler (scalars + 2D slices).
5. `src/bridge/launcher.py`: Subprocess Base64 bridge launcher tailored to the paradigm.
6. `tests/test_safety.py`: Unit and contract test suite.
7. `docker/Dockerfile`: Headless container configuration with `Xvfb` and Mesa software rasterizer.
8. `plugin.json` & `mcp.json`: Dual-compatible OpenAI Codex & Antigravity manifest.

---

## 📦 Directory Structure

```text
universal-software-mcp-builder/
├── plugin.json                 # Standard manifest (Agent-Plugins.org + OpenAI Codex + Antigravity)
├── mcp.json                    # Codex stdio process approvals & timeout rules
├── mcp_config.json             # Standard Antigravity / Cursor MCP declarations
├── .codex-plugin/plugin.json   # Codex local discovery alias
├── scripts/
│   └── scaffolder.py           # The Universal Scaffolding CLI Engine
├── rules/
│   └── universal_mcp_rules.md  # 6 non-negotiable engineering safety guardrails
├── skills/
│   ├── software-mcp-builder/   # The Universal 13-Section Architecture Specification
│   │   └── SKILL.md
│   └── case-study-comsol/      # Production Case Study: Direct Java API & JNA Bridge
│       └── SKILL.md
├── examples/                   # Ready-to-run scaffolded projects for reference
│   ├── ansys-fluent-mcp/       # SDK/RPC Paradigm (CFD)
│   ├── abaqus-standard-mcp/    # Card/File Paradigm (FEA)
│   └── solidworks-cad-mcp/     # COM/ActiveX Paradigm (CAD)
└── README.md
```

---

## 🚀 Installation & Usage

### 1. In OpenAI Codex (CLI & Desktop)
Add the plugin directory to your `~/.codex/config.toml`:

```toml
[plugins."universal-software-mcp-builder@local"]
enabled = true
source_type = "local"
source = 'C:/Users/Administrator/.gemini/config/plugins/universal-software-mcp-builder'
```

### 2. In Antigravity / Agentic IDE
Already permanently installed in your global `~/.gemini/config/plugins/` and `~/.gemini/config/skills/`. It automatically activates across all workspaces and Git branches.
