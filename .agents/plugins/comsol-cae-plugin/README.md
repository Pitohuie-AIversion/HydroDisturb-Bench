# COMSOL CAE MCP Plugin

This package is a production-ready, dual-compatible agent plugin for **OpenAI Codex** and **Antigravity / Gemini / Claude Code**.

---

## What is Inside

- `plugin.json`: Plugin manifest meeting `agent-plugins.org` and Antigravity specifications.
- `mcp.json`: Tool definitions and process approval permissions for OpenAI Codex.
- `mcp_config.json`: Standard MCP server declaration for Antigravity, Cursor, and Claude Desktop.
- `rules/`: Engineering safety guardrails enforcing baseline immutability and Copy-on-Write.
- `skills/`:
  - `comsol-workflow`: The 5-step operational loop (Golden Loop) for COMSOL execution.
  - `software-mcp-builder`: Complete 12-section architecture reference standard for industrial MCP integration.

---

## Installation & Usage

### 1. In OpenAI Codex (CLI / Desktop)
Add the local plugin to your `~/.codex/config.toml`:

```toml
[plugins."comsol-cae-plugin@local"]
enabled = true
source_type = "local"
source = 'C:/path/to/comsol-cae-plugin'
```

### 2. In Antigravity
Place this directory inside your workspace's `.agents/plugins/` or globally in `~/.gemini/config/plugins/comsol-cae-plugin/`. It will be discovered and activated automatically.
