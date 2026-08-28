from pathlib import Path

from workpilot.tools.base import Registry
from workpilot.tools.fs import ReadFileTool


def test_read_file_schema_matches_anthropic_tool_format():
    schema = ReadFileTool(workspace=Path(".")).schema

    assert schema["name"] == "read_file"
    assert schema["description"]
    assert schema["input_schema"]["type"] == "object"
    assert "path" in schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["path"]


def test_registry_returns_schemas_sorted_by_name():
    """工具顺序必须稳定 —— 顺序一抖动，prompt cache 就整片失效。"""
    class ZTool:
        name = "z_tool"
        schema = {"name": "z_tool"}

    class ATool:
        name = "a_tool"
        schema = {"name": "a_tool"}

    reg = Registry([ZTool(), ATool()])

    assert [s["name"] for s in reg.schemas()] == ["a_tool", "z_tool"]


def test_registry_get_returns_tool_by_name():
    tool = ReadFileTool(workspace=Path("."))
    reg = Registry([tool])

    assert reg.get("read_file") is tool
