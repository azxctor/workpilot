"""文件系统工具。"""
from pathlib import Path

from workpilot.agent.events import Danger

MAX_LINES = 2000


def safe_path(workspace: Path, raw: str) -> Path:
    """把相对路径解析为绝对路径，并确保它没有逃出 workspace。

    必须在 resolve() 之后再比较 —— 否则 '../' 和符号链接都能绕过检查。
    """
    root = workspace.resolve()
    target = (root / raw).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {raw}")
    return target


class ReadFileTool:
    name = "read_file"
    danger = Danger.SAFE
    schema = {
        "name": "read_file",
        "description": (
            "读取工作目录内某个文件的内容，返回带行号的文本。"
            "在修改任何代码之前，先用它把相关文件读出来，不要靠猜测。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于工作目录的文件路径，例如 src/main.py",
                },
            },
            "required": ["path"],
        },
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def run(self, path: str) -> str:
        target = safe_path(self.workspace, path)
        lines = target.read_text(encoding="utf-8").splitlines()
        body = "\n".join(f"{i}\t{line}"
                         for i, line in enumerate(lines[:MAX_LINES], 1))
        if len(lines) > MAX_LINES:
            # 必须告知模型这里被截断了，否则它会以为文件就这么长
            body += (f"\n\n[truncated: showing first {MAX_LINES} of "
                     f"{len(lines)} lines]")
        return body
