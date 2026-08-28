"""文件系统工具。"""
from pathlib import Path

from workpilot.agent.events import Danger

MAX_LINES = 2000
MAX_ENTRIES = 1000

# 这些目录里的内容对理解项目没有帮助，却能瞬间撑爆上下文
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
             ".idea", ".vscode"}


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


class EditFileTool:
    name = "edit_file"
    danger = Danger.CONFIRM
    schema = {
        "name": "edit_file",
        "description": (
            "对文件做精确的字符串替换。old_string 必须在文件中唯一出现 —— "
            "若出现 0 次或多次都会报错并拒绝执行。"
            "因此请先用 read_file 读取文件，再据实构造 old_string；"
            "若原文太短不唯一，就多带几行上下文。"
            "修改已有文件请优先用本工具，而不是 write_file 重写全文。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "相对于工作目录的文件路径"},
                "old_string": {"type": "string",
                               "description": "要被替换的原文，必须唯一"},
                "new_string": {"type": "string", "description": "替换成的新内容"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def run(self, path: str, old_string: str, new_string: str) -> str:
        target = safe_path(self.workspace, path)
        content = target.read_text(encoding="utf-8")

        count = content.count(old_string)
        # 0 次说明模型记错了内容，多次说明可能改错位置 ——
        # 两种情况都拒绝执行并说明实情，绝不猜一个
        if count == 0:
            raise ValueError(
                f"old_string not found in {path}. "
                f"请先用 read_file 读取该文件，确认要替换的原文。")
        if count > 1:
            raise ValueError(
                f"old_string 在 {path} 中出现了 {count} 次，无法确定改哪一处。"
                f"请提供包含上下文的更长片段，使其唯一。")

        target.write_text(content.replace(old_string, new_string),
                          encoding="utf-8")
        return f"已修改 {path}"


class WriteFileTool:
    name = "write_file"
    danger = Danger.CONFIRM
    schema = {
        "name": "write_file",
        "description": (
            "把内容整体写入文件，会覆盖已有内容。"
            "用于新建文件；修改已有文件请用 edit_file，避免重写全文。"
            "缺失的父目录会自动创建。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "相对于工作目录的文件路径"},
                "content": {"type": "string", "description": "完整的文件内容"},
            },
            "required": ["path", "content"],
        },
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def run(self, path: str, content: str) -> str:
        target = safe_path(self.workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content.splitlines())} 行）"


class ListFilesTool:
    name = "list_files"
    danger = Danger.SAFE
    schema = {
        "name": "list_files",
        "description": (
            "递归列出目录下的文件，自动跳过 .git、node_modules、.venv 等噪音目录。"
            "想了解项目结构时先用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "相对于工作目录的目录路径，默认为当前目录"},
            },
            "required": [],
        },
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def run(self, path: str = ".") -> str:
        root = safe_path(self.workspace, path)
        base = self.workspace.resolve()

        entries = []
        for p in sorted(root.rglob("*")):
            if any(part in SKIP_DIRS for part in p.relative_to(base).parts):
                continue
            if p.is_file():
                entries.append(str(p.relative_to(base)))

        body = "\n".join(entries[:MAX_ENTRIES])
        if len(entries) > MAX_ENTRIES:
            body += (f"\n\n[truncated: showing first {MAX_ENTRIES} of "
                     f"{len(entries)} files]")
        return body or "（目录为空）"
