# solidworks-cad-mcp

Production-Grade Model Context Protocol (MCP) Server for **SolidWorks CAD**.
Built using the Universal Software MCP Builder.

## Universal Paradigm: SDK
- **Crash Isolation**: Native SolidWorks CAD execution decoupled in isolated subprocess.
- **Base64 Boundary**: No CLI escaping or injection vulnerabilities.
- **Copy-on-Write**: Baseline models are strictly read-only; pre/post SHA-256 hashed.
- **License Queue**: Commercial license locks serialized via cross-process FileLock.
- **Downsampled Diagnostics**: Heavy data kept on disk; scalar KPIs & 2D slices returned.

## Quick Start
```bash
pip install -e .
python -m src.server
```
