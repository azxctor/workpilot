"""CLI 入口：把内核、渲染、权限确认装配到一起。"""
import sys
from pathlib import Path

from workpilot.agent.events import Danger, ToolCallRequest
from workpilot.agent.loop import AgentLoop
from workpilot.config import build_system_prompt
from workpilot.providers.anthropic_provider import AnthropicProvider
from workpilot.tools.base import Registry
from workpilot.tools.fs import ReadFileTool
from workpilot.ui.renderer import Renderer


def drive(loop, user_input: str, renderer, approve) -> None:
    """驱动一轮对话：内核吐事件 → 渲染 → 遇到请求就问 → 把答案送回内核。

    这 15 行就是整个系统的装配点。
    """
    gen = loop.run_turn(user_input)
    decision = None
    while True:
        try:
            event = gen.send(decision)
        except StopIteration:
            return
        decision = None
        renderer.handle(event)
        if isinstance(event, ToolCallRequest):
            decision = approve(event)


def approve_safe_only(req: ToolCallRequest) -> str:
    """M1 的权限策略：只读工具直接放行，其余一律拒绝。

    M2 会换成带交互确认的 Approver。
    """
    return "allow" if req.danger is Danger.SAFE else "deny"


def main() -> None:
    workspace = Path.cwd()
    loop = AgentLoop(
        provider=AnthropicProvider(),
        registry=Registry([ReadFileTool(workspace=workspace)]),
        ctx_manager=None,
        system_prompt=build_system_prompt(workspace),
        workspace=workspace,
    )
    renderer = Renderer()

    renderer.console.print(f"[bold]WorkPilot[/bold] [dim]{workspace}[/dim]")
    renderer.console.print("[dim]输入 /exit 退出[/dim]")

    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue
        if raw in ("/exit", "/quit"):
            return
        try:
            drive(loop, raw, renderer, approve_safe_only)
        except KeyboardInterrupt:
            renderer.console.print("\n[yellow]已中断[/yellow]")
        except Exception as e:
            renderer.console.print(f"\n[red]错误: {type(e).__name__}: {e}[/red]")


if __name__ == "__main__":
    main()
