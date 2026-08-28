import io

from workpilot.agent.events import Danger, ToolCallRequest
from workpilot.ui.confirm import Approver


def req(name="write_file", danger=Danger.CONFIRM, **args):
    return ToolCallRequest(id="t1", name=name, args=args or {"path": "a.py"},
                           danger=danger)


def make(answers=(), yolo=False, tty=True):
    """answers 是依次喂给交互提示的回答。"""
    buf = io.StringIO()
    it = iter(answers)
    return Approver(out=buf, yolo=yolo, is_tty=tty,
                    ask_line=lambda _: next(it, "n")), buf


def test_safe_tools_are_allowed_without_asking():
    approver, buf = make()

    assert approver.ask(req(name="read_file", danger=Danger.SAFE)) == "allow"
    assert buf.getvalue() == ""          # 完全不打扰用户


def test_yolo_allows_everything_including_destructive():
    approver, _ = make(yolo=True)

    assert approver.ask(req(name="bash", danger=Danger.DESTRUCTIVE,
                            command="rm -rf /")) == "allow"


def test_non_tty_denies_instead_of_silently_allowing():
    """管道运行时静默放行写操作是不可接受的。"""
    approver, _ = make(tty=False)

    assert approver.ask(req()) == "deny"


def test_yes_allows_once_but_does_not_remember():
    approver, _ = make(answers=["y", "n"])

    assert approver.ask(req()) == "allow"
    assert approver.ask(req()) == "deny"      # 第二次仍然会问


def test_no_denies():
    approver, _ = make(answers=["n"])

    assert approver.ask(req()) == "deny"


def test_always_remembers_the_tool_for_the_session():
    approver, _ = make(answers=["a"])

    assert approver.ask(req()) == "allow"
    assert approver.ask(req()) == "allow"     # 不再询问
    assert approver.ask(req()) == "allow"


def test_always_does_not_leak_to_other_tools():
    approver, _ = make(answers=["a", "n"])

    assert approver.ask(req(name="write_file")) == "allow"
    assert approver.ask(req(name="edit_file")) == "deny"   # 另一个工具仍要问


def test_always_never_applies_to_destructive_tools():
    """bash 敲 a 只当作单次放行，下次仍然会问。"""
    approver, buf = make(answers=["a", "n"])
    bash = req(name="bash", danger=Danger.DESTRUCTIVE, command="ls")

    assert approver.ask(bash) == "allow"
    assert approver.ask(bash) == "deny"       # 没有被记住
    assert "不支持" in buf.getvalue()          # 且明确告知过用户


def test_dangerous_command_shows_warning():
    approver, buf = make(answers=["n"])

    approver.ask(req(name="bash", danger=Danger.DESTRUCTIVE,
                     command="rm -rf build"))

    out = buf.getvalue()
    assert "rm -rf build" in out
    assert "递归" in out or "删除" in out


def test_write_to_new_file_previews_line_count():
    approver, buf = make(answers=["n"])

    approver.ask(req(name="write_file", path="new.py",
                     content="a\nb\nc\n"))

    assert "new.py" in buf.getvalue()
    assert "3" in buf.getvalue()


def test_edit_preview_shows_both_sides():
    approver, buf = make(answers=["n"])

    approver.ask(req(name="edit_file", path="a.py",
                     old_string="return 1", new_string="return 42"))

    out = buf.getvalue()
    assert "return 1" in out
    assert "return 42" in out
