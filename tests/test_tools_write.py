import pytest

from workpilot.agent.events import Danger
from workpilot.tools.fs import WriteFileTool


def test_creates_new_file(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    tool.run(path="new.py", content="print('hi')\n")

    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_overwrites_existing_file(tmp_path):
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    tool = WriteFileTool(workspace=tmp_path)

    tool.run(path="a.txt", content="new")

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


def test_creates_missing_parent_directories(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)

    tool.run(path="src/deep/mod.py", content="x = 1\n")

    assert (tmp_path / "src/deep/mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_rejects_path_outside_workspace(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tool = WriteFileTool(workspace=ws)

    with pytest.raises(ValueError, match="escapes workspace"):
        tool.run(path="../evil.txt", content="pwned")

    assert not (tmp_path / "evil.txt").exists()


def test_write_tool_is_confirm_level():
    assert WriteFileTool.danger is Danger.CONFIRM
