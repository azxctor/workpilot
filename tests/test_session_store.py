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
