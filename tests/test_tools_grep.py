import pytest

from workpilot.agent.events import Danger
from workpilot.tools.search import GrepTool, _search_python


def build(tmp_path):
    (tmp_path / "a.py").write_text("import os\ndef run():\n    return os\n",
                                   encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\nimport sys\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "c.py").write_text("import evil\n", encoding="utf-8")
    return GrepTool(workspace=tmp_path)


def test_finds_matches_with_path_and_line_number(tmp_path):
    tool = build(tmp_path)

    out = tool.run(pattern="import")

    assert "a.py:1" in out
    assert "b.py:2" in out
    assert "import os" in out


def test_skips_noise_directories(tmp_path):
    tool = build(tmp_path)

    out = tool.run(pattern="import")

    assert "evil" not in out


def test_reports_no_match_clearly(tmp_path):
    tool = build(tmp_path)

    out = tool.run(pattern="zzz_nonexistent")

    assert "没有匹配" in out


def test_supports_regex(tmp_path):
    tool = build(tmp_path)

    out = tool.run(pattern=r"^def \w+")

    assert "a.py:2" in out
    assert "b.py" not in out


def test_rejects_path_outside_workspace(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    tool = GrepTool(workspace=ws)

    with pytest.raises(ValueError, match="escapes workspace"):
        tool.run(pattern="x", path="..")


def test_python_fallback_finds_same_hits_as_the_tool(tmp_path):
    """无 ripgrep 时的回退实现，必须给出相同的命中集合。"""
    tool = build(tmp_path)

    hits = _search_python(tmp_path.resolve(), tmp_path.resolve(), "import")

    assert ("a.py", 1, "import os") in hits
    assert ("b.py", 2, "import sys") in hits
    assert all(".git" not in h[0] for h in hits)


def test_grep_tool_is_safe_level():
    assert GrepTool.danger is Danger.SAFE


# ---- ripgrep 分支：本机无 rg 二进制，故把解析逻辑抽成纯函数单独测 ----

def test_parses_real_ripgrep_output_format(tmp_path):
    """rg --line-number --no-heading 的真实输出形如 <绝对路径>:<行号>:<内容>。"""
    from workpilot.tools.search import _parse_rg_output

    base = tmp_path.resolve()
    stdout = (f"{base}/a.py:1:import os\n"
              f"{base}/src/b.py:12:    import sys\n")

    hits = _parse_rg_output(stdout, base)

    assert hits == [("a.py", 1, "import os"),
                    ("src/b.py", 12, "    import sys")]


def test_rg_parser_handles_colons_inside_the_matched_line(tmp_path):
    """行内容本身含冒号时，只能按前两个冒号切分。"""
    from workpilot.tools.search import _parse_rg_output

    base = tmp_path.resolve()
    stdout = f"{base}/a.py:7:d = {{'k': 'v:1'}}\n"

    hits = _parse_rg_output(stdout, base)

    assert hits == [("a.py", 7, "d = {'k': 'v:1'}")]


def test_rg_parser_skips_malformed_lines(tmp_path):
    from workpilot.tools.search import _parse_rg_output

    base = tmp_path.resolve()
    stdout = f"garbage-without-colons\n{base}/a.py:3:ok\n"

    hits = _parse_rg_output(stdout, base)

    assert hits == [("a.py", 3, "ok")]
