import asyncio

import pytest

pytest.importorskip("mcp")

EXPECTED = {
    "lsd_status", "list_models", "read_equations", "describe_configuration",
    "copy_model", "write_equations", "set_values", "set_run_settings",
    "set_saved", "compile_model", "run_configuration", "read_results",
    "sa_create_design", "sa_run_design", "sa_analyze",
}


def test_server_lists_all_tools():
    from lsd_mcp import server
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 15
    assert {tool.name for tool in tools} == EXPECTED
    for tool in tools:
        assert tool.description


def test_tool_functions_return_errors_not_exceptions(models_dir):
    from lsd_mcp import server
    result = server.describe_configuration("missing", "x")
    assert "error" in result


def test_status_and_describe_through_tools(linear, lsd_root):
    from lsd_mcp import server
    info = server.lsd_status()
    assert info["lsd_tag"] and info["compiler"]
    described = server.describe_configuration("linear", "Linear")
    unit = [obj for obj in described["objects"] if obj["object"] == "Unit"][0]
    names = [element["name"] for element in unit["elements"]]
    assert names == ["a", "b", "c", "n", "Z"]
    assert unit["elements"][0]["value"] == 0.5


def test_forwarded_modules_import_without_mcp():
    import subprocess
    import sys
    code = ("import sys; sys.modules['mcp'] = None; "
            "import lsd_mcp.tools, lsd_mcp.call, lsd_mcp.backend; print('ok')")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.stdout.strip() == "ok", done.stderr


def test_call_module_prints_one_json_document(linear, lsd_root, monkeypatch):
    import json
    import subprocess
    import sys
    env = dict(__import__("os").environ, LSD_MODELS=str(linear.parent))
    done = subprocess.run([sys.executable, "-m", "lsd_mcp.call", "describe_configuration"],
                          input=json.dumps({"model": "linear", "config": "Linear"}),
                          capture_output=True, text=True, env=env)
    assert json.loads(done.stdout)["run_settings"]["MAX_STEP"] == "10"
    bad = subprocess.run([sys.executable, "-m", "lsd_mcp.call", "set_saved"], input="{}",
                         capture_output=True, text=True, env=env)
    assert json.loads(bad.stdout)["error"].startswith("TypeError")


def test_status_utilities_is_a_string(models_dir, lsd_root):
    from lsd_mcp import server
    info = server.lsd_status()
    assert info["utilities"] == "built" or info["utilities"].startswith("not built yet")
    assert "utilities_built" not in info
