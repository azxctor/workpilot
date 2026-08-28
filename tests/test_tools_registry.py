import pytest
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


ALL_TOOL_CLASSES = [
    ("read_file", "workpilot.tools.fs", "ReadFileTool"),
    ("write_file", "workpilot.tools.fs", "WriteFileTool"),
    ("edit_file", "workpilot.tools.fs", "EditFileTool"),
    ("list_files", "workpilot.tools.fs", "ListFilesTool"),
    ("grep", "workpilot.tools.search", "GrepTool"),
    ("bash", "workpilot.tools.shell", "BashTool"),
]


@pytest.mark.parametrize("name,module,cls_name", ALL_TOOL_CLASSES)
def test_every_tool_has_a_valid_schema(name, module, cls_name):
    import importlib
    cls = getattr(importlib.import_module(module), cls_name)
    schema = cls(workspace=Path(".")).schema

    assert schema["name"] == name
    assert schema["description"], f"{name} 缺少 description"
    assert schema["input_schema"]["type"] == "object"
    assert isinstance(schema["input_schema"]["properties"], dict)
    assert isinstance(schema["input_schema"]["required"], list)
    # required 里的字段必须真的在 properties 里声明过
    for field in schema["input_schema"]["required"]:
        assert field in schema["input_schema"]["properties"], \
            f"{name}: required 字段 {field} 未在 properties 中声明"


def test_build_default_registry_contains_all_six_tools(tmp_path):
    from workpilot.tools.base import build_default_registry

    reg = build_default_registry(tmp_path)

    assert [s["name"] for s in reg.schemas()] == sorted(
        n for n, _, _ in ALL_TOOL_CLASSES)
