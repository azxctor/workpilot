"""Agent Loop 内核。

铁律：这个文件不 import ui/，不 print，不认识任何厂商 SDK。
它只 yield 事件，由消费者决定怎么渲染、落盘、以及是否放行工具。
"""
from typing import Iterator

from workpilot.agent.events import (Event, TextDelta, ThinkingDelta,
                                    ToolCallRequest, ToolResult, TurnEnd)


class AgentLoop:
    def __init__(self, provider, registry, ctx_manager,
                 system_prompt: str, workspace):
        self.provider = provider
        self.registry = registry
        self.ctx = ctx_manager
        self.system = system_prompt
        self.workspace = workspace
        self.history: list[dict] = []

    def run_turn(self, user_input: str) -> Iterator[Event]:
        """跑完一整轮对话（可能包含多次工具调用）。

        这是一个双向生成器：遇到 ToolCallRequest 时暂停，
        等消费者 send("allow" | "deny") 回灌决定后继续。
        """
        self.history.append({"role": "user", "content": user_input})

        while True:
            blocks: list = []
            tool_calls = []
            usage, stop_reason = None, ""

            for chunk in self.provider.stream(
                system=self.system,
                messages=self.history,
                tools=self.registry.schemas(),
            ):
                if chunk.kind == "text":
                    yield TextDelta(chunk.text)
                elif chunk.kind == "thinking":
                    yield ThinkingDelta(chunk.text)
                elif chunk.kind == "tool_call":
                    tool_calls.append(chunk.tool_call)
                elif chunk.kind == "done":
                    blocks = chunk.blocks
                    usage, stop_reason = chunk.usage, chunk.stop_reason

            self.history.append({"role": "assistant", "content": blocks})

            if not tool_calls:
                yield TurnEnd(
                    stop_reason=stop_reason,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens,
                )
                return

            results = []
            for call in tool_calls:
                tool = self.registry.get(call.name)

                decision = yield ToolCallRequest(
                    id=call.id, name=call.name,
                    args=call.args, danger=tool.danger,
                )

                if decision == "deny":
                    output, is_error = "User declined this operation.", True
                else:
                    try:
                        output, is_error = tool.run(**call.args), False
                    except Exception as e:
                        # 失败也必须回一条结果 —— 丢掉会让 tool_use/tool_result
                        # 配对断裂，下一次请求直接 400
                        output, is_error = f"{type(e).__name__}: {e}", True

                yield ToolResult(call.id, call.name, output, is_error)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": output,
                    "is_error": is_error,
                })

            # 所有 tool_result 必须打包进同一条 user 消息，
            # 拆开会让模型以后不再发起并行工具调用
            self.history.append({"role": "user", "content": results})
