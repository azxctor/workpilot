"""事件协议 —— 内核与所有消费者之间的唯一接口。"""
from dataclasses import dataclass
from enum import Enum


class Danger(Enum):
    """工具的危险等级，决定是否需要人工确认。"""
    SAFE = 0          # 只读，直接放行
    CONFIRM = 1       # 有副作用，问一次
    DESTRUCTIVE = 2   # 不可逆，每次都问


@dataclass
class TextDelta:
    """模型正文的流式增量。"""
    text: str


@dataclass
class ThinkingDelta:
    """思考摘要的流式增量。"""
    text: str


@dataclass
class ToolCallRequest:
    """唯一需要消费者回灌决定的事件：send('allow' | 'deny')。"""
    id: str
    name: str
    args: dict
    danger: Danger


@dataclass
class ToolResult:
    id: str
    name: str
    output: str
    is_error: bool = False


@dataclass
class ContextCompacted:
    freed_tokens: int
    kept_turns: int


@dataclass
class TurnEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


Event = (TextDelta | ThinkingDelta | ToolCallRequest
         | ToolResult | ContextCompacted | TurnEnd)
