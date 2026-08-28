"""终端渲染：事件流的消费者。这是全系统唯一负责输出的地方。"""
import sys

from rich.console import Console

from workpilot.agent.events import (ContextCompacted, TextDelta, ThinkingDelta,
                                    ToolCallRequest, ToolResult, TurnEnd)


class Renderer:
    def __init__(self, out=None, width: int | None = None):
        self.console = Console(file=out or sys.stdout, width=width,
                               highlight=False, soft_wrap=True)

    def handle(self, event) -> None:
        if isinstance(event, TextDelta):
            self.console.print(event.text, end="")

        elif isinstance(event, ThinkingDelta):
            self.console.print(f"[dim]{event.text}[/dim]", end="")

        elif isinstance(event, ToolCallRequest):
            args = ", ".join(f"{k}={v!r}" for k, v in event.args.items())
            self.console.print(f"\n[cyan]⏺ {event.name}[/cyan]({args})")

        elif isinstance(event, ToolResult):
            mark = "[red]✗[/red]" if event.is_error else "[green]✓[/green]"
            self.console.print(f"  {mark} {self._preview(event.output)}")

        elif isinstance(event, ContextCompacted):
            self.console.print(
                f"[yellow]⚡ 已压缩上下文，释放 {event.freed_tokens} tokens"
                f"（保留最近 {event.kept_turns} 条）[/yellow]")

        elif isinstance(event, TurnEnd):
            self.console.print(
                f"\n[dim]tokens: in={event.input_tokens} "
                f"out={event.output_tokens} "
                f"cached={event.cache_read_tokens}[/dim]")

    @staticmethod
    def _preview(text: str, limit: int = 200) -> str:
        """工具输出可能很长，终端只显示头部摘要（完整内容照样进模型）。"""
        first = text.strip().splitlines()[0] if text.strip() else ""
        if len(first) > limit:
            first = first[:limit] + "…"
        extra = len(text.splitlines()) - 1
        return first + (f" [dim](+{extra} 行)[/dim]" if extra > 0 else "")
