"""CLI 入口：把内核、渲染、权限确认、会话持久化装配到一起。"""
import argparse
import os
from pathlib import Path

from workpilot.agent.events import ToolCallRequest
from workpilot.agent.loop import AgentLoop
from workpilot.config import build_provider, build_system_prompt
from workpilot.session.store import (SessionStore, find_by_id, find_latest,
                                     list_sessions, repair_dangling_tool_use)
from workpilot.tools.base import build_default_registry
from workpilot.ui.confirm import Approver
from workpilot.ui.renderer import Renderer


def sessions_dir() -> Path:
    return Path.home() / ".workpilot" / "sessions"


def drive(loop, user_input: str, renderer, approve, store=None) -> None:
    """驱动一轮对话：内核吐事件 → 渲染 → 遇到请求就问 → 把答案送回内核。

    store 不为 None 时，每转一圈把 history 的增量追加落盘。
    内核不知道存储的存在 —— 落盘发生在这里，而不是回调进内核。
    """
    gen = loop.run_turn(user_input)
    decision = None
    synced = 0 if store is None else len(store.load_history())
    while True:
        try:
            event = gen.send(decision)
        except StopIteration:
            break
        decision = None
        renderer.handle(event)
        if isinstance(event, ToolCallRequest):
            decision = approve(event)
        if store is not None:
            synced = store.sync(loop.history, synced)

    if store is not None:
        store.sync(loop.history, synced)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="workpilot",
                                     description="终端 AI 编码助手")
    parser.add_argument("--yolo", action="store_true",
                        help="跳过所有确认，自动放行全部工具调用")
    parser.add_argument("--sessions", action="store_true",
                        help="列出当前目录的历史会话")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--continue", dest="continue_session",
                       action="store_true",
                       help="恢复当前目录最近一次会话")
    group.add_argument("--resume", metavar="ID",
                       help="恢复指定 id 的会话")
    return parser.parse_args(argv)


def _print_sessions(console, workspace) -> None:
    items = list_sessions(sessions_dir(), workspace)
    if not items:
        console.print("[dim]该目录没有历史会话。[/dim]")
        return
    for item in items:
        summary = item["summary"][:50] or "（无提问）"
        console.print(f"[cyan]{item['session_id']}[/cyan]  "
                      f"[dim]{item['created_at'][:19]}[/dim]  {summary}")


def _open_session(args, console, workspace, model):
    """返回 (store, 恢复出的 history)。新会话时 history 为空。"""
    path = None
    if args.resume:
        path = find_by_id(sessions_dir(), args.resume)
        if path is None:
            console.print(f"[red]找不到会话 {args.resume}[/red]")
            _print_sessions(console, workspace)
            raise SystemExit(1)
    elif args.continue_session:
        path = find_latest(sessions_dir(), workspace)
        if path is None:
            console.print("[dim]该目录没有历史会话，开始新会话。[/dim]")

    if path is None:
        return SessionStore.create(sessions_dir(), workspace, model), []

    store = SessionStore.open(path)
    history, dropped = repair_dangling_tool_use(store.load_history())
    if dropped:
        console.print("[yellow]上次有一轮未完成，已丢弃该回合。[/yellow]")
    console.print(f"[dim]已恢复会话 {store.session_id}"
                  f"（{len(history)} 条消息）[/dim]")
    return store, history


def main() -> None:
    args = parse_args()
    workspace = Path.cwd()
    renderer = Renderer()

    if args.sessions:
        _print_sessions(renderer.console, workspace)
        return

    model = os.environ.get("WORKPILOT_MODEL", "claude-opus-5")
    store, history = _open_session(args, renderer.console, workspace, model)

    loop = AgentLoop(
        provider=build_provider(),
        registry=build_default_registry(workspace),
        ctx_manager=None,
        system_prompt=build_system_prompt(workspace),
        workspace=workspace,
    )
    loop.history = history
    approver = Approver(yolo=args.yolo)

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
            drive(loop, raw, renderer, approver.ask, store=store)
        except KeyboardInterrupt:
            renderer.console.print("\n[yellow]已中断[/yellow]")
        except Exception as e:
            renderer.console.print(f"\n[red]错误: {type(e).__name__}: {e}[/red]")


if __name__ == "__main__":
    main()
