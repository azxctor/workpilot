"""Shell 执行工具。

安全说明：这不是沙箱。cwd 只约束工作目录，命令里写绝对路径照样能
读写工作区之外的任何位置 —— 这正是它被定为 DESTRUCTIVE、每次都必须
人工确认、且永不适用「始终允许」的原因。
"""
from pathlib import Path

from workpilot.agent.events import Danger

TIMEOUT = 120
MAX_OUTPUT = 30_000

DANGEROUS_PATTERNS: list[tuple[str, str]] = []


def dangerous_hits(command: str) -> list[str]:
    """返回命中的危险模式说明。这是给人看的提示，不是防护。"""
    raise NotImplementedError


class BashTool:
    name = "bash"
    danger = Danger.DESTRUCTIVE
    schema: dict = {}

    def __init__(self, workspace: Path, timeout: int = TIMEOUT):
        self.workspace = Path(workspace)
        self.timeout = timeout

    def run(self, command: str) -> str:
        raise NotImplementedError
