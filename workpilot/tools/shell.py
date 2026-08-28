"""Shell 执行工具。

安全说明：这不是沙箱。cwd 只约束工作目录，命令里写绝对路径照样能
读写工作区之外的任何位置 —— 这正是它被定为 DESTRUCTIVE、每次都必须
人工确认、且永不适用「始终允许」的原因。
"""
import re
import subprocess
from pathlib import Path

from workpilot.agent.events import Danger

TIMEOUT = 120
MAX_OUTPUT = 30_000

# (正则, 人类可读的说明)
# 要求：命中真实危险，且不误伤形近的安全命令
# —— `git rm --cached` 不是 `rm -rf`，`>>` 是追加不是覆盖
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"(^|[;&|]\s*)rm\s+(-\w*[rf]\w*\s+)+", "递归/强制删除文件"),
    (r"(^|[;&|]\s*)sudo\s", "以 root 权限执行"),
    (r"\|\s*(sudo\s+)?(sh|bash|zsh)\b", "把下载内容直接管道给 shell 执行"),
    (r"(^|[;&|]\s*)dd\s+if=", "裸设备写入"),
    (r"(^|[;&|]\s*)mkfs", "格式化文件系统"),
    (r"chmod\s+(-\w+\s+)*777", "把权限放开到所有人可写"),
    (r"git\s+push\s+.*(--force|-f)\b", "强制推送，会覆盖远端历史"),
    (r"(^|\s)>(?!>)\s*\S", "输出重定向会覆盖目标文件"),
]


def dangerous_hits(command: str) -> list[str]:
    """返回命中的危险模式说明。这是给人看的提示，不是防护。"""
    return [why for pattern, why in DANGEROUS_PATTERNS
            if re.search(pattern, command)]


class BashTool:
    name = "bash"
    danger = Danger.DESTRUCTIVE
    schema = {
        "name": "bash",
        "description": (
            "在工作目录下执行一条 shell 命令，返回 stdout/stderr 与退出码。"
            "支持管道、重定向、&&。用于运行测试、查看 git 状态、安装依赖等。"
            "每次执行都需要用户确认，请勿用它做文件读写 —— "
            "读文件用 read_file，写文件用 write_file/edit_file。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        },
    }

    def __init__(self, workspace: Path, timeout: int = TIMEOUT):
        self.workspace = Path(workspace)
        self.timeout = timeout

    def run(self, command: str) -> str:
        # shell=True 是刻意的：模型给的本来就是一条 shell 命令
        # （含管道、重定向、&&），假装能把它安全拆成 argv 反而更危险
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.workspace),
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return f"命令执行超时（超过 {self.timeout} 秒），已终止。"

        parts = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr}")
        if proc.returncode != 0:
            parts.append(f"[exit code: {proc.returncode}]")

        out = "\n".join(parts) or "（命令无输出）"
        if len(out) > MAX_OUTPUT:
            # 一条 cat 大文件就能吃掉几十万 token
            out = out[:MAX_OUTPUT] + f"\n\n[truncated at {MAX_OUTPUT} chars]"
        return out
