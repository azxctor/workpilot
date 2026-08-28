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
