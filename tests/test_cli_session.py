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
    return AgentLoop(provider=FakeProvider(script),
                     registry=Registry(list(tools)),
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
