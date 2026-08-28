"""测试替身：按脚本吐 chunk 的假 Provider。

内核测试完全不碰真实 API —— 这正是「内核不依赖外部 I/O」这条设计的回报。
"""
from dataclasses import dataclass

from workpilot.providers.base import Chunk, ToolCall


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 20
    cache_read_input_tokens: int = 0


class FakeProvider:
    """script 是「每次请求依次返回的 chunk 列表」的列表。"""

    context_limit = 1_000_000

    def __init__(self, script: list[list[Chunk]]):
        self.script = script
        self.calls: list[list[dict]] = []   # 记录每次收到的 messages，供断言

    def stream(self, system, messages, tools):
        self.calls.append([dict(m) for m in messages])
        yield from self.script.pop(0)

    def count_tokens(self, system, messages, tools) -> int:
        return 100


def text_turn(text: str) -> list[Chunk]:
    """一个只说话、不调工具的回合。"""
    return [
        Chunk("text", text=text),
        Chunk("done",
              blocks=[{"type": "text", "text": text}],
              usage=FakeUsage(), stop_reason="end_turn"),
    ]


def tool_turn(tool_id: str, name: str, args: dict) -> list[Chunk]:
    """一个发起工具调用的回合。"""
    call = ToolCall(id=tool_id, name=name, args=args)
    return [
        Chunk("tool_call", tool_call=call),
        Chunk("done",
              blocks=[{"type": "tool_use", "id": tool_id,
                       "name": name, "input": args}],
              usage=FakeUsage(), stop_reason="tool_use"),
    ]
