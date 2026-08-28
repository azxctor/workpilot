"""端到端烟测：除 HTTP 之外全用真实组件。

模拟 M1 的验收场景 —— 用户问「这个项目是干嘛的」，
模型自己决定读 README，再基于内容回答。
"""
import io

from fakes import FakeProvider, FakeUsage, text_turn
from workpilot.agent.loop import AgentLoop
from workpilot.cli import drive
from workpilot.ui.confirm import Approver
from workpilot.config import build_system_prompt
from workpilot.providers.base import Chunk, ToolCall
from workpilot.tools.base import Registry, build_default_registry
from workpilot.tools.fs import ReadFileTool
from workpilot.ui.renderer import Renderer


def test_model_reads_a_file_then_answers(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Calculator\n一个命令行计算器。", encoding="utf-8")

    read_tool = ReadFileTool(workspace=tmp_path)
    provider = FakeProvider([
        # 第 1 回合：模型决定读 README
        [Chunk("tool_call",
               tool_call=ToolCall("t1", "read_file", {"path": "README.md"})),
         Chunk("done", blocks=[], usage=FakeUsage(), stop_reason="tool_use")],
        # 第 2 回合：基于读到的内容回答
        text_turn("这是一个命令行计算器项目。"),
    ])
    loop = AgentLoop(
        provider=provider,
        registry=Registry([read_tool]),
        ctx_manager=None,
        system_prompt=build_system_prompt(tmp_path),
        workspace=tmp_path,
    )
    buf = io.StringIO()

    approver = Approver(out=buf, is_tty=False)   # SAFE 工具无需 TTY
    drive(loop, "这个项目是干嘛的？", Renderer(out=buf, width=200),
          approver.ask)

    out = buf.getvalue()
    # 工具被调用并渲染出来
    assert "read_file" in out and "README.md" in out
    # 模型最终给出了答案
    assert "命令行计算器" in out

    # 关键：真实的文件内容确实回到了模型手里
    second_request = provider.calls[1]
    tool_result = second_request[-1]["content"][0]
    assert "Calculator" in tool_result["content"]
    assert tool_result["is_error"] is False


def test_model_edits_a_file_after_user_approves(tmp_path):
    """M2 验收场景：模型读文件 → 发起 edit_file → 用户批准 → 落盘。"""
    target = tmp_path / "calc.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    provider = FakeProvider([
        [Chunk("tool_call", tool_call=ToolCall(
            "t1", "read_file", {"path": "calc.py"})),
         Chunk("done", blocks=[], usage=FakeUsage(), stop_reason="tool_use")],
        [Chunk("tool_call", tool_call=ToolCall("t2", "edit_file", {
            "path": "calc.py",
            "old_string": "return a + b",
            "new_string": "return round(a + b, 2)"})),
         Chunk("done", blocks=[], usage=FakeUsage(), stop_reason="tool_use")],
        text_turn("已加上四舍五入。"),
    ])
    loop = AgentLoop(
        provider=provider,
        registry=build_default_registry(tmp_path),
        ctx_manager=None,
        system_prompt=build_system_prompt(tmp_path),
        workspace=tmp_path,
    )
    buf = io.StringIO()
    approver = Approver(out=buf, is_tty=True, ask_line=lambda _: "y")

    drive(loop, "给 add 加上四舍五入", Renderer(out=buf, width=200),
          approver.ask)

    # 确认界面展示了正负两侧
    assert "return a + b" in buf.getvalue()
    assert "round(a + b, 2)" in buf.getvalue()
    # 文件真的改了
    assert target.read_text(encoding="utf-8") == \
        "def add(a, b):\n    return round(a + b, 2)\n"


def test_denied_edit_leaves_file_untouched(tmp_path):
    target = tmp_path / "calc.py"
    original = "def add(a, b):\n    return a + b\n"
    target.write_text(original, encoding="utf-8")

    provider = FakeProvider([
        [Chunk("tool_call", tool_call=ToolCall("t1", "edit_file", {
            "path": "calc.py", "old_string": "a + b", "new_string": "a * b"})),
         Chunk("done", blocks=[], usage=FakeUsage(), stop_reason="tool_use")],
        text_turn("好的，我不改了。"),
    ])
    loop = AgentLoop(
        provider=provider, registry=build_default_registry(tmp_path),
        ctx_manager=None, system_prompt="s", workspace=tmp_path,
    )
    buf = io.StringIO()
    approver = Approver(out=buf, is_tty=True, ask_line=lambda _: "n")

    drive(loop, "把加号改成乘号", Renderer(out=buf, width=200), approver.ask)

    assert target.read_text(encoding="utf-8") == original
