import io

from workpilot.agent.events import (Danger, TextDelta, ToolCallRequest,
                                    ToolResult, TurnEnd)
from workpilot.ui.renderer import Renderer


def make():
    buf = io.StringIO()
    return Renderer(out=buf, width=200), buf


def test_text_delta_is_written_through_without_newline():
    r, buf = make()

    r.handle(TextDelta("你好"))
    r.handle(TextDelta("世界"))

    assert buf.getvalue() == "你好世界"


def test_tool_call_request_shows_tool_name_and_args():
    r, buf = make()

    r.handle(ToolCallRequest(id="t1", name="read_file",
                             args={"path": "main.py"}, danger=Danger.SAFE))

    out = buf.getvalue()
    assert "read_file" in out
    assert "main.py" in out


def test_failed_tool_result_is_marked_as_error():
    r, buf = make()

    r.handle(ToolResult(id="t1", name="read_file",
                        output="FileNotFoundError: nope.txt", is_error=True))

    out = buf.getvalue()
    assert "FileNotFoundError" in out
    assert "✗" in out


def test_turn_end_reports_token_usage():
    r, buf = make()

    r.handle(TurnEnd(stop_reason="end_turn", input_tokens=1200,
                     output_tokens=340, cache_read_tokens=900))

    out = buf.getvalue()
    assert "1200" in out and "340" in out
    assert "900" in out          # 缓存命中要可见，否则没法诊断缓存失效
