"""搜索工具。"""
import re
import shutil
import subprocess
from pathlib import Path

from workpilot.agent.events import Danger
from workpilot.tools.fs import SKIP_DIRS, safe_path

MAX_HITS = 200


def _search_python(root: Path, base: Path, pattern: str) -> list[tuple]:
    """纯 Python 回退实现。返回 (相对路径, 行号, 行内容) 的列表。"""
    regex = re.compile(pattern)
    hits: list[tuple] = []

    for p in sorted(root.rglob("*")):
        rel = p.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue        # 二进制文件直接跳过
        for i, line in enumerate(lines, 1):
            if regex.search(line):
                hits.append((str(rel), i, line))
                if len(hits) >= MAX_HITS:
                    return hits
    return hits


def _parse_rg_output(stdout: str, base: Path) -> list[tuple]:
    """解析 rg 的 <路径>:<行号>:<内容> 输出。

    只按前两个冒号切分 —— 行内容本身经常含冒号（字典、URL、时间戳）。
    """
    hits: list[tuple] = []
    for line in stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        file_path, lineno, text = parts
        hits.append((str(Path(file_path).relative_to(base)), int(lineno), text))
    return hits


def _search_ripgrep(root: Path, base: Path, pattern: str) -> list[tuple]:
    """ripgrep 实现。输出格式由本函数统一，不直接透出 rg 的原生排版。"""
    # 注意 --max-count 是【每文件】上限，不是总数上限；
    # 它只用来防止单个文件产生海量匹配，总数由下面的切片保证
    args = ["rg", "--line-number", "--no-heading", "--color", "never",
            "--max-count", str(MAX_HITS)]
    for d in sorted(SKIP_DIRS):
        args += ["--glob", f"!{d}/"]
    args += [pattern, str(root)]

    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if proc.returncode not in (0, 1):        # 1 = 无匹配，属正常
        raise RuntimeError(proc.stderr.strip() or "ripgrep 执行失败")

    return _parse_rg_output(proc.stdout, base)[:MAX_HITS]


class GrepTool:
    name = "grep"
    danger = Danger.SAFE
    schema: dict = {}

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    def run(self, pattern: str, path: str = ".") -> str:
        root = safe_path(self.workspace, path)
        base = self.workspace.resolve()

        if shutil.which("rg"):
            hits = _search_ripgrep(root, base, pattern)
        else:
            hits = _search_python(root, base, pattern)

        if not hits:
            return f"没有匹配 {pattern!r} 的内容。"

        body = "\n".join(f"{f}:{n}: {t}" for f, n, t in hits)
        if len(hits) >= MAX_HITS:
            body += f"\n\n[truncated: 命中数已达上限 {MAX_HITS}]"
        return body
