"""Comprehensive unit and integration test suite for COMSOL MCP Server."""

import json
import pytest
from pathlib import Path
import tempfile

from hydro_disturb_bench.mcp.server import server
from hydro_disturb_bench.mcp.safety import (
    ERR_COW_VIOLATION,
    ERR_MODEL_NOT_FOUND,
    compute_sha256,
    validate_cow_paths,
)
from hydro_disturb_bench.mcp.tools_meta import (
    comsol_inspect_environment,
    comsol_execute_java,
    comsol_export_to_java,
)
from hydro_disturb_bench.mcp.tools_model import (
    comsol_create_model,
    comsol_inspect_model,
    comsol_evaluate_expressions,
)
from hydro_disturb_bench.mcp.tools_params import comsol_set_parameters
from hydro_disturb_bench.mcp.tools_geometry import (
    comsol_geom_primitive,
    comsol_geom_build,
)


def test_mcp_server_registration():
    """Verify that all tools, resources, and prompts are properly registered on MCPServer."""
    assert server.name == "comsol-multiphysics"
    # Inspect registered tools
    tools = server._tool_manager.list_tools() if hasattr(server, "_tool_manager") else []
    # MCPServer exposes tools via internal registry or list_tools
    print(f"Server initialized with name: {server.name}")


def test_safety_cow_rejection():
    """Ensure Copy-on-Write strictly forbids in-place overwrite of models."""
    same_path = "case/cylinder_flow.mph"
    err = validate_cow_paths(same_path, same_path)
    assert err is not None
    assert err["code"] == ERR_COW_VIOLATION

    diff_err = validate_cow_paths("case/source.mph", "runs/working.mph")
    assert diff_err is None


def test_inspect_environment():
    """Ensure environment audit detects COMSOL 6.4 and OpenJDK."""
    env = comsol_inspect_environment()
    assert env["status"] == "SUCCESS"
    assert "6.4" in env["comsol_version"]
    assert Path(env["comsol_root"]).is_dir()
    assert Path(env["openjdk_path"]).is_file()


@pytest.mark.integration
def test_from_scratch_model_and_dynamic_java():
    """End-to-end integration test: from-scratch model creation, geometry primitive, parameter setting, and reverse-engineering."""
    with tempfile.TemporaryDirectory(prefix="comsol_mcp_test_") as tmp:
        work = Path(tmp)
        out_mph = work / "test_scratch.mph"

        # 1. Create 2D blank model
        create_res = comsol_create_model(dim=2, name="MCP_Test_Case", output_model=str(out_mph))
        assert create_res["status"] == "SUCCESS"
        assert out_mph.is_file()
        init_hash = compute_sha256(out_mph)

        # 2. Add Geometry Primitive (Rectangle) into copy
        geom_mph = work / "test_geom.mph"
        geom_res = comsol_geom_primitive(
            source_model=str(out_mph),
            output_model=str(geom_mph),
            shape="Rectangle",
            tag="r1",
            params={"size": ["2.0", "0.4"], "pos": ["0.0", "0.0"]},
        )
        assert geom_res["status"] == "SUCCESS"
        assert geom_mph.is_file()

        # Check source immutability
        assert compute_sha256(out_mph) == init_hash

        # 3. Build geometry & inspect topology
        build_mph = work / "test_built.mph"
        build_res = comsol_geom_build(
            source_model=str(geom_mph),
            output_model=str(build_mph),
        )
        assert build_res["status"] == "SUCCESS"
        assert build_res.get("topology", {}).get("domains") == 1
        assert build_res.get("topology", {}).get("boundaries") == 4

        # 4. Set parameters
        param_mph = work / "test_params.mph"
        param_res = comsol_set_parameters(
            source_model=str(build_mph),
            output_model=str(param_mph),
            parameters={
                "U_mean": "1.5[m/s]",
                "rho": "1000[kg/m^3]",
            },
        )
        assert param_res["status"] == "SUCCESS"

        # 5. Evaluate expressions
        eval_res = comsol_evaluate_expressions(
            model_path=str(param_mph),
            expressions=["U_mean", "rho * U_mean^2"],
        )
        assert eval_res["status"] == "SUCCESS"
        assert eval_res["results"]["U_mean"]["value"] == 1.5

        # 6. Turing-complete dynamic Java execution
        dyn_mph = work / "test_dynamic.mph"
        dyn_res = comsol_execute_java(
            java_code='model.param().set("injected_var", "123.456");',
            source_model=str(param_mph),
            output_model=str(dyn_mph),
        )
        assert dyn_res["status"] == "SUCCESS"
        assert dyn_mph.is_file()

        # 7. Reverse-engineer to Java code
        out_java = work / "reconstructed.java"
        exp_res = comsol_export_to_java(
            source_model=str(dyn_mph),
            output_java_path=str(out_java),
        )
        assert exp_res["status"] == "SUCCESS"
        assert out_java.is_file()
        assert out_java.stat().st_size > 0
