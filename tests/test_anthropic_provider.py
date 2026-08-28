"""AnthropicProvider 的转换逻辑测试：SDK 事件 → 归一化 Chunk。

不打真实 API —— 注入一个假 client，只验证「差异磨平」这件事做对了。
"""
from types import SimpleNamespace

from workpilot.providers.anthropic_provider import AnthropicProvider


class FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self._events)
    def get_final_message(self): return self._final


class FakeMessages:
    def __init__(self, stream):
        self._stream, self.last_kwargs = stream, None

    def stream(self, **kwargs):
        self.last_kwargs = kwargs
        return self._stream


class FakeClient:
    def __init__(self, stream):
        self.messages = FakeMessages(stream)


def delta(kind, value):
    field = "text" if kind == "text_delta" else "thinking"
    return SimpleNamespace(type="content_block_delta",
                           delta=SimpleNamespace(**{"type": kind, field: value}))


def build(events, blocks, stop_reason="end_turn"):
    final = SimpleNamespace(
        content=blocks, stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=5, output_tokens=7,
                              cache_read_input_tokens=3),
    )
    client = FakeClient(FakeStream(events, final))
    return AnthropicProvider(client=client), client


def test_text_deltas_become_text_chunks():
    provider, _ = build([delta("text_delta", "你"), delta("text_delta", "好")],
                        blocks=[SimpleNamespace(type="text", text="你好")])

    chunks = list(provider.stream(system="s", messages=[], tools=[]))

    assert [c.text for c in chunks if c.kind == "text"] == ["你", "好"]


def test_thinking_deltas_become_thinking_chunks():
    provider, _ = build([delta("thinking_delta", "让我想想")],
                        blocks=[SimpleNamespace(type="text", text="")])

    chunks = list(provider.stream(system="s", messages=[], tools=[]))

    assert [c.text for c in chunks if c.kind == "thinking"] == ["让我想想"]


def test_tool_use_block_becomes_tool_call_chunk():
    block = SimpleNamespace(type="tool_use", id="t1", name="read_file",
                            input={"path": "a.py"})
    provider, _ = build([], blocks=[block], stop_reason="tool_use")

    chunks = list(provider.stream(system="s", messages=[], tools=[]))

    call = next(c.tool_call for c in chunks if c.kind == "tool_call")
    assert (call.id, call.name, call.args) == ("t1", "read_file", {"path": "a.py"})


def test_done_chunk_carries_blocks_usage_and_stop_reason():
    blocks = [SimpleNamespace(type="text", text="hi")]
    provider, _ = build([], blocks=blocks)

    done = list(provider.stream(system="s", messages=[], tools=[]))[-1]

    assert done.kind == "done"
    assert done.blocks is blocks
    assert done.stop_reason == "end_turn"
    assert done.usage.cache_read_input_tokens == 3


def test_system_prompt_is_sent_with_cache_control():
    """system 段必须打 cache_control，否则每轮都全价重发。"""
    provider, client = build([], blocks=[SimpleNamespace(type="text", text="")])

    list(provider.stream(system="SYS", messages=[], tools=[]))

    sent = client.messages.last_kwargs["system"]
    assert sent[0]["text"] == "SYS"
    assert sent[0]["cache_control"] == {"type": "ephemeral"}


def test_request_uses_adaptive_thinking_and_no_budget_tokens():
    """budget_tokens 在 Opus 5 上会 400，必须用 adaptive。"""
    provider, client = build([], blocks=[SimpleNamespace(type="text", text="")])

    list(provider.stream(system="s", messages=[], tools=[]))

    kwargs = client.messages.last_kwargs
    assert kwargs["thinking"]["type"] == "adaptive"
    assert "budget_tokens" not in kwargs["thinking"]
    assert kwargs["model"] == "claude-opus-5"
