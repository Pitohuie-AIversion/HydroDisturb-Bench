"""
Production FastMCP Server for SolidWorks CAD.
Architecture: Paradigm [SDK], License FileLock, CoW Immutability, Downsampled Diagnostics.
"""
import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from fastmcp import FastMCP, Context
from src.safety.license_lock import license_lock
from src.safety.immutability import compute_sha256, assert_cow
from src.diagnostics.downsampler import extract_diagnostics

mcp = FastMCP("solidworks-cad-mcp")

def run_native_bridge(command: str, *args: str, timeout: int = 600) -> Dict[str, Any]:
    """
    Calls native SolidWorks CAD Python SDK / COM / RPC daemon in an isolated worker process.
    """
    cmd = ["python", "-m", "src.bridge.sdk_worker", command, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"status": "ERROR", "code": "SDK_CALL_FAILED", "message": proc.stderr.strip()}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "ERROR", "code": "CORRUPTED_OUTPUT", "raw": proc.stdout[:1000]}


# --- Core Tools: Introspection (Anti-Hallucination) ---

@mcp.tool()
def inspect_environment() -> Dict[str, Any]:
    """Verifies SolidWorks CAD installation path, version, and license server reachability."""
    return run_native_bridge("inspect-env")

@mcp.tool()
def inspect_model(model_path: str) -> Dict[str, Any]:
    """
    Read model tree, components, physics, boundary conditions, and parameters without modifying the file.
    Always call this first before mutating parameters or running jobs.
    """
    p = Path(model_path).resolve()
    if not p.exists():
        return {"status": "ERROR", "code": "MODEL_NOT_FOUND", "message": f"Model {model_path} not found"}
    return run_native_bridge("inspect-model", str(p))

# --- Core Tools: Safe Copy-on-Write Mutation ---

@mcp.tool()
def update_parameter(
    source_model: str,
    output_model: str,
    param_name: str,
    param_value: str
) -> Dict[str, Any]:
    """
    Safely mutates a model parameter using Copy-on-Write.
    The source model file is strictly read-only and verified via SHA-256 pre/post hashing.
    """
    src = Path(source_model).resolve()
    dst = Path(output_model).resolve()
    assert_cow(src, dst)
    if not src.exists():
        return {"status": "ERROR", "code": "MODEL_NOT_FOUND", "message": f"Source model {src} missing"}
    
    baseline_hash = compute_sha256(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    result = run_native_bridge("update-parameter", str(src), str(dst), param_name, param_value)
    
    # Assert source model remained untouched
    if compute_sha256(src) != baseline_hash:
        raise RuntimeError("CRITICAL: Source model hash changed! Immutability violated.")
        
    return result

# --- Core Tools: Heavy Compute Execution (Async + License Queuing) ---

@mcp.tool(task=True)
async def solve_job(
    working_model: str,
    output_dir: str,
    job_tag: str,
    ctx: Context,
    timeout_sec: int = 1800
) -> Dict[str, Any]:
    """
    Executes heavy computation with real-time progress reporting.
    Guarantees commercial floating license queueing and returns downsampled diagnostics.
    """
    src = Path(working_model).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    
    await ctx.report_progress(progress=10, total=100)
    
    # 1. Acquire Floating License Queue Lock
    with license_lock:
        await ctx.report_progress(progress=30, total=100)
        # 2. Run computation in isolated subprocess
        res = run_native_bridge("run-solve", str(src), str(out), job_tag, timeout=timeout_sec)
        if res.get("status") != "SUCCESS":
            return res
            
    await ctx.report_progress(progress=80, total=100)
    
    # 3. Downsample heavy GB results into context-friendly scalars & 2D diagnostics
    diagnostics = extract_diagnostics(out)
    await ctx.report_progress(progress=100, total=100)
    
    return {
        "status": "SUCCESS",
        "job_tag": job_tag,
        "diagnostics": diagnostics
    }

if __name__ == "__main__":
    mcp.run()
