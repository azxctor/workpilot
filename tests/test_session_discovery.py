from workpilot.session.store import (SessionStore, find_by_id, find_latest,
                                     list_sessions)


def seed(sessions_dir, workspace, first_user_text="占位提问"):
    """建一个真实的会话。

    注意：会话文件是延迟创建的 —— 不写任何消息就不会落盘，
    所以这里必须至少写一条，否则 find_latest 根本看不到它。
    """
    store = SessionStore.create(sessions_dir, workspace=workspace, model="k3")
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


def test_list_sessions_summary_is_empty_for_a_meta_only_file(tmp_path):
    """只有 meta 的文件（旧版本遗留）不应让列举崩掉。"""
    import json

    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    sessions.mkdir(parents=True)
    meta = {"type": "meta", "session_id": "20260828-120000-aaaa",
            "workspace": str(ws.resolve()), "model": "k3",
            "created_at": "2026-08-28T12:00:00+08:00"}
    (sessions / "20260828-120000-aaaa.jsonl").write_text(
        json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")

    assert list_sessions(sessions, ws)[0]["summary"] == ""


def test_find_by_id_locates_session_regardless_of_workspace(tmp_path):
    sessions = tmp_path / "sessions"
    store = seed(sessions, tmp_path / "a")

    assert find_by_id(sessions, store.session_id) == store.path
    assert find_by_id(sessions, "20990101-000000-ffff") is None


def test_latest_is_by_activity_not_by_filename(tmp_path):
    """回归测试：曾因按文件名排序而选错会话。

    同一秒创建的两个会话，文件名的字典序由随机后缀决定，
    先创建的可能被当成最新的。改为按 mtime 排序后才正确。
    这里手工把「文件名较大但活动较早」的会话构造出来。
    """
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    sessions.mkdir(parents=True)

    import json
    import os

    def write(session_id, mtime):
        path = sessions / f"{session_id}.jsonl"
        meta = {"type": "meta", "session_id": session_id,
                "workspace": str(ws.resolve()), "model": "k3",
                "created_at": "2026-08-28T21:34:37+08:00"}
        path.write_text(json.dumps(meta, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    older_but_larger_name = write("20260828-213437-ffff", mtime=1000)
    newer_but_smaller_name = write("20260828-213437-0000", mtime=2000)

    assert find_latest(sessions, ws) == newer_but_smaller_name
    assert find_latest(sessions, ws) != older_but_larger_name


def test_list_sessions_orders_by_recent_activity(tmp_path):
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    first = seed(sessions, ws, first_user_text="较早")
    second = seed(sessions, ws, first_user_text="较晚")

    import os
    os.utime(first.path, (1000, 1000))
    os.utime(second.path, (2000, 2000))

    assert [i["summary"] for i in list_sessions(sessions, ws)] == ["较晚", "较早"]


def test_find_latest_ignores_a_session_that_never_got_written(tmp_path):
    """回归测试：空会话曾遮蔽掉真正想恢复的会话。

    启动后立刻退出会创建 SessionStore 但不落盘。若它留下了文件，
    其 mtime 最新，--continue 就会匹配到这个空会话。
    """
    sessions = tmp_path / "sessions"
    ws = tmp_path / "a"
    real = seed(sessions, ws, first_user_text="有内容的会话")

    SessionStore.create(sessions, workspace=ws, model="k3")   # 启动即退出

    assert find_latest(sessions, ws) == real.path
