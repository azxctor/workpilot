import pytest

from workpilot.agent.events import Danger
from workpilot.tools.fs import ListFilesTool


def build(tmp_path):
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")
    for junk in [".git", "node_modules", ".venv", "__pycache__"]:
        d = tmp_path / junk
        d.mkdir()
        (d / "trash.txt").write_text("x", encoding="utf-8")
    return ListFilesTool(workspace=tmp_path)


def test_lists_files_recursively(tmp_path):
    tool = build(tmp_path)

    out = tool.run(path=".")

    assert "main.py" in out
    assert "src/app.py" in out


def test_skips_noise_directories(tmp_path):
    tool = build(tmp_path)

    out = tool.run(path=".")

    for junk in [".git", "node_modules", ".venv", "__pycache__"]:
        assert junk not in out


def test_rejects_path_outside_workspace(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tool = ListFilesTool(workspace=ws)

    with pytest.raises(ValueError, match="escapes workspace"):
        tool.run(path="..")


def test_truncates_when_too_many_entries(tmp_path):
    for i in range(1200):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    tool = ListFilesTool(workspace=tmp_path)

    out = tool.run(path=".")

    assert len(out.splitlines()) < 1100
    assert "truncated" in out.lower()


def test_list_tool_is_safe_level():
    assert ListFilesTool.danger is Danger.SAFE
