from fakes import FakeProvider, text_turn, tool_turn

from workpilot.agent.events import Danger, TextDelta, ToolCallRequest, ToolResult
from workpilot.agent.loop import AgentLoop
from workpilot.cli import drive
from workpilot.tools.base import Registry


class RecordingRenderer:
    def __init__(self): self.seen = []
    def handle(self, event): self.seen.append(event)


class SafeTool:
    name = "spy"
    danger = Danger.SAFE
    schema = {"name": "spy", "description": "d",
              "input_schema": {"type": "object", "properties": {}, "required": []}}
    def run(self, **kw): return "ok"


class NoCompact:
    def should_compact(self, h): return False


def build_loop(script, tools):
    return AgentLoop(provider=FakeProvider(script), registry=Registry(tools),
                     ctx_manager=NoCompact(), system_prompt="s", workspace=".")


def test_drive_forwards_every_event_to_renderer():
    loop = build_loop([text_turn("嗨")], [])
    renderer = RecordingRenderer()

    drive(loop, "hi", renderer, approve=lambda req: "allow")

    assert any(isinstance(e, TextDelta) for e in renderer.seen)


def test_drive_feeds_approval_decision_back_into_loop():
    loop = build_loop([tool_turn("t1", "spy", {}), text_turn("完成")], [SafeTool()])
    renderer = RecordingRenderer()
    asked = []

    drive(loop, "干活", renderer,
          approve=lambda req: (asked.append(req.name), "deny")[1])

    assert asked == ["spy"]
    res = next(e for e in renderer.seen if isinstance(e, ToolResult))
    assert res.is_error is True          # 决定确实被回灌进了内核
