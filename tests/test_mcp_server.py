"""Comprehensive unit and integration test suite for COMSOL MCP Server."""

import json
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp package required for MCP server tests")

from hydro_disturb_bench.mcp.safety import (
    ERR_COW_VIOLATION,
    compute_sha256,
    validate_cow_paths,
)
from hydro_disturb_bench.mcp.server import server
from hydro_disturb_bench.mcp.tools_geometry import (
    comsol_geom_build,
    comsol_geom_primitive,
)
from hydro_disturb_bench.mcp.tools_meta import (
    comsol_execute_java,
    comsol_export_to_java,
    comsol_inspect_environment,
)
from hydro_disturb_bench.mcp.tools_model import (
    comsol_create_model,
    comsol_evaluate_expressions,
)
from hydro_disturb_bench.mcp.tools_params import comsol_set_parameters
from hydro_disturb_bench.mcp.tools_study import (
    comsol_configure_solver,
    comsol_solve_study,
)


def test_mcp_server_registration():
    """Verify that all tools, resources, and prompts are properly registered on MCPServer."""
    assert server.name == "comsol-multiphysics"
    tools = [tool.name for tool in server._tool_manager.list_tools()]
    assert len(tools) == 26
    print(f"Server initialized with {len(tools)} tools: {tools}")


def test_safety_cow_rejection():
    """Ensure Copy-on-Write strictly forbids in-place overwrite of models."""
    same_path = "case/cylinder_flow.mph"
    err = validate_cow_paths(same_path, same_path)
    assert err is not None
    assert err["code"] == ERR_COW_VIOLATION

    diff_err = validate_cow_paths("case/source.mph", "runs/working.mph")
    assert diff_err is None


def test_inspect_environment():
    """Ensure environment audit detects COMSOL 6.4 and OpenJDK when installed, or returns structured error."""
    env = comsol_inspect_environment()
    assert env["status"] in ("SUCCESS", "ERROR")
    if env["status"] == "SUCCESS":
        assert "6.4" in env["comsol_version"]
        assert Path(env["comsol_root"]).is_dir()
        assert Path(env["openjdk_path"]).is_file()
    else:
        assert env["code"] == "ENVIRONMENT_ERROR"


def test_configure_solver_validation_and_maxstep(monkeypatch):
    """Verify comsol_configure_solver validates CoW and wires maxstep snippet."""
    # CoW rejection
    err = comsol_configure_solver("same.mph", "same.mph", study_tag="std1", maxstep="0.02[s]")
    assert err["code"] == ERR_COW_VIOLATION

    # Mock execute_java_code to verify Java snippet construction without calling COMSOL
    captured = {}

    def mock_execute_java_code(java_snippet, source_model, output_model, **kwargs):
        captured["snippet"] = java_snippet
        captured["source"] = source_model
        captured["output"] = output_model
        return {"status": "SUCCESS"}

    monkeypatch.setattr(
        "hydro_disturb_bench.mcp.tools_study.execute_java_code",
        mock_execute_java_code,
    )

    res = comsol_configure_solver(
        source_model="case/source.mph",
        output_model="runs/target.mph",
        study_tag="std_test",
        tlist="range(0,0.05,10)",
        rtol="1e-4",
        maxstep="0.05",
    )
    assert res["status"] == "SUCCESS"
    assert res["study_tag"] == "std_test"
    assert res["tlist"] == "range(0,0.05,10)"
    assert res["rtol"] == "1e-4"
    assert res["maxstep"] == "0.05"
    assert 'feat.set("maxstepbdf", "0.05");' in captured["snippet"]
    assert 'feat.set("maxstepconstraintbdf", "const");' in captured["snippet"]


def test_solve_study_timeout_forwarding(monkeypatch):
    """Verify comsol_solve_study forwards timeout_sec and returns error on timeout."""
    # CoW rejection
    err = comsol_solve_study("same.mph", "same.mph", study_tag="std1", timeout_sec=60)
    assert err["code"] == ERR_COW_VIOLATION

    captured_call = {}

    def mock_run_study(model_path, output_path, study_tag, timeout=None, **kwargs):
        captured_call["model_path"] = model_path
        captured_call["output_path"] = output_path
        captured_call["study_tag"] = study_tag
        captured_call["timeout"] = timeout
        return {"status": "SUCCESS", "study_tag": study_tag}

    monkeypatch.setattr(
        "hydro_disturb_bench.mcp.tools_study._legacy_run_study",
        mock_run_study,
    )

    res = comsol_solve_study(
        source_model="case/source.mph",
        output_model="runs/target.mph",
        study_tag="std1",
        timeout_sec=120,
    )
    assert res["status"] == "SUCCESS"
    assert captured_call["timeout"] == 120.0

    # Failure / timeout handling
    def mock_run_study_fail(*args, **kwargs):
        raise TimeoutError("Simulated solver timeout after 30 seconds")

    monkeypatch.setattr(
        "hydro_disturb_bench.mcp.tools_study._legacy_run_study",
        mock_run_study_fail,
    )
    fail_res = comsol_solve_study(
        source_model="case/source.mph",
        output_model="runs/target2.mph",
        study_tag="std1",
        timeout_sec=30,
    )
    assert fail_res["status"] == "ERROR"
    assert fail_res["code"] == "SOLVER_FAILED"
    assert "Simulated solver timeout" in fail_res["message"]


def test_plugin_tool_names_match_server():
    """Verify that every tool declared in universal-software-mcp-builder plugin mcp.json exists in MCP server."""
    plugin_mcp_path = Path(".agents/plugins/universal-software-mcp-builder/mcp.json")
    if not plugin_mcp_path.is_file():
        pytest.skip("plugin mcp.json not found")
    data = json.loads(plugin_mcp_path.read_text(encoding="utf-8"))
    tools_config = data["mcpServers"]["software_mcp_scaffold"]["tools"]

    server_tools = {tool.name for tool in server._tool_manager.list_tools()}
    assert len(server_tools) == 26

    for tool_name in tools_config:
        assert tool_name in server_tools, f"Tool '{tool_name}' declared in mcp.json not registered in server!"


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
