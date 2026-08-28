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
