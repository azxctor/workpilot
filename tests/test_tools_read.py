import pytest
from workpilot.tools.fs import ReadFileTool


def test_read_file_returns_content_with_line_numbers(tmp_path):
    (tmp_path / "hello.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    out = tool.run(path="hello.py")

    assert "1\timport os" in out
    assert "2\tprint('hi')" in out


def test_read_file_rejects_path_escaping_workspace(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    workspace = tmp_path / "project"
    workspace.mkdir()
    tool = ReadFileTool(workspace=workspace)

    with pytest.raises(ValueError, match="escapes workspace"):
        tool.run(path="../secret.txt")


def test_read_file_truncates_long_file_and_says_so(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(f"line{i}" for i in range(5000)),
                                      encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)

    out = tool.run(path="big.txt")

    assert out.count("\n") < 2100                # 没有把 5000 行全塞回来
    assert "truncated" in out.lower()            # 明确告知模型被截断了
    assert "5000" in out                         # 并告知总行数


def test_read_file_missing_file_raises_readable_error(tmp_path):
    tool = ReadFileTool(workspace=tmp_path)

    with pytest.raises(FileNotFoundError):
        tool.run(path="nope.txt")
