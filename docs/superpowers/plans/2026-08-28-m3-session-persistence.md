# M3 会话持久化 实现计划

> **给执行者：** 必需子技能：用 superpowers:subagent-driven-development（推荐）
> 或 superpowers:executing-plans 逐任务实现本计划。步骤使用 `- [ ]` 复选框跟踪。

**目标：** 让 WorkPilot 关掉终端再打开能接着上次继续聊。

**架构：** 会话以 JSONL 落盘，每行一条原样的 message（零转换、无损）。
`drive()` 在事件循环中比对 `loop.history` 长度并追加增量，内核完全不知道
存储的存在。加载时对尾部做一次配对修复，防止崩溃截断导致下次请求 400。

**技术栈：** Python 3.10+、标准库 `json` / `pathlib` / `os` / `secrets` /
`datetime`、pytest。不引入新的第三方依赖。

**规格来源：** `docs/superpowers/specs/2026-08-28-m3-session-persistence-design.md`

## 全局约束

- Python 3.10 兼容：不使用 `match` 语句，联合类型写作 `X | None` 可用
- 所有测试用真实临时目录（pytest `tmp_path`）读写文件，禁止 mock 文件系统
- 内核铁律不破：`workpilot/agent/` 不得 import `workpilot/session/`
- 会话文件权限 `0600`，会话目录权限 `0700`
- 每个任务结束后测试必须全绿再提交；提交信息用简体中文
- 运行测试统一用 `.venv/bin/python -m pytest`
- 提交信息结尾附加：`Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `workpilot/session/__init__.py` | 新建空包文件 |
| `workpilot/session/store.py` | SessionStore（写入/加载）+ 会话发现 + 尾部修复 |
| `workpilot/cli.py` | 修改：`drive()` 加 store 参数、参数解析、装配 |
| `tests/test_session_store.py` | 写入与加载 |
| `tests/test_session_repair.py` | 尾部配对修复 |
| `tests/test_session_discovery.py` | workspace 隔离的查找与列举 |
| `tests/test_cli_session.py` | drive 增量同步、参数解析 |
| `README.md` | 新建：用法 + 隐私说明 |

`store.py` 预计约 160 行，承担「存储」这一个职责，不再细分。

---

### 任务 1：SessionStore 的写入与加载

**文件：**
- 创建：`workpilot/session/__init__.py`（空文件）
- 创建：`workpilot/session/store.py`
- 创建：`tests/test_session_store.py`

**接口：**
- 消费：无（本任务是起点）
- 产出：
  - `new_session_id() -> str`
  - `SessionStore.create(sessions_dir: Path, workspace: Path, model: str) -> SessionStore`
  - `SessionStore.open(path: Path) -> SessionStore`
  - `SessionStore.append_message(message: dict) -> None`
  - `SessionStore.load_history() -> list[dict]`
  - `SessionStore.meta -> dict`
  - `SessionStore.session_id -> str`
  - `SessionStore.path -> Path`

- [ ] **步骤 1：写失败的测试**

创建 `tests/test_session_store.py`：

```python
import json
import os
import stat

import pytest

from workpilot.session.store import SessionStore, new_session_id


def test_session_id_has_sortable_timestamp_prefix():
    """字典序即时间序，靠的是前缀而非随机后缀。

    不要断言两次生成必然不同 —— 4 位随机有 1/65536 的碰撞概率，
    那会变成偶发失败的 flaky 测试。
    """
    sid = new_session_id()
    date, time_part, suffix = sid.split("-")

    assert len(date) == 8 and date.isdigit()
    assert len(time_part) == 6 and time_part.isdigit()
    assert len(suffix) == 4


def test_session_ids_sort_in_chronological_order():
    earlier = "20260828-204930-a3f9"
    later = "20260828-204931-0001"

    assert sorted([later, earlier]) == [earlier, later]


def test_create_writes_meta_as_first_line(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path / "proj",
                                model="k3")

    first = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    assert first["type"] == "meta"
    assert first["workspace"] == str((tmp_path / "proj").resolve())
    assert first["model"] == "k3"
    assert first["session_id"] == store.session_id


def test_append_message_is_readable_immediately(tmp_path):
    """一条写完立刻可读 —— 崩溃不丢是 M3 的核心承诺。"""
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")

    store.append_message({"role": "user", "content": "你好"})

    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[1]) == {"type": "message",
                                    "data": {"role": "user", "content": "你好"}}


def test_history_roundtrip_preserves_block_structure(tmp_path):
    """无损是本里程碑的核心命题：读回来必须与写进去的对象相等。"""
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    history = [
        {"role": "user", "content": "读一下 a.py"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "好的"},
            {"type": "tool_use", "id": "t1", "name": "read_file",
             "input": {"path": "a.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "1\tx = 1", "is_error": False},
        ]},
    ]
    for msg in history:
        store.append_message(msg)

    assert SessionStore.open(store.path).load_history() == history


def test_load_skips_a_truncated_last_line(tmp_path):
    """断电写到一半，不能让整个会话不可用。"""
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    store.append_message({"role": "user", "content": "完整的一条"})
    with store.path.open("a", encoding="utf-8") as f:
        f.write('{"type": "message", "data": {"role": "user", "cont')

    assert SessionStore.open(store.path).load_history() == [
        {"role": "user", "content": "完整的一条"}]


def test_empty_session_loads_as_empty_history(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")

    assert SessionStore.open(store.path).load_history() == []


def test_files_are_owner_only(tmp_path):
    """会话含文件内容与 bash 输出，可能包含密钥，权限必须收紧。"""
    store = SessionStore.create(tmp_path / "sessions", workspace=tmp_path,
                                model="k3")

    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.path.parent).st_mode) == 0o700
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv/bin/python -m pytest tests/test_session_store.py -v`
预期：collection error，`ModuleNotFoundError: No module named 'workpilot.session'`

- [ ] **步骤 3：写最小实现**

创建 `workpilot/session/__init__.py`（空文件）。

创建 `workpilot/session/store.py`：

```python
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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv/bin/python -m pytest tests/test_session_store.py -v`
预期：8 passed

- [ ] **步骤 5：提交**

```bash
git add workpilot/session/ tests/test_session_store.py
git commit -m "feat: 会话存储的写入与加载

存 history 而非事件流，data 为 message 原样内容，零转换无损。
JSONL 追加写保证单条写完即刻可读；残行跳过，不使整个会话不可用。
文件 0600、目录 0700 —— 会话含文件内容与 bash 输出，可能包含密钥。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 2：尾部配对修复

**文件：**
- 修改：`workpilot/session/store.py`（追加模块级函数）
- 创建：`tests/test_session_repair.py`

**接口：**
- 消费：任务 1 的 `SessionStore.load_history()`
- 产出：`repair_dangling_tool_use(history: list[dict]) -> tuple[list[dict], bool]`
  返回 `(修复后的 history, 是否丢弃了内容)`

- [ ] **步骤 1：写失败的测试**

创建 `tests/test_session_repair.py`：

```python
from workpilot.session.store import repair_dangling_tool_use


def assistant_with_tool_use():
    return {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "read_file",
         "input": {"path": "a.py"}}]}


def tool_result_message():
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok",
         "is_error": False}]}


def test_drops_trailing_assistant_whose_tool_use_has_no_result():
    """崩在工具执行中途 —— 直接发这样的 history 会 400。"""
    history = [{"role": "user", "content": "读一下"},
               assistant_with_tool_use()]

    fixed, dropped = repair_dangling_tool_use(history)

    assert dropped is True
    assert fixed == [{"role": "user", "content": "读一下"}]


def test_keeps_history_when_tool_result_is_present():
    history = [{"role": "user", "content": "读一下"},
               assistant_with_tool_use(),
               tool_result_message()]

    fixed, dropped = repair_dangling_tool_use(history)

    assert dropped is False
    assert fixed == history


def test_keeps_plain_text_assistant_at_the_end():
    history = [{"role": "user", "content": "你好"},
               {"role": "assistant", "content": [
                   {"type": "text", "text": "你好呀"}]}]

    fixed, dropped = repair_dangling_tool_use(history)

    assert dropped is False
    assert fixed == history


def test_does_not_touch_multiple_completed_tool_rounds():
    """连续多轮工具调用且全部配对完整时，一条都不能丢。"""
    history = [{"role": "user", "content": "干活"},
               assistant_with_tool_use(), tool_result_message(),
               assistant_with_tool_use(), tool_result_message(),
               {"role": "assistant", "content": [
                   {"type": "text", "text": "做完了"}]}]

    fixed, dropped = repair_dangling_tool_use(history)

    assert dropped is False
    assert fixed == history


def test_empty_history_is_unchanged():
    fixed, dropped = repair_dangling_tool_use([])

    assert dropped is False
    assert fixed == []


def test_string_content_assistant_is_unchanged():
    history = [{"role": "assistant", "content": "纯文本"}]

    fixed, dropped = repair_dangling_tool_use(history)

    assert dropped is False
    assert fixed == history
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv/bin/python -m pytest tests/test_session_repair.py -v`
预期：collection error，`ImportError: cannot import name 'repair_dangling_tool_use'`

- [ ] **步骤 3：写最小实现**

在 `workpilot/session/store.py` 末尾追加：

```python
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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv/bin/python -m pytest tests/test_session_repair.py -v`
预期：6 passed

- [ ] **步骤 5：提交**

```bash
git add workpilot/session/store.py tests/test_session_repair.py
git commit -m "feat: 会话加载的尾部配对修复

崩在工具执行中途时 history 末尾是未配对的 tool_use，直接发送会 400。
加载后丢弃该回合 —— 丢一轮未完成的，好过恢复出一个必然失败的会话。
只需检查最后一条：中间的消息当时已成功发送过，不可能断裂。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 3：按工作目录隔离的会话发现

**文件：**
- 修改：`workpilot/session/store.py`（追加模块级函数）
- 创建：`tests/test_session_discovery.py`

**接口：**
- 消费：任务 1 的 `SessionStore.create/open`、`SessionStore.path`
- 产出：
  - `find_latest(sessions_dir: Path, workspace: Path) -> Path | None`
  - `list_sessions(sessions_dir: Path, workspace: Path) -> list[dict]`
    每项形如 `{"session_id": str, "created_at": str, "summary": str, "path": Path}`
  - `find_by_id(sessions_dir: Path, session_id: str) -> Path | None`

- [ ] **步骤 1：写失败的测试**

创建 `tests/test_session_discovery.py`：

```python
from workpilot.session.store import (SessionStore, find_by_id, find_latest,
                                     list_sessions)


def seed(sessions_dir, workspace, first_user_text=None):
    store = SessionStore.create(sessions_dir, workspace=workspace, model="k3")
    if first_user_text is not None:
        store.append_message({"role": "user", "content": first_user_text})
    return store


def test_find_latest_returns_none_when_nothing_matches(tmp_path):
    sessions = tmp_path / "sessions"
    seed(sessions, tmp_path / "other-project")

    assert find_latest(sessions, tmp_path / "my-project") is None


def test_find_latest_ignores_sessions_from_other_workspaces(tmp_path):
    """跨项目串上下文能真正造成误改文件。"""
    sessions = tmp_path / "sessions"
    mine = seed(sessions, tmp_path / "a")
    seed(sessions, tmp_path / "b")          # 更晚，但属于别的目录

    assert find_latest(sessions, tmp_path / "a") == mine.path


def test_find_latest_picks_the_newest_of_the_same_workspace(tmp_path):
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    seed(sessions, ws)
    newest = seed(sessions, ws)

    assert find_latest(sessions, ws) == newest.path


def test_find_latest_handles_missing_directory(tmp_path):
    assert find_latest(tmp_path / "never-created", tmp_path) is None


def test_list_sessions_returns_id_time_and_first_question(tmp_path):
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    store = seed(sessions, ws, first_user_text="帮我看看这个项目")

    items = list_sessions(sessions, ws)

    assert len(items) == 1
    assert items[0]["session_id"] == store.session_id
    assert items[0]["summary"] == "帮我看看这个项目"
    assert items[0]["created_at"]


def test_list_sessions_only_shows_current_workspace(tmp_path):
    sessions = tmp_path / "sessions"
    seed(sessions, tmp_path / "a", first_user_text="属于 a")
    seed(sessions, tmp_path / "b", first_user_text="属于 b")

    items = list_sessions(sessions, tmp_path / "a")

    assert [i["summary"] for i in items] == ["属于 a"]


def test_list_sessions_summary_is_empty_for_session_without_messages(tmp_path):
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    seed(sessions, ws)

    assert list_sessions(sessions, ws)[0]["summary"] == ""


def test_find_by_id_locates_session_regardless_of_workspace(tmp_path):
    sessions = tmp_path / "sessions"
    store = seed(sessions, tmp_path / "a")

    assert find_by_id(sessions, store.session_id) == store.path
    assert find_by_id(sessions, "20990101-000000-ffff") is None
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv/bin/python -m pytest tests/test_session_discovery.py -v`
预期：collection error，`ImportError: cannot import name 'find_latest'`

- [ ] **步骤 3：写最小实现**

在 `workpilot/session/store.py` 末尾追加：

```python
def _session_files(sessions_dir) -> list[Path]:
    """按文件名倒序返回会话文件。session_id 的字典序即时间序。"""
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []
    return sorted(sessions_dir.glob("*.jsonl"), reverse=True)


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


def _first_question(path: Path) -> str:
    """取第一条 user 消息的文本作为摘要。"""
    for message in SessionStore.open(path).load_history():
        if message.get("role") == "user" and isinstance(
                message.get("content"), str):
            return message["content"]
    return ""
```

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv/bin/python -m pytest tests/test_session_discovery.py -v`
预期：8 passed

- [ ] **步骤 5：提交**

```bash
git add workpilot/session/store.py tests/test_session_discovery.py
git commit -m "feat: 按工作目录隔离的会话发现

--continue 只恢复当前目录的会话。若恢复全局最近一次，从 A 项目切到
B 项目再 --continue，模型会拿着 A 的记忆在 B 里操作，可造成误改文件。
匹配只读文件第一行的 meta，不解析整个文件。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 4：drive() 增量同步

**文件：**
- 修改：`workpilot/cli.py:13-30`（`drive` 函数）
- 修改：`workpilot/session/store.py`（给 SessionStore 加 `sync`）
- 创建：`tests/test_cli_session.py`

**接口：**
- 消费：任务 1 的 `SessionStore.append_message`；`workpilot.cli.drive`
  现有签名 `drive(loop, user_input, renderer, approve)`
- 产出：
  - `SessionStore.sync(history: list[dict], synced: int) -> int`
    追加 `history[synced:]`，返回新的已同步条数
  - `drive(loop, user_input, renderer, approve, store=None) -> None`
    新增可选形参 `store`，默认 `None` 保持向后兼容

- [ ] **步骤 1：写失败的测试**

创建 `tests/test_cli_session.py`：

```python
import io

from fakes import FakeProvider, text_turn, tool_turn

from workpilot.agent.events import Danger
from workpilot.agent.loop import AgentLoop
from workpilot.cli import drive
from workpilot.session.store import SessionStore
from workpilot.tools.base import Registry
from workpilot.ui.confirm import Approver
from workpilot.ui.renderer import Renderer


class SafeTool:
    name = "spy"
    danger = Danger.SAFE
    schema = {"name": "spy", "description": "d",
              "input_schema": {"type": "object", "properties": {},
                               "required": []}}

    def run(self, **kw):
        return "ok"


def build_loop(script, tools=()):
    return AgentLoop(provider=FakeProvider(script), registry=Registry(list(tools)),
                     ctx_manager=None, system_prompt="s", workspace=".")


def run(loop, store, text="hi"):
    buf = io.StringIO()
    approver = Approver(out=buf, is_tty=False)
    drive(loop, text, Renderer(out=buf, width=200), approver.ask, store=store)


def test_sync_appends_only_new_messages(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")

    synced = store.sync([{"role": "user", "content": "a"}], 0)
    assert synced == 1

    synced = store.sync([{"role": "user", "content": "a"},
                         {"role": "assistant", "content": "b"}], synced)
    assert synced == 2

    assert store.load_history() == [{"role": "user", "content": "a"},
                                    {"role": "assistant", "content": "b"}]


def test_sync_is_idempotent_when_nothing_new(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    history = [{"role": "user", "content": "a"}]

    synced = store.sync(history, 0)
    synced = store.sync(history, synced)

    assert synced == 1
    assert len(store.load_history()) == 1


def test_drive_persists_a_plain_turn(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    loop = build_loop([text_turn("你好")])

    run(loop, store)

    saved = store.load_history()
    assert saved[0] == {"role": "user", "content": "hi"}
    assert saved[-1]["role"] == "assistant"


def test_drive_persists_tool_rounds_in_order(tmp_path):
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    loop = build_loop([tool_turn("t1", "spy", {}), text_turn("完成")],
                      tools=[SafeTool()])

    run(loop, store, text="干活")

    saved = store.load_history()
    assert [m["role"] for m in saved] == ["user", "assistant", "user",
                                          "assistant"]
    assert saved[2]["content"][0]["tool_use_id"] == "t1"


def test_saved_history_equals_in_memory_history(tmp_path):
    """落盘必须与内存中的 history 完全一致，否则恢复后行为会漂移。"""
    store = SessionStore.create(tmp_path, workspace=tmp_path, model="k3")
    loop = build_loop([tool_turn("t1", "spy", {}), text_turn("完成")],
                      tools=[SafeTool()])

    run(loop, store, text="干活")

    assert store.load_history() == loop.history


def test_drive_without_store_still_works(tmp_path):
    """store 是可选的，不传时行为不变。"""
    loop = build_loop([text_turn("你好")])
    buf = io.StringIO()

    drive(loop, "hi", Renderer(out=buf, width=200),
          Approver(out=buf, is_tty=False).ask)

    assert "你好" in buf.getvalue()
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv/bin/python -m pytest tests/test_cli_session.py -v`
预期：`AttributeError: 'SessionStore' object has no attribute 'sync'`
以及 `drive() got an unexpected keyword argument 'store'`

- [ ] **步骤 3：写最小实现**

在 `workpilot/session/store.py` 的 `SessionStore` 类中，
`load_history` 方法之后添加：

```python
    def sync(self, history: list[dict], synced: int) -> int:
        """把 history 中尚未落盘的部分追加写入，返回新的已同步条数。"""
        for message in history[synced:]:
            self.append_message(message)
        return len(history)
```

修改 `workpilot/cli.py` 的 `drive` 函数为：

```python
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
```

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv/bin/python -m pytest tests/test_cli_session.py -v`
预期：6 passed

再运行全量：`.venv/bin/python -m pytest`
预期：全部通过（`drive` 的新参数有默认值，旧测试不受影响）

- [ ] **步骤 5：提交**

```bash
git add workpilot/cli.py workpilot/session/store.py tests/test_cli_session.py
git commit -m "feat: drive() 中同步 history 增量落盘

事件循环每转一圈追加新增 message，工具执行完即刻落盘，
崩溃最多丢当前未完成的半轮。内核不知道存储的存在 ——
不用 on_message 回调，理由与 M1 拒绝回调式权限确认相同。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 5：CLI 参数与装配

**文件：**
- 修改：`workpilot/cli.py`（`parse_args` 与 `main`）
- 修改：`tests/test_cli_session.py`（追加参数解析测试）

**接口：**
- 消费：任务 2 的 `repair_dangling_tool_use`、任务 3 的
  `find_latest` / `find_by_id` / `list_sessions`、任务 4 的 `drive(..., store=)`
- 产出：
  - `sessions_dir() -> Path` 返回 `~/.workpilot/sessions`
  - `parse_args` 支持 `--continue` / `--resume ID` / `--sessions`，
    前两者互斥
  - `main()` 装配完成

- [ ] **步骤 1：写失败的测试**

在 `tests/test_cli_session.py` 末尾追加：

```python
import pytest

from workpilot.cli import parse_args


def test_continue_flag_is_parsed():
    assert parse_args(["--continue"]).continue_session is True
    assert parse_args([]).continue_session is False


def test_resume_takes_a_session_id():
    assert parse_args(["--resume", "20260828-204930-a3f9"]).resume == \
        "20260828-204930-a3f9"


def test_sessions_flag_is_parsed():
    assert parse_args(["--sessions"]).sessions is True


def test_continue_and_resume_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--continue", "--resume", "abc"])


def test_sessions_dir_is_under_home():
    from workpilot.cli import sessions_dir

    assert sessions_dir().name == "sessions"
    assert sessions_dir().parent.name == ".workpilot"
```

- [ ] **步骤 2：运行测试确认失败**

运行：`.venv/bin/python -m pytest tests/test_cli_session.py -k "flag or resume or mutually or sessions_dir" -v`
预期：`AttributeError: 'Namespace' object has no attribute 'continue_session'`

- [ ] **步骤 3：写最小实现**

修改 `workpilot/cli.py`。先替换 import 段与 `parse_args`：

```python
import argparse
import os
import sys
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
```

然后把 `main()` 整体替换为：

```python
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
```

注意：恢复会话时 `loop.history` 已有内容，而 `drive` 内部用
`len(store.load_history())` 初始化 `synced`，两者一致，不会重复写入。

- [ ] **步骤 4：运行测试确认通过**

运行：`.venv/bin/python -m pytest`
预期：全部通过

- [ ] **步骤 5：提交**

```bash
git add workpilot/cli.py tests/test_cli_session.py
git commit -m "feat: --continue / --resume / --sessions 参数与装配

--sessions 是必需的：没有它 --resume 要求用户凭空知道 id。
--continue 与 --resume 互斥。恢复时先做尾部配对修复并告知用户。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 6：README 与隐私说明

**文件：**
- 创建：`README.md`

**接口：**
- 消费：任务 5 的 CLI 参数
- 产出：无代码接口

- [ ] **步骤 1：写 README**

创建 `README.md`：

```markdown
# WorkPilot

终端里的 AI 编码助手：对话式地读写代码、执行命令。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 配置模型

默认使用 Anthropic：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

也可接任何 OpenAI 兼容接口（Moonshot / DeepSeek / Qwen / Ollama）：

```bash
export WORKPILOT_PROVIDER=openai
export WORKPILOT_BASE_URL=https://api.kimi.com/coding/v1
export WORKPILOT_MODEL=k3
export WORKPILOT_API_KEY=<你的 key>
```

API key 只从环境变量读取，不接受命令行参数 —— 避免留在 shell history 里。

## 用法

```bash
workpilot                  # 新会话
workpilot --continue       # 恢复当前目录最近一次会话
workpilot --resume <id>    # 恢复指定会话
workpilot --sessions       # 列出当前目录的历史会话
workpilot --yolo           # 跳过所有确认（危险）
```

## 工具与权限

| 工具 | 是否需要确认 |
|---|---|
| `read_file` / `list_files` / `grep` | 否 |
| `write_file` / `edit_file` | 是，可选「始终允许」 |
| `bash` | 每次都要确认，不支持「始终允许」 |

确认时会展示改动的 diff、或命令原文。对 `rm -rf`、`sudo`、
`curl | sh` 等模式会额外标红提示 —— 这是提示，**不是防护**。

`bash` 只能约束工作目录，命令里写绝对路径照样能读写工作区之外的位置。
**它不是沙箱。**

## 会话数据与隐私

会话保存在 `~/.workpilot/sessions/<id>.jsonl`，**明文存储**，其中包含：

- 你输入的全部内容
- 模型读取过的文件内容
- `bash` 命令及其完整输出

因此**可能包含密钥、令牌或生产数据**。文件权限为 `0600`、目录为 `0700`
（仅属主可读写），但未加密。不需要时可直接删除：

```bash
rm -rf ~/.workpilot/sessions
```

会话按工作目录隔离，`--continue` 只会恢复当前目录的会话。

## 开发

```bash
.venv/bin/python -m pytest
```
```

- [ ] **步骤 2：验证文档中的命令真实可用**

运行：`.venv/bin/workpilot --sessions`
预期：正常输出（无历史会话时提示「该目录没有历史会话。」），不报错

- [ ] **步骤 3：提交**

```bash
git add README.md
git commit -m "docs: README 与会话隐私说明

明确写出 ~/.workpilot/sessions 里存了什么：用户输入、读过的文件内容、
bash 完整输出，可能包含密钥。文件 0600 但未加密，并给出删除方式。
同时如实说明 bash 不是沙箱、危险模式高亮是提示而非防护。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### 任务 7：真实模型验收

**文件：** 无（验证任务，不改代码）

**接口：**
- 消费：任务 1-6 的全部产出

- [ ] **步骤 1：全量测试与铁律复查**

```bash
.venv/bin/python -m pytest
grep -rn "workpilot.session" workpilot/agent/ || echo "✓ 内核未引用 session/"
```

预期：全部测试通过；内核未引用 `session/`

- [ ] **步骤 2：准备验收目录**

```bash
rm -rf /tmp/wp-m3 && mkdir -p /tmp/wp-m3 && cd /tmp/wp-m3
cat > shipping.py <<'EOF'
def calculate_shipping(weight, distance):
    return weight * 0.5 + distance * 0.1
EOF
```

- [ ] **步骤 3：第一轮对话后退出**

设置好模型环境变量（见 README），然后：

```bash
script -q /dev/null .venv/bin/workpilot <<'INPUT'
shipping.py 里那个函数是怎么算运费的？
/exit
INPUT
```

预期：模型调用 `read_file` 读取后作答。

- [ ] **步骤 4：恢复会话并提问依赖上下文的问题**

```bash
script -q /dev/null .venv/bin/workpilot --continue <<'INPUT'
你刚才读的那个函数叫什么名字？它的第二个参数是什么？
/exit
INPUT
```

预期：先输出「已恢复会话 ...（N 条消息）」，随后模型**不再重新读文件**
即可答出 `calculate_shipping` 与 `distance`。这证明历史被无损恢复。

- [ ] **步骤 5：验证会话文件与隔离**

```bash
ls -l ~/.workpilot/sessions/            # 权限应为 -rw-------
cd /tmp && .venv/bin/workpilot --sessions   # 换目录后应看不到上面的会话
```

预期：文件权限 `0600`；在 `/tmp` 下 `--sessions` 不显示 `/tmp/wp-m3` 的会话。

- [ ] **步骤 6：清理并提交验收结果**

```bash
rm -rf /tmp/wp-m3
git add -A
git commit -m "test: M3 真实模型验收通过

恢复后模型不再重读文件即可答出上一轮的函数名与参数，证明历史无损。
会话文件权限 0600，跨目录 --sessions 不串。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```
