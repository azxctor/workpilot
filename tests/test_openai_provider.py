"""OpenAI 兼容 Provider 的转换测试。

难点在两处：工具 schema 要包一层 function，
以及 tool_call 的 arguments 是【分片的字符串】，必须拼接后再 json.loads。
"""
from types import SimpleNamespace

from workpilot.providers.openai_provider import OpenAIProvider


def chunk(content=None, tool_calls=None, finish_reason=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content, tool_calls=tool_calls),
        finish_reason=finish_reason,
    )], usage=None)


def tc(index, call_id=None, name=None, args=""):
    return SimpleNamespace(index=index, id=call_id,
                           function=SimpleNamespace(name=name, arguments=args))


class FakeCompletions:
    def __init__(self, chunks): self._chunks, self.last_kwargs = chunks, None
    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return iter(self._chunks)


class FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=FakeCompletions(chunks))


def build(chunks):
    client = FakeClient(chunks)
    return OpenAIProvider(model="kimi-k2", client=client), client


def test_content_deltas_become_text_chunks():
    provider, _ = build([chunk(content="你"), chunk(content="好"),
                         chunk(finish_reason="stop")])

    out = list(provider.stream(system="s", messages=[], tools=[]))

    assert [c.text for c in out if c.kind == "text"] == ["你", "好"]


def test_fragmented_tool_arguments_are_joined_then_parsed():
    """arguments 分三片到达，必须拼接后整体解析。"""
    provider, _ = build([
        chunk(tool_calls=[tc(0, "c1", "read_file", '{"pa')]),
        chunk(tool_calls=[tc(0, None, None, 'th": "ma')]),
        chunk(tool_calls=[tc(0, None, None, 'in.py"}')]),
        chunk(finish_reason="tool_calls"),
    ])

    out = list(provider.stream(system="s", messages=[], tools=[]))

    call = next(c.tool_call for c in out if c.kind == "tool_call")
    assert call.id == "c1"
    assert call.name == "read_file"
    assert call.args == {"path": "main.py"}


def test_tool_schema_is_wrapped_in_function_envelope():
    provider, client = build([chunk(finish_reason="stop")])
    anthropic_schema = {
        "name": "read_file",
        "description": "读文件",
        "input_schema": {"type": "object",
                         "properties": {"path": {"type": "string"}},
                         "required": ["path"]},
    }

    list(provider.stream(system="s", messages=[], tools=[anthropic_schema]))

    sent = client.chat.completions.last_kwargs["tools"][0]
    assert sent["type"] == "function"
    assert sent["function"]["name"] == "read_file"
    assert sent["function"]["parameters"]["required"] == ["path"]


def test_system_prompt_becomes_first_system_message():
    provider, client = build([chunk(finish_reason="stop")])

    list(provider.stream(system="SYS",
                         messages=[{"role": "user", "content": "hi"}], tools=[]))

    sent = client.chat.completions.last_kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "SYS"}
    assert sent[1]["content"] == "hi"


def test_anthropic_tool_result_message_converts_to_tool_role_messages():
    """内部格式是 Anthropic 的 content-block，出站要翻成 OpenAI 的 tool 角色。"""
    provider, client = build([chunk(finish_reason="stop")])
    history = [
        {"role": "user", "content": "读一下"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "read_file",
             "input": {"path": "a.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1",
             "content": "1\timport os", "is_error": False}]},
    ]

    list(provider.stream(system="s", messages=history, tools=[]))

    sent = client.chat.completions.last_kwargs["messages"]
    assistant_msg = sent[2]
    assert assistant_msg["tool_calls"][0]["id"] == "c1"
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "read_file"

    tool_msg = sent[3]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    assert "import os" in tool_msg["content"]
