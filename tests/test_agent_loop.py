import pytest
from fakes import FakeProvider, text_turn, tool_turn

from workpilot.agent.events import (ContextCompacted, Danger, TextDelta,
                                    ToolCallRequest, ToolResult, TurnEnd)
from workpilot.agent.loop import AgentLoop
from workpilot.tools.base import Registry


class SpyTool:
    """记录自己被调用了几次的假工具。"""
    name = "spy"
    danger = Danger.SAFE
    schema = {"name": "spy", "description": "d",
              "input_schema": {"type": "object", "properties": {},
                               "required": []}}

    def __init__(self, result="spy-result", raises=None):
        self.result, self.raises, self.call_count = result, raises, 0

    def run(self, **kwargs):
        self.call_count += 1
        if self.raises:
            raise self.raises
        return self.result


class NoCompact:
    """不做任何压缩的 ContextManager 替身。"""
    def should_compact(self, history): return False
    def compact(self, history): raise AssertionError("不该被调用")


def build(script, tools=None, ctx=None):
    provider = FakeProvider(script)
    loop = AgentLoop(
        provider=provider,
        registry=Registry(tools or []),
        ctx_manager=ctx or NoCompact(),
        system_prompt="sys",
        workspace=".",
    )
    return loop, provider


def drain(loop, user_input, decide=lambda req: "allow"):
    """把生成器跑干，收集所有事件；遇到 ToolCallRequest 用 decide 回灌决定。"""
    events, gen, decision = [], loop.run_turn(user_input), None
    while True:
        try:
            ev = gen.send(decision)
        except StopIteration:
            return events
        decision = None
        events.append(ev)
        if isinstance(ev, ToolCallRequest):
            decision = decide(ev)


def test_turn_without_tool_calls_yields_text_then_ends():
    loop, _ = build([text_turn("你好")])

    events = drain(loop, "hi")

    assert isinstance(events[0], TextDelta)
    assert events[0].text == "你好"
    assert isinstance(events[-1], TurnEnd)
    assert events[-1].stop_reason == "end_turn"


def test_approved_tool_call_runs_and_feeds_result_back():
    spy = SpyTool(result="42")
    loop, provider = build(
        [tool_turn("t1", "spy", {"q": "x"}), text_turn("答案是 42")],
        tools=[spy],
    )

    events = drain(loop, "算一下")

    req = next(e for e in events if isinstance(e, ToolCallRequest))
    assert req.name == "spy" and req.args == {"q": "x"}
    assert req.danger is Danger.SAFE

    res = next(e for e in events if isinstance(e, ToolResult))
    assert res.output == "42" and res.is_error is False
    assert spy.call_count == 1

    # 第二次请求必须带上 tool_result，且打包在同一条 user 消息里
    second_request = provider.calls[1]
    last = second_request[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "t1"


def test_denied_tool_call_is_not_executed():
    spy = SpyTool()
    loop, _ = build([tool_turn("t1", "spy", {}), text_turn("好的")], tools=[spy])

    events = drain(loop, "干活", decide=lambda req: "deny")

    assert spy.call_count == 0
    res = next(e for e in events if isinstance(e, ToolResult))
    assert res.is_error is True
    assert "declined" in res.output.lower()


def test_failing_tool_returns_error_result_instead_of_crashing():
    """工具抛异常也必须回一条 tool_result —— 丢掉会让配对断裂，下次请求 400。"""
    spy = SpyTool(raises=FileNotFoundError("nope.txt"))
    loop, provider = build([tool_turn("t1", "spy", {}), text_turn("文件不存在")],
                           tools=[spy])

    events = drain(loop, "读文件")

    res = next(e for e in events if isinstance(e, ToolResult))
    assert res.is_error is True
    assert "FileNotFoundError" in res.output

    # 关键：错误结果照样进了历史，配对完整
    last = provider.calls[1][-1]
    assert last["content"][0]["tool_use_id"] == "t1"
    assert last["content"][0]["is_error"] is True


def test_parallel_tool_calls_pack_all_results_into_one_message():
    """拆成多条 user 消息会静默地让模型以后不再发起并行调用。"""
    from fakes import FakeUsage
    from workpilot.providers.base import Chunk, ToolCall

    spy = SpyTool(result="ok")
    two_calls = [
        Chunk("tool_call", tool_call=ToolCall("t1", "spy", {"n": 1})),
        Chunk("tool_call", tool_call=ToolCall("t2", "spy", {"n": 2})),
        Chunk("done", blocks=[], usage=FakeUsage(), stop_reason="tool_use"),
    ]
    loop, provider = build([two_calls, text_turn("都做完了")], tools=[spy])

    events = drain(loop, "并行干两件事")

    assert spy.call_count == 2
    assert len([e for e in events if isinstance(e, ToolResult)]) == 2

    last = provider.calls[1][-1]
    assert last["role"] == "user"
    assert len(last["content"]) == 2                      # 同一条消息里两个结果
    assert [c["tool_use_id"] for c in last["content"]] == ["t1", "t2"]
