"""Anthropic Provider —— 默认实现。"""
from typing import Iterator

from workpilot.providers.base import Chunk, ToolCall

MODEL = "claude-opus-5"
MAX_TOKENS = 64000


class AnthropicProvider:
    context_limit = 1_000_000

    def __init__(self, client=None, api_key: str | None = None):
        if client is not None:
            self.client = client
        else:
            import anthropic
            # 不传 api_key 时 SDK 自行解析 ANTHROPIC_API_KEY / ant auth 的 profile
            self.client = (anthropic.Anthropic(api_key=api_key) if api_key
                           else anthropic.Anthropic())

    def stream(self, system: str, messages: list,
               tools: list) -> Iterator[Chunk]:
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # 自适应思考：display 必须显式开启，否则默认 omitted，
            # thinking 文本为空，UI 上表现为长时间无输出
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "xhigh"},
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=tools,
            messages=messages,
        ) as stream:
            for event in stream:
                if event.type != "content_block_delta":
                    continue
                if event.delta.type == "text_delta":
                    yield Chunk("text", text=event.delta.text)
                elif event.delta.type == "thinking_delta":
                    yield Chunk("thinking", text=event.delta.thinking)

            final = stream.get_final_message()

        for block in final.content:
            if block.type == "tool_use":
                yield Chunk("tool_call", tool_call=ToolCall(
                    id=block.id, name=block.name, args=block.input,
                ))

        yield Chunk("done", blocks=final.content, usage=final.usage,
                    stop_reason=final.stop_reason)

    def count_tokens(self, system: str, messages: list, tools: list) -> int:
        return self.client.messages.count_tokens(
            model=MODEL, system=system, tools=tools, messages=messages,
        ).input_tokens
