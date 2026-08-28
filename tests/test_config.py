from workpilot.config import build_system_prompt


def test_system_prompt_states_the_workspace(tmp_path):
    prompt = build_system_prompt(tmp_path)

    assert str(tmp_path) in prompt


def test_system_prompt_is_byte_stable_across_calls(tmp_path):
    """system 段打了 cache_control，任何抖动都会让缓存整片失效。"""
    assert build_system_prompt(tmp_path) == build_system_prompt(tmp_path)
