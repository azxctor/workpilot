"""CLI 入口：把内核、渲染、权限确认装配到一起。"""
import argparse
import os
import sys
from pathlib import Path

from workpilot.agent.events import ToolCallRequest
from workpilot.agent.loop import AgentLoop
from workpilot.config import build_provider, build_system_prompt
from workpilot.tools.base import build_default_registry
from workpilot.ui.confirm import Approver
from workpilot.ui.renderer import Renderer


def drive(loop, user_input: str, renderer, approve) -> None:
    """驱动一轮对话：内核吐事件 → 渲染 → 遇到请求就问 → 把答案送回内核。

    这十几行就是整个系统的装配点。
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="workpilot",
                                     description="终端 AI 编码助手")
    parser.add_argument("--yolo", action="store_true",
                        help="跳过所有确认，自动放行全部工具调用")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    workspace = Path.cwd()

    loop = AgentLoop(
        provider=build_provider(),
        registry=build_default_registry(workspace),
        ctx_manager=None,
        system_prompt=build_system_prompt(workspace),
        workspace=workspace,
    )
    renderer = Renderer()
    approver = Approver(yolo=args.yolo)

    model = os.environ.get("WORKPILOT_MODEL", "claude-opus-5")
    renderer.console.print(
        f"[bold]WorkPilot[/bold] [dim]{workspace}[/dim]  [dim]({model})[/dim]")
    if args.yolo:
        renderer.console.print("[red]--yolo 已启用：所有工具调用将自动放行[/red]")
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
            drive(loop, raw, renderer, approver.ask)
        except KeyboardInterrupt:
            renderer.console.print("\n[yellow]已中断[/yellow]")
        except Exception as e:
            renderer.console.print(f"\n[red]错误: {type(e).__name__}: {e}[/red]")


if __name__ == "__main__":
    main()
