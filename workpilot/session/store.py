"""会话持久化。

存 history 而非事件流：恢复需要的是 messages 数组，而事件只是它的投影，
从投影反推原件必然有损 —— 且这种损伤不会在恢复时报错，
会在下一次请求时才炸成 400。
"""
import json
import os
import secrets
from datetime import datetime
from pathlib import Path


def new_session_id() -> str:
    """<YYYYMMDD>-<HHMMSS>-<4 位随机>，字典序即时间序。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


class SessionStore:
    def __init__(self, path: Path, meta: dict):
        self.path = Path(path)
        self.meta = meta

    @property
    def session_id(self) -> str:
        return self.meta.get("session_id", self.path.stem)

    @classmethod
    def create(cls, sessions_dir, workspace, model: str) -> "SessionStore":
        sessions_dir = Path(sessions_dir)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(sessions_dir, 0o700)

        session_id = new_session_id()
        meta = {
            "type": "meta",
            "session_id": session_id,
            "workspace": str(Path(workspace).resolve()),
            "model": model,
            "created_at": datetime.now().astimezone().isoformat(),
        }
        path = sessions_dir / f"{session_id}.jsonl"
        path.touch()
        os.chmod(path, 0o600)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        return cls(path, meta)

    @classmethod
    def open(cls, path) -> "SessionStore":
        path = Path(path)
        meta = {}
        with path.open("r", encoding="utf-8") as f:
            first = f.readline()
        if first.strip():
            try:
                candidate = json.loads(first)
                if candidate.get("type") == "meta":
                    meta = candidate
            except json.JSONDecodeError:
                pass
        return cls(path, meta)

    def append_message(self, message: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "message", "data": message},
                               ensure_ascii=False) + "\n")

    def load_history(self) -> list[dict]:
        history = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue          # 残行（断电写到一半）直接跳过
                if record.get("type") == "message":
                    history.append(record["data"])
        return history

    def sync(self, history: list[dict], synced: int) -> int:
        """把 history 中尚未落盘的部分追加写入，返回新的已同步条数。"""
        for message in history[synced:]:
            self.append_message(message)
        return len(history)


def repair_dangling_tool_use(history: list[dict]) -> tuple[list[dict], bool]:
    """丢弃尾部未配对的 tool_use 回合。

    崩溃点若在工具执行中途，history 末尾是一条带 tool_use 的 assistant
    消息，而 tool_result 尚未写入。直接发送这样的 history 会得到 400。

    只需检查最后一条：tool_result 总是紧随其后的 user 消息，
    所以「最后一条是含 tool_use 的 assistant」等价于「配对断裂」。
    历史中间不可能断裂 —— 中间的消息当时已成功发送过。
    """
    if not history:
        return history, False

    last = history[-1]
    if last.get("role") != "assistant":
        return history, False

    content = last.get("content")
    if not isinstance(content, list):
        return history, False

    has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use"
                       for b in content)
    if not has_tool_use:
        return history, False

    return history[:-1], True


def _session_files(sessions_dir) -> list[Path]:
    """按最近活动时间倒序返回会话文件。

    用 mtime 而非文件名排序，有两个理由：

    1. 文件名的字典序只在【秒级不同】时等于时间序 —— 同一秒创建的两个
       会话，排序完全由随机后缀决定，先创建的可能被当成最新的。
    2. 语义上 --continue 想要的是「最近活动过的」会话，而不是「最近创建的」。
       昨天建、今天又聊过的会话，才是用户想接着聊的那个。

    mtime 相同时用文件名兜底，保证顺序确定。
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []
    return sorted(sessions_dir.glob("*.jsonl"),
                  key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def _read_meta(path: Path) -> dict:
    """只读第一行拿 meta，不解析整个文件。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            first = f.readline()
    except OSError:
        return {}
    if not first.strip():
        return {}
    try:
        record = json.loads(first)
    except json.JSONDecodeError:
        return {}
    return record if record.get("type") == "meta" else {}


def find_latest(sessions_dir, workspace) -> "Path | None":
    """当前工作目录下最近一次会话。跨目录不匹配，避免串上下文。"""
    target = str(Path(workspace).resolve())
    for path in _session_files(sessions_dir):
        if _read_meta(path).get("workspace") == target:
            return path
    return None


def find_by_id(sessions_dir, session_id: str) -> "Path | None":
    path = Path(sessions_dir) / f"{session_id}.jsonl"
    return path if path.is_file() else None


def _first_question(path: Path) -> str:
    """取第一条 user 消息的文本作为摘要。"""
    for message in SessionStore.open(path).load_history():
        if message.get("role") == "user" and isinstance(
                message.get("content"), str):
            return message["content"]
    return ""


def list_sessions(sessions_dir, workspace) -> list[dict]:
    """列出当前工作目录的会话，附首条提问摘要。"""
    target = str(Path(workspace).resolve())
    items = []
    for path in _session_files(sessions_dir):
        meta = _read_meta(path)
        if meta.get("workspace") != target:
            continue
        items.append({
            "session_id": meta.get("session_id", path.stem),
            "created_at": meta.get("created_at", ""),
            "summary": _first_question(path),
            "path": path,
        })
    return items
