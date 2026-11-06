---
name: software-mcp-builder
description: Universal engineering workflow and scaffolding for building production-grade Model Context Protocol (MCP) servers for complex desktop, scientific, and industrial software (CAE/CAD/EDA/simulation tools like COMSOL, ANSYS, ABAQUS, MATLAB, etc.).
---

# Universal Software MCP Builder Skill (Production Standard)

This skill codifies the architectural principles, safety contracts, and engineering scaffolding required to build bulletproof **Model Context Protocol (MCP)** servers for complex desktop, scientific, and industrial engineering software.

Derived from real-world production experience in `HydroDisturb-Bench` (COMSOL Java API / JNA / Subprocess Bridge) and aligned with the latest **Model Context Protocol (MCP) Specification** and **FastMCP** best practices.

---

## 1. The 7 Core Architectural Pillars

Connecting LLM agents to heavy engineering software requires enforcing seven non-negotiable architectural pillars:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LLM Agent (Claude / Gemini)                     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ MCP JSON-RPC (stdio / SSE)
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Layer 5: MCP Tooling & Resource Surface             │
│        [inspect_*]   [mutate_*]   [submit_job]   [export_diagnostics]  │
├────────────────────────────────────────────────────────────────────────┤
│                    Layer 4: Async Engine & Progress Streaming          │
│        (Background Tasks, ctx.report_progress, Job Ticket Queue)       │
├────────────────────────────────────────────────────────────────────────┤
│                    Layer 3: Dynamic Introspection & Schemas            │
│        (Model tree reflection, active tags, units, permitted ranges)   │
├────────────────────────────────────────────────────────────────────────┤
│                    Layer 2: Safety Guardrails & Heavy Data Contract    │
│        (SHA-256 immutability, CoW sandbox, Out-of-Context downsampling)│
├────────────────────────────────────────────────────────────────────────┤
│                    Layer 1: Native Bridge & License Lock               │
│        (Subprocess isolation, Base64 zero-escape IPC, FileLock queue)  │
├────────────────────────────────────────────────────────────────────────┤
│                    Layer 0: Host Environment & Display Subsystem       │
│        (Registry, JNA DLL loader, LD_LIBRARY_PATH, Xvfb/Offscreen)     │
└────────────────────────────────────────────────────────────────────────┘
```

1. **Host Environment & Display Subsystem**: 
   - Never rely on default system PATH. Dynamically resolve native libraries (`kernel32.dll -> AddDllDirectory` on Windows; `LD_LIBRARY_PATH` / `rpath` on Linux).
   - In headless / Docker / CI environments, force offscreen rendering (`QT_QPA_PLATFORM=offscreen`, Mesa `llvmpipe`, or virtual framebuffers via `Xvfb`).
2. **Crash & Lifecycle Isolation**: 
   - Solvers can segfault, hit singular matrices, or run OOM. The MCP server process must **never** run the native engine in-process. Decouple via Subprocess / RPC daemon with watchdog timeouts.
3. **Base64 Zero-Escaping Boundary**: 
   - Engineering arguments contain brackets, units, and mathematical formulas (e.g. `1.15[m/s]`, `range(0,0.05,0.1)`). All CLI arguments and standard I/O payloads must be Base64-encoded to prevent shell escaping and injection bugs.
4. **Baseline Immutability & Copy-on-Write (CoW)**:
   - Source models are strictly read-only. Pre- and post-operation SHA-256 hashes are verified. Any mutation must write to a new target path in a sandboxed working directory.
5. **Introspection-First (Hallucination Defense)**:
   - LLMs hallucinate node tags (e.g., `geom1`, `spf`, `solid`). The MCP must enforce an `inspect_model` / `inspect_node` step before mutation or solver execution.
6. **Async Tasks & Progress Streaming (Timeout Defense)**:
   - Industrial simulations take minutes to hours. Synchronous MCP calls hit client/gateway timeouts (typically 60s–120s). The MCP must use **FastMCP Background Tasks (`task=True`)** with `ctx.report_progress` or an **Async Job Ticket & Polling** pattern.
7. **Heavy Data & Downsampling Contract (Context Window Defense)**:
   - Raw simulation meshes and 3D transient fields (GBs) will crash LLM context windows and JSON-RPC limits. **Keep heavy data on disk**. The MCP server returns only:
     - Scalar KPIs (min, max, volume-averaged values, residual convergence).
     - 2D diagnostic cross-section image file paths or small Base64 `BlobResourceContents`.
     - 1D line probe CSV artifact paths.

---

## 2. Resource, Concurrency & Display Governance

### 2.1 Floating License Queue (Token Contention Management)
Commercial engineering tools (COMSOL, ANSYS, Abaqus) use expensive floating license tokens. Concurrent agent invocations crash if tokens are exhausted.
- Use a cross-process **FileLock** (`filelock.FileLock`) or named Semaphore to serialize solver executions up to `MAX_CONCURRENT_LICENSES`.
- While waiting for a license lock, notify the agent: `{"status": "QUEUED", "message": "Waiting for commercial license checkout..."}`.

### 2.2 Headless & Virtual Framebuffer Setup (Cross-Platform)

#### Windows
- Force software rasterizer: e.g. `-3drend sw` in COMSOL.
- Isolate preferences directory (`cs.prefsdir=...`) so multiple concurrent or sequential instances don't corrupt preference lockfiles.

#### Linux / Docker / HPC
- When running in Docker containers, GUI-backed batch engines fail with `Cannot open display`. Use **Xvfb**:
  ```dockerfile
  RUN apt-get update && apt-get install -y xvfb libgl1-mesa-glx libgl1-mesa-dri
  ```
  Wrapper entrypoint:
  ```bash
  #!/usr/bin/env bash
  xvfb-run -a --server-args="-screen 0 1920x1080x24" "$@"
  ```
- Modern Qt-based tools (ANSYS, ParaView): export `QT_QPA_PLATFORM=offscreen`.

---

## 3. Universal 6-Stage Implementation Workflow

### Stage 1: Runtime, License & Display Audit
- Detect installation roots via Windows Registry (`HKLM:\SOFTWARE\...`) or Linux `/opt/`.
- Test headless license checkout without blocking.
- Validate offscreen graphics rendering.

### Stage 2: Native Bridge & Subprocess IPC
- Implement native launcher (`.java`, `.cpp`, or standalone `.py`).
- Implement dynamic library injection (`AddDllDirectory` on Win32, `os.add_dll_directory` on Python 3.8+).
- Standardize all IPC on Base64 payload encoding over stdio.

### Stage 3: Safety Guardrails & Heavy Data Protocol
- Implement `compute_sha256()` and baseline immutability assertion.
- Enforce Copy-on-Write (reject in-place source overwrites).
- Define downsampling rules: extract key scalars, 2D slice PNGs, 1D probe CSVs.

### Stage 4: Dynamic Introspection Engine
- `inspect_environment`: Returns software version, active toolboxes, license status.
- `inspect_model`: Returns tree of components, geometries, physics, boundaries, meshes, studies.
- `inspect_node`: Returns detailed properties, valid value domains, and expected unit expressions for a specific tag.

### Stage 5: Async Job & Progress Streaming Layer
- For quick tools (<10s, e.g. inspect, update_parameter): Run synchronously.
- For heavy compute (solvers, remeshing): Use FastMCP `task=True` and `await ctx.report_progress(progress, total, message)` or asynchronous ticket submission.

### Stage 6: FastMCP Server & Resource Exposition
- Map capabilities into MCP Primitives:
  - **Tools**: `inspect_*`, `update_parameter`, `submit_job`, `query_job`, `export_diagnostics`.
  - **Resources**: `model://{id}/tree`, `log://{id}/residuals`, `file://{id}/preview.png`.
  - **Prompts**: Standard agent SOPs (`mesh-sensitivity-study`, `parametric-optimization`).

---

## 4. Standard Project Directory Layout

```
my-software-mcp/
├── pyproject.toml
├── README.md
├── docker/
│   ├── Dockerfile                 # Includes Xvfb, Mesa software rasterizer, GPU toolkit
│   └── entrypoint.sh              # xvfb-run wrapper
├── src/
│   └── software_mcp/
│       ├── __init__.py
│       ├── server.py              # FastMCP Server (Tools, Resources, Prompts)
│       ├── environment.py         # Registry scan, LD_LIBRARY_PATH, license probe
│       ├── safety/
│       │   ├── immutability.py    # SHA-256 pre/post checker
│       │   └── license_lock.py    # FileLock / Semaphore queue
│       ├── bridge/
│       │   ├── launcher.py        # Subprocess manager, Base64 encoder/decoder, watchdog
│       │   └── native/            # Java Bridge (JAR) / C++ CLI / Python COM script
│       ├── diagnostics/
│       │   └── downsampler.py     # Extracts scalar KPIs, 2D slice PNGs, probe CSVs
│       └── jobs/
│           ├── task_manager.py    # Background worker & job ticket state store
│           └── schemas.py         # Pydantic schemas for models, nodes, tasks
└── tests/
    ├── test_safety_cow.py         # Asserts immutability under failed mutations
    ├── test_downsampler.py        # Asserts context-friendly payload size (<50KB)
    └── test_smoke_bridge.py       # End-to-end smoke test on sample model
```

---

## 5. Production FastMCP Scaffolding Template

```python
"""
Production-Grade Universal Engineering Software MCP Server
Supports: Base64 IPC, CoW Immutability, License Queuing, FastMCP Progress & Background Tasks
"""
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from filelock import FileLock
from fastmcp import FastMCP, Context

# Initialize FastMCP Server
mcp = FastMCP("engineering-software-mcp")

# Environment & Concurrency Constants
WORKSPACE_ROOT = Path(os.getenv("MCP_WORKSPACE_ROOT", "./runs")).resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
LICENSE_LOCK_FILE = WORKSPACE_ROOT / ".license.lock"
license_lock = FileLock(str(LICENSE_LOCK_FILE), timeout=300)

# --- Core Safety & Utility Functions ---

def compute_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file for immutability verification."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def run_bridge_command(command: str, *args: str, timeout: int = 120) -> Dict[str, Any]:
    """
    Executes native bridge using Base64 isolation.
    Eliminates all CLI quoting, special character and injection vulnerabilities.
    """
    encoded_args = [base64.b64encode(a.encode("utf-8")).decode("ascii") for a in [command, *args]]
    # Adjust target launcher path as required (e.g. java -jar bridge.jar or native binary)
    cmd = ["python", "-m", "software_mcp.bridge.launcher", *encoded_args]
    
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {
            "status": "ERROR",
            "code": "BRIDGE_EXECUTION_FAILED",
            "message": proc.stderr.strip() or proc.stdout.strip()
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "status": "ERROR",
            "code": "CORRUPTED_BRIDGE_OUTPUT",
            "raw_output": proc.stdout[:2000]
        }

# --- MCP Tools: Introspection (Synchronous) ---

@mcp.tool()
def inspect_environment() -> Dict[str, Any]:
    """Inspect local installation, detected software version, active toolboxes, and license status."""
    return run_bridge_command("inspect-env")

@mcp.tool()
def inspect_model(model_path: str) -> Dict[str, Any]:
    """
    Read full model tree, components, physics tags, and parameters without altering the file.
    Always call this before making mutations to discover valid tags.
    """
    p = Path(model_path).resolve()
    if not p.exists():
        return {"status": "ERROR", "code": "MODEL_NOT_FOUND", "message": f"Model {model_path} not found"}
    return run_bridge_command("inspect-model", str(p))

# --- MCP Tools: Safe Parameter Mutation (Synchronous, CoW) ---

@mcp.tool()
def update_parameter(
    source_model: str,
    output_model: str,
    param_name: str,
    param_value: str,
    description: Optional[str] = None
) -> Dict[str, Any]:
    """
    Safely updates a model parameter using Copy-on-Write.
    The source model file is strictly read-only and verified via pre/post SHA-256 hashes.
    """
    src = Path(source_model).resolve()
    dst = Path(output_model).resolve()
    if src == dst:
        return {
            "status": "ERROR",
            "code": "COW_VIOLATION",
            "message": "Copy-on-Write violated: source_model and output_model must be different paths"
        }
    if not src.exists():
        return {"status": "ERROR", "code": "MODEL_NOT_FOUND", "message": f"Source model {src} does not exist"}

    baseline_hash = compute_sha256(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Execute mutation via bridge
    result = run_bridge_command("update-parameter", str(src), str(dst), param_name, param_value, description or "")
    
    # Post-validation assertion
    if compute_sha256(src) != baseline_hash:
        raise RuntimeError("FATAL: Source model file was modified during parameter update!")
        
    return result

# --- MCP Tools: Heavy Solver Execution (Async Task + Progress + Downsampling) ---

@mcp.tool(task=True)
async def solve_study(
    working_model: str,
    output_model: str,
    study_tag: str,
    ctx: Context,
    timeout_sec: int = 1800
) -> Dict[str, Any]:
    """
    Executes the simulation solver asynchronously with real-time progress reporting.
    Guarantees floating license queuing and enforces the Heavy Data Downsampling Contract.
    """
    src = Path(working_model).resolve()
    dst = Path(output_model).resolve()
    if not src.exists():
        return {"status": "ERROR", "code": "MODEL_NOT_FOUND", "message": f"Working model {src} missing"}

    await ctx.report_progress(progress=5, total=100)
    
    # 1. Acquire Floating License Queue Lock
    await ctx.report_progress(progress=10, total=100)
    with license_lock:
        await ctx.report_progress(progress=25, total=100)
        
        # 2. Run solver process via native bridge
        solve_res = run_bridge_command("run-study", str(src), str(dst), study_tag, timeout=timeout_sec)
        if solve_res.get("status") != "SUCCESS":
            return solve_res
            
    await ctx.report_progress(progress=80, total=100)
    
    # 3. Heavy Data Downsampling: Extract lightweight KPIs & 2D diagnostics rather than dumping GBs
    diag_res = run_bridge_command("export-diagnostics", str(dst))
    
    await ctx.report_progress(progress=100, total=100)
    
    return {
        "status": "SUCCESS",
        "output_model": str(dst),
        "study_tag": study_tag,
        # Lightweight scalar metrics that fit comfortably in LLM context
        "kpis": diag_res.get("kpis", {}),
        "convergence_status": diag_res.get("convergence", "CONVERGED"),
        # File paths to heavy artifacts for out-of-band inspection
        "artifacts": {
            "slice_image": diag_res.get("slice_image_path"),
            "probe_csv": diag_res.get("probe_csv_path"),
            "solver_log": diag_res.get("log_path")
        }
    }

if __name__ == "__main__":
    mcp.run()
```

---

## 6. Standard Error Code Taxonomy

Every tool failure must return a structured JSON response matching this taxonomy:

| Error Code | Trigger Condition | Recommended Agent Recovery |
| :--- | :--- | :--- |
| `MODEL_NOT_FOUND` | Target model path does not exist. | Verify file path or check previous step's output directory. |
| `COW_VIOLATION` | Target output path equals source path. | Generate a distinct working copy path in `runs/`. |
| `NODE_NOT_FOUND` | Referenced component, physics, or study tag missing. | Run `inspect_model` to read active tags; do not guess tags. |
| `INVALID_PARAMETER`| Expression syntax invalid or unit inconsistent. | Check parameter unit syntax (e.g. `1.15[m/s]`) against documentation. |
| `LICENSE_CHECKOUT_FAILED` | Commercial floating license exhausted or server unreachable. | Wait in queue or report license server downtime. |
| `SOLVER_DIVERGED` | Non-linear solver failed to converge / singular matrix. | Adjust time step `tlist`, relax mesh, or inspect boundary conditions. |
| `TIMEOUT_EXCEEDED` | Solver exceeded maximum time budget. | Shorten simulation time horizon or coarsen mesh resolution. |

---

## 7. Agent Operational Protocol (The Golden Loop)

When an AI agent interacts with an engineering software MCP server, it must strictly follow this 5-step operational loop:

1. **Step 1: Environment Audit**: Call `inspect_environment` once to verify software version and license availability.
2. **Step 2: Schema Discovery**: Call `inspect_model` on the target file. **Never guess or invent tags** (`comp1`, `spf`, `std1`).
3. **Step 3: Copy-on-Write**: Always branch source models into `runs/<task_id>/working.mph`. Never overwrite baseline assets.
4. **Step 4: Immediate Validation**: Validate parameter units and syntax via readback before triggering long-running solves.
5. **Step 5: Async Solve & Downsampled Evaluation**: Call `solve_study`. Evaluate returned scalar KPIs and 2D diagnostic images rather than requesting raw field arrays.

---

## 8. Security, Authentication & Dangerous Action Guards

### 8.1 Transport & Authentication (Production Deployments)
When deploying MCP servers beyond local `stdio` mode (e.g., as shared team services over network):
- **Local development**: `stdio` transport is fine (implicit trust, child-process model).
- **Remote / team / cloud**: Use **SSE over HTTPS** with **OAuth 2.1 + PKCE** authentication. The MCP server acts as an OAuth 2.1 Resource Server.
- **Enterprise environments**: Integrate with your corporate Identity Provider (Okta, Azure AD / Entra ID, Keycloak) for centralized authorization via the **Enterprise-Managed Authorization (EMA)** extension.
- **Granular Tool-Level Scopes**: Map OAuth scopes to specific MCP capabilities:
  - `mcp:tools:inspect` — read-only model queries.
  - `mcp:tools:mutate` — parameter and geometry modifications.
  - `mcp:tools:solve` — solver execution (most expensive, license-consuming).
  - `mcp:tools:admin` — environment configuration and license management.

### 8.2 Human-in-the-Loop (HITL) Guard for Dangerous Actions
Industrial software MCP servers can trigger irreversible, expensive, or dangerous operations. Implement a tiered action classification:

| Risk Tier | Examples | Guard Policy |
| :--- | :--- | :--- |
| **Low (Auto-approve)** | `inspect_model`, `inspect_environment`, read parameters | Execute immediately, no confirmation needed. |
| **Medium (Log & proceed)** | `update_parameter`, mesh refinement, export diagnostics | Log with full audit trail; auto-approve unless flagged. |
| **High (HITL Required)** | `solve_study` (hours of compute + license), delete model, batch parametric sweep (100+ runs) | **Pause execution**, serialize agent state, emit structured approval request to human reviewer with full context (tool name, arguments, estimated cost/time). Resume only after explicit human approval. |

Implementation pattern (FastMCP with elicitation):
```python
@mcp.tool()
async def delete_working_model(model_path: str, ctx: Context) -> Dict[str, Any]:
    """Permanently deletes a working model copy. Requires human confirmation."""
    p = Path(model_path).resolve()
    # Emit confirmation request back to host UI
    confirmation = await ctx.elicit(
        message=f"⚠️ Confirm permanent deletion of: {p.name}",
        schema={"type": "object", "properties": {"confirm": {"type": "boolean"}}, "required": ["confirm"]}
    )
    if not confirmation.data.get("confirm"):
        return {"status": "CANCELLED", "message": "Deletion cancelled by user"}
    p.unlink()
    return {"status": "SUCCESS", "deleted": str(p)}
```

### 8.3 Container & Network Isolation
- Run production MCP servers inside containers with **read-only root filesystems** and **dropped Linux capabilities**.
- Enforce **strict egress filtering**: block public outbound internet. The MCP server should only communicate with the specific internal solver engine or license server it wraps.

---

## 9. Observability, Telemetry & Audit Trail

### 9.1 Logging Rules
- **CRITICAL**: When using `stdio` transport, **never write logs to `stdout`** — that channel is reserved for JSON-RPC protocol messages. Route all application logs to `stderr`.
- Emit structured JSON log records for every tool invocation containing:
  - `session_id`: Client/agent session identifier.
  - `tool_name`: Which MCP tool was called.
  - `arguments`: Sanitized parameters (redact file contents, strip PII, truncate large blobs).
  - `outcome`: `SUCCESS` / error code from the taxonomy.
  - `duration_ms`: Wall-clock execution time.
  - `license_wait_ms`: Time spent waiting for floating license acquisition.

### 9.2 OpenTelemetry (OTel) Integration
Instrument the MCP server with OpenTelemetry for distributed tracing across the full agent → MCP → native solver chain:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# Initialize OTel
provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("engineering-mcp-server")

@mcp.tool()
def inspect_model(model_path: str) -> Dict[str, Any]:
    with tracer.start_as_current_span("mcp.tool.inspect_model") as span:
        span.set_attribute("mcp.tool.name", "inspect_model")
        span.set_attribute("mcp.model.path", model_path)
        result = run_bridge_command("inspect-model", model_path)
        span.set_attribute("mcp.result.status", result.get("status", "UNKNOWN"))
        return result
```

**Key metrics to track**:
- **Tool Latency Histograms**: Flag performance regressions per tool.
- **Error Rate by Code**: Monitor `SOLVER_DIVERGED` vs `LICENSE_CHECKOUT_FAILED` ratios.
- **License Queue Depth**: Alert when queue length exceeds threshold.

### 9.3 Audit Trail for Compliance
For regulated industries (aerospace, nuclear, biomedical), maintain immutable audit logs:
- Every solver invocation: who triggered it, what parameters were used, what baseline model hash was verified.
- Every parameter mutation: old value → new value, with timestamp and agent session ID.
- Forward structured logs to SIEM / centralized log aggregation (ELK, Grafana Loki, SigNoz).

---

## 10. Testing Pyramid for MCP Servers

### 10.1 Four-Layer Test Strategy

```
         ┌─────────────────────────┐
         │   Layer 4: Agent Evals  │  (Nightly / Pre-release only)
         │   Real LLM + MCP E2E   │  Non-deterministic, slow, costly
         ├─────────────────────────┤
         │ Layer 3: Integration    │  (Per-PR, mocked downstream)
         │ Subprocess MCP Client   │  Spawn server, call tools, assert JSON
         ├─────────────────────────┤
         │ Layer 2: Protocol &     │  (Per-PR, fast)
         │ Contract / Schema Tests │  Snapshot tools/list, validate JSON-RPC
         ├─────────────────────────┤
         │ Layer 1: Unit Tests     │  (Per-commit, fastest)
         │ Handler logic isolation │  Pure functions, no transport
         └─────────────────────────┘
```

### 10.2 Specific Test Categories

| Test Category | What to Assert | Example |
| :--- | :--- | :--- |
| **Safety / CoW** | Source model hash unchanged after failed mutation. | Corrupt the bridge mid-write; assert baseline SHA-256 is intact. |
| **Downsampling** | Tool response payload fits in LLM context. | Assert `len(json.dumps(result)) < 50_000` bytes for any solver output. |
| **License Lock** | Concurrent calls serialize correctly. | Launch 3 parallel `solve_study` calls; assert no `LICENSE_CHECKOUT_FAILED`. |
| **Schema Snapshot** | `tools/list` output unchanged across commits. | Snapshot `tools/list` JSON; fail CI on unexpected schema drift. |
| **Error Taxonomy** | Every error code in taxonomy is reachable. | Inject `MODEL_NOT_FOUND`, `INVALID_PARAMETER`, `SOLVER_DIVERGED` scenarios. |
| **Bridge Timeout** | Watchdog kills hung subprocesses. | Mock a bridge that hangs forever; assert `TIMEOUT_EXCEEDED` within budget. |

### 10.3 CI Pipeline Recommendations
- **Per-commit**: Unit tests (handler logic) + schema snapshots. Fast, deterministic.
- **Per-PR**: Integration tests with mocked native bridge (no real software license needed). Use `unittest.mock` or `WireMock` to simulate bridge subprocess responses.
- **Nightly**: Full end-to-end agent evaluation against a real software installation. Reserve for pre-release validation due to cost and non-determinism.
- **Tooling**: Use **MCP Inspector** (`npx @modelcontextprotocol/inspector`) for interactive debugging during development.

---

## 11. Software Version Migration & Compatibility

### 11.1 The Version Upgrade Problem
Industrial software (COMSOL 6.3→6.4, ANSYS 2024R1→2025R1) frequently introduces:
- Restructured feature trees and renamed node tags.
- Deprecated or removed API methods.
- Changed default solver configurations and mesh algorithms.
- License manager version incompatibilities (newer clients can't check out from older FlexNet servers).

### 11.2 Version Compatibility Matrix
Maintain a machine-readable compatibility manifest in the MCP project:

```json
{
  "software": "COMSOL Multiphysics",
  "mcp_version": "1.2.0",
  "supported_versions": [
    {
      "version": "6.4",
      "build_range": ["400", "999"],
      "status": "primary",
      "known_breaking_changes": []
    },
    {
      "version": "6.3",
      "build_range": ["300", "399"],
      "status": "supported",
      "known_breaking_changes": [
        "model.physics().feature() renamed to model.physics().create() for new features"
      ]
    },
    {
      "version": "6.2",
      "build_range": ["200", "299"],
      "status": "deprecated",
      "known_breaking_changes": [
        "Pair boundary selections restructured",
        "Legacy fallback features removed from physics nodes"
      ]
    }
  ]
}
```

### 11.3 Migration Safety Practices
1. **API Version Pinning**: Never point production scripts to a floating "latest" installation path. Explicitly declare software paths and expected version ranges in MCP server configuration.
2. **Side-by-Side Installation**: Keep the previous major version installed alongside the new one. Both COMSOL and ANSYS support parallel major-version installations.
3. **Automated Regression Suite**: Maintain a set of canonical test models (standard verification geometries) that are solved automatically after any software upgrade. Compare results numerically (within tolerance) against golden reference outputs.
4. **Record-and-Compare**: After upgrading, use the software's "Record Code" / "Record Java" feature to re-record a standard workflow. Diff the generated code against legacy scripts to identify renamed methods, changed defaults, or restructured node trees.
5. **`inspect_environment` Version Gate**: The MCP server's `inspect_environment` tool should return the detected software version. The agent should compare this against the compatibility matrix and warn or abort if the version is unsupported.

---

## 12. Deployment Topology Reference

### 12.1 Local Developer Mode (Simplest)
```
[Claude Desktop / Cursor / AGY]
         │ stdio
         ▼
[Python FastMCP Server]
         │ subprocess
         ▼
[Native Bridge (Java/C++)]
         │
         ▼
[COMSOL / ANSYS / Abaqus on same machine]
```
- No auth needed. No network exposure.
- Ideal for prototyping and individual research workflows.

### 12.2 Team Server Mode (SSE + OAuth)
```
[Multiple Agents / Team Members]
         │ HTTPS + SSE + OAuth 2.1
         ▼
[Nginx Reverse Proxy + TLS]
         │
         ▼
[Python FastMCP Server (containerized)]
         │ subprocess + FileLock
         ▼
[Native Bridge]
         │
         ▼
[Shared License Server + Compute Node]
```
- OAuth 2.1 + PKCE for authentication.
- FileLock serializes license-consuming operations.
- Nginx handles TLS termination and rate limiting.

### 12.3 HPC / Slurm Cluster Mode
```
[Agent on Login Node]
         │ SSH + stdio
         ▼
[MCP Server on Login Node]
         │ sbatch / srun
         ▼
[Compute Nodes (GPU/CPU)]
         │ xvfb-run + native solver
         ▼
[Shared Filesystem (Lustre/GPFS)]
```
- MCP server submits jobs via Slurm `sbatch` and polls `squeue` for completion.
- Results written to shared filesystem; MCP server reads and downsamples.
- Xvfb required on compute nodes for any visualization exports.
