import pytest

from workpilot.tools.fs import EditFileTool


def make(tmp_path, content="def f():\n    return 1\n"):
    (tmp_path / "a.py").write_text(content, encoding="utf-8")
    return EditFileTool(workspace=tmp_path), tmp_path / "a.py"


def test_unique_match_is_replaced(tmp_path):
    tool, f = make(tmp_path)

    tool.run(path="a.py", old_string="return 1", new_string="return 42")

    assert f.read_text(encoding="utf-8") == "def f():\n    return 42\n"


def test_no_match_raises_and_leaves_file_untouched(tmp_path):
    tool, f = make(tmp_path)
    before = f.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="not found"):
        tool.run(path="a.py", old_string="return 999", new_string="x")

    assert f.read_text(encoding="utf-8") == before


def test_multiple_matches_raise_with_count_and_leave_file_untouched(tmp_path):
    """出现多次说明可能改错位置 —— 拒绝执行，并把出现次数告诉模型。"""
    tool, f = make(tmp_path, content="x = 1\ny = 1\nz = 1\n")
    before = f.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="3"):
        tool.run(path="a.py", old_string="= 1", new_string="= 2")

    assert f.read_text(encoding="utf-8") == before


def test_only_the_matched_span_changes(tmp_path):
    tool, f = make(tmp_path, content="header\nTARGET\nfooter\n")

    tool.run(path="a.py", old_string="TARGET", new_string="REPLACED")

    assert f.read_text(encoding="utf-8") == "header\nREPLACED\nfooter\n"


def test_edit_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET", encoding="utf-8")
    ws = tmp_path / "proj"
    ws.mkdir()
    tool = EditFileTool(workspace=ws)

    with pytest.raises(ValueError, match="escapes workspace"):
        tool.run(path="../secret.txt", old_string="SECRET", new_string="HACKED")

    assert outside.read_text(encoding="utf-8") == "SECRET"


def test_edit_tool_is_confirm_level():
    from workpilot.agent.events import Danger
    assert EditFileTool.danger is Danger.CONFIRM
