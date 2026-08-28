"""OpenAI 兼容 Provider —— 可接 Moonshot / DeepSeek / Qwen / Ollama。

内部消息格式统一采用 Anthropic 的 content-block 结构，
这里负责双向翻译（block 信息量更大，反向选型会丢 thinking 块）。
"""
import json
from dataclasses import dataclass
from typing import Iterator

from workpilot.providers.base import Chunk, ToolCall


class OpenAIProvider:
    context_limit = 128_000

    def __init__(self, model: str, client=None,
                 api_key: str | None = None, base_url: str | None = None):
        self.model = model
        if client is not None:
            self.client = client
        else:
            import openai
            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def stream(self, system: str, messages: list,
               tools: list) -> Iterator[Chunk]:
        kwargs = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}]
                     + self._to_openai_messages(messages),
            stream=True,
        )
        if tools:
            kwargs["tools"] = [self._to_openai_tool(t) for t in tools]

        # index -> 累积中的工具调用（arguments 是分片到达的字符串）
        pending: dict[int, dict] = {}
        text_parts: list[str] = []
        finish_reason = ""
        usage = None

        for event in self.client.chat.completions.create(**kwargs):
            if not event.choices:
                continue
            choice = event.choices[0]
            delta = choice.delta

            if getattr(delta, "content", None):
                text_parts.append(delta.content)
                yield Chunk("text", text=delta.content)

            # Kimi / DeepSeek 等把思考流放在 reasoning_content 里
            if getattr(delta, "reasoning_content", None):
                yield Chunk("thinking", text=delta.reasoning_content)

            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = pending.setdefault(tc.index, {"id": None, "name": None,
                                                     "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

            # usage 的位置不统一：标准 OpenAI 在顶层，
            # Kimi 放在 choices[0] 里 —— 两处都要看
            usage = (getattr(choice, "usage", None)
                     or (getattr(choice, "model_extra", None) or {}).get("usage")
                     or getattr(event, "usage", None) or usage)

        blocks: list = []
        if text_parts:
            blocks.append({"type": "text", "text": "".join(text_parts)})

        for _, slot in sorted(pending.items()):
            # 分片拼完后整体解析 —— 逐片解析必然失败
            args = json.loads(slot["args"]) if slot["args"].strip() else {}
            blocks.append({"type": "tool_use", "id": slot["id"],
                           "name": slot["name"], "input": args})
            yield Chunk("tool_call", tool_call=ToolCall(
                id=slot["id"], name=slot["name"], args=args))

        yield Chunk("done", blocks=blocks, usage=_normalize_usage(usage),
                    stop_reason="tool_use" if pending else "end_turn")

    def count_tokens(self, system: str, messages: list, tools: list) -> int:
        # 无 count_tokens 接口，粗略估算即可（只用于压缩触发判断）
        return len(json.dumps([system, messages, tools],
                              ensure_ascii=False)) // 3

    @staticmethod
    def _to_openai_tool(schema: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "parameters": schema["input_schema"],
            },
        }

    @staticmethod
    def _to_openai_messages(messages: list) -> list[dict]:
        """Anthropic content-block → OpenAI 消息格式。"""
        out: list[dict] = []
        for msg in messages:
            content = msg["content"]

            if isinstance(content, str):
                out.append({"role": msg["role"], "content": content})
                continue

            if msg["role"] == "assistant":
                text = "".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text")
                calls = [{
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"],
                                 "arguments": json.dumps(b["input"],
                                                         ensure_ascii=False)},
                } for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"]
                entry: dict = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
                continue

            # user 消息里装的是 tool_result → 摊成多条 tool 角色消息
            results = [b for b in content
                       if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                for b in results:
                    out.append({"role": "tool",
                                "tool_call_id": b["tool_use_id"],
                                "content": str(b["content"])})
            else:
                text = "".join(b.get("text", "") for b in content
                               if isinstance(b, dict))
                out.append({"role": "user", "content": text})
        return out


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0


def _normalize_usage(raw) -> _Usage:
    """把 OpenAI 风格的 usage 字段名映射成内部统一命名。

    raw 可能是对象，也可能是 dict —— SDK 把非标准字段塞进 model_extra
    时就是 dict（Kimi 的 choice.usage 即如此）。
    """
    if raw is None:
        return _Usage()

    def pick(key: str) -> int:
        value = (raw.get(key) if isinstance(raw, dict)
                 else getattr(raw, key, 0))
        return value or 0

    return _Usage(
        input_tokens=pick("prompt_tokens"),
        output_tokens=pick("completion_tokens"),
        cache_read_input_tokens=pick("cached_tokens"),
    )
