"""Provider 抽象层：把各家 API 的差异磨平成统一的 Chunk 流。

agent/loop.py 只认识这里的 Chunk 和 ToolCall，看不到任何厂商 SDK。
"""
from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Chunk:
    """归一化的流式片段。kind: text | thinking | tool_call | done"""
    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    blocks: list | None = None      # kind=done 时携带完整 content blocks
    usage: object | None = None
    stop_reason: str = ""


class LLMClient(Protocol):
    context_limit: int

    def stream(self, system: str, messages: list,
               tools: list) -> Iterator[Chunk]: ...

    def count_tokens(self, system: str, messages: list, tools: list) -> int: ...
