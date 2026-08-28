"""端到端烟测：除 HTTP 之外全用真实组件。

模拟 M1 的验收场景 —— 用户问「这个项目是干嘛的」，
模型自己决定读 README，再基于内容回答。
"""
import io

from fakes import FakeProvider, FakeUsage, text_turn
from workpilot.agent.loop import AgentLoop
from workpilot.cli import approve_safe_only, drive
from workpilot.config import build_system_prompt
from workpilot.providers.base import Chunk, ToolCall
from workpilot.tools.base import Registry
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

    drive(loop, "这个项目是干嘛的？", Renderer(out=buf, width=200),
          approve_safe_only)

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
