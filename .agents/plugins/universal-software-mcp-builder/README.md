# Universal Software MCP Builder Plugin

**A production-grade, dual-compatible Agent Plugin for OpenAI Codex and Antigravity to scaffold, build, audit, and deploy Model Context Protocol (MCP) servers for any heavy desktop, scientific, and industrial software.**

---

## 🌟 Positioning & Value

Engineering and desktop software (like COMSOL, ANSYS Fluent/Mechanical, Abaqus, MATLAB, SolidWorks, Star-CCM+, AutoCAD) cannot be easily wrapped in standard web APIs. They involve:
- Floating commercial license contention
- Native dynamic link libraries (`.dll` / `.so`) and headless display crashes (`Xvfb`)
- High risk of OOM, segmentation faults, and singular matrices
- GB-scale 3D unstructured mesh results that choke LLM context windows

**This plugin acts as a specialized Meta-Developer Agent**, teaching AI assistants (Codex, Antigravity, Claude Code) how to systematically engineer production-grade MCP servers for **any target software** from zero to HPC deployment.

---

## 📦 What is Inside the Plugin

```text
universal-software-mcp-builder/
├── plugin.json                 # Standard manifest (Agent-Plugins.org + OpenAI Codex + Antigravity)
├── mcp.json                    # Codex stdio process approvals & execution timeout rules
├── mcp_config.json             # Standard Antigravity / Cursor MCP declarations
├── .codex-plugin/plugin.json   # Codex local discovery alias
├── rules/
│   └── universal_mcp_rules.md  # 6 non-negotiable engineering safety guardrails
├── skills/
│   ├── software-mcp-builder/   # The Universal 12-Section Architecture Specification
│   │   └── SKILL.md
│   └── case-study-comsol/      # Production Case Study: Direct Java API & JNA Bridge
│       └── SKILL.md
└── README.md
```

---

## 🚀 Quick Start / Installation

### 1. In OpenAI Codex (CLI & Desktop)
Add the plugin directory to your `~/.codex/config.toml`:

```toml
[plugins."universal-software-mcp-builder@local"]
enabled = true
source_type = "local"
source = 'C:/path/to/universal-software-mcp-builder'
```

### 2. In Antigravity / Agentic IDE
Place this folder under your workspace's `.agents/plugins/universal-software-mcp-builder/` or globally in `~/.gemini/config/plugins/`. It will be automatically discovered and activated.

---

## 🛠️ The 7 Core Architectural Pillars Included

1. **Host Environment & Display Subsystem**: Dynamic native DLL injection, headless software rasterizers, and Xvfb setup.
2. **Crash & Lifecycle Isolation**: Decoupled subprocess architecture with watchdog timeouts.
3. **Base64 Zero-Escape Boundary**: Safe handling of mathematical brackets, units, and paths.
4. **Baseline Immutability & Copy-on-Write**: SHA-256 pre/post hashing to guarantee baseline safety.
5. **Introspection-First Principle**: Dynamic model tree reflection to eliminate hallucinations.
6. **Async Tasks & Progress Streaming**: Long-running solver execution via FastMCP background tasks.
7. **Heavy Data Downsampling Contract**: Extraction of lightweight scalar KPIs and 2D cross-sections.
