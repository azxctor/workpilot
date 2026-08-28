"""权限确认：事件流的消费者。

内核只负责 yield ToolCallRequest 然后接收一个字符串，
它不知道确认是怎么发生的 —— 这是 M1 三条铁律的延续。
"""
import difflib
import sys
from pathlib import Path

from rich.console import Console

from workpilot.agent.events import Danger, ToolCallRequest
from workpilot.tools.shell import dangerous_hits


class Approver:
    def __init__(self, out=None, yolo: bool = False,
                 is_tty: bool | None = None, ask_line=None):
        self.console = Console(file=out or sys.stdout, highlight=False,
                               soft_wrap=True)
        self.yolo = yolo
        self.is_tty = sys.stdin.isatty() if is_tty is None else is_tty
        self._ask_line = ask_line or input
        self.always: set[str] = set()      # 会话内记忆，不落盘

    def ask(self, req: ToolCallRequest) -> str:
        if self.yolo or req.danger is Danger.SAFE:
            return "allow"

        if req.danger is Danger.CONFIRM and req.name in self.always:
            return "allow"

        if not self.is_tty:
            # 管道运行时静默放行写操作是不可接受的
            self.console.print(
                f"[red]非交互环境，已拒绝 {req.name}"
                f"（如需自动放行请加 --yolo）[/red]")
            return "deny"

        self._preview(req)
        answer = (self._ask_line("允许? [y]es / [n]o / [a]lways: ")
                  or "").strip().lower()

        if answer == "a":
            if req.danger is Danger.DESTRUCTIVE:
                self.console.print(
                    f"[yellow]{req.name} 不支持「始终允许」，"
                    f"本次仅放行一次。[/yellow]")
                return "allow"
            self.always.add(req.name)
            return "allow"

        return "allow" if answer == "y" else "deny"

    # ---- 预览渲染：每种工具形态不同，这是确认环节的价值所在 ----

    def _preview(self, req: ToolCallRequest) -> None:
        self.console.print()
        renderer = {
            "write_file": self._preview_write,
            "edit_file": self._preview_edit,
            "bash": self._preview_bash,
        }.get(req.name, self._preview_generic)
        renderer(req)

    def _preview_generic(self, req: ToolCallRequest) -> None:
        args = ", ".join(f"{k}={v!r}" for k, v in req.args.items())
        self.console.print(f"[cyan]{req.name}[/cyan]({args})")

    def _preview_write(self, req: ToolCallRequest) -> None:
        path = req.args.get("path", "")
        content = req.args.get("content", "")
        existing = Path(path)

        old = ""
        if existing.is_file():
            try:
                old = existing.read_text(encoding="utf-8")
            except OSError:
                old = ""

        if old:
            self.console.print(f"[cyan]覆盖[/cyan] {path}")
            self._print_diff(old, content, path)
        else:
            lines = content.splitlines()
            self.console.print(
                f"[cyan]新建[/cyan] {path} [dim](+{len(lines)} 行)[/dim]")
            for line in lines[:20]:
                self.console.print(f"  [green]+ {_esc(line)}[/green]")
            if len(lines) > 20:
                self.console.print(f"  [dim]… 还有 {len(lines) - 20} 行[/dim]")

    def _preview_edit(self, req: ToolCallRequest) -> None:
        path = req.args.get("path", "")
        old = req.args.get("old_string", "")
        new = req.args.get("new_string", "")

        self.console.print(f"[cyan]编辑[/cyan] {path}")
        for line in old.splitlines() or [old]:
            self.console.print(f"  [red]- {_esc(line)}[/red]")
        for line in new.splitlines() or [new]:
            self.console.print(f"  [green]+ {_esc(line)}[/green]")

    def _preview_bash(self, req: ToolCallRequest) -> None:
        command = req.args.get("command", "")
        self.console.print(f"[cyan]执行[/cyan] [bold]{_esc(command)}[/bold]")

        for why in dangerous_hits(command):
            self.console.print(f"  [red]⚠ {why}[/red]")

    def _print_diff(self, old: str, new: str, path: str) -> None:
        diff = difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=path, tofile=path, lineterm="", n=2)
        for line in list(diff)[:60]:
            if line.startswith("+") and not line.startswith("+++"):
                self.console.print(f"  [green]{_esc(line)}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                self.console.print(f"  [red]{_esc(line)}[/red]")
            else:
                self.console.print(f"  [dim]{_esc(line)}[/dim]")


def _esc(text: str) -> str:
    """转义 rich 的标记语法，避免文件内容里的方括号被当成样式。"""
    return text.replace("[", "\\[")
