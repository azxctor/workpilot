"""配置与系统提示构建。"""
from pathlib import Path


def build_system_prompt(workspace: Path) -> str:
    """构建系统提示。

    注意：这段内容会打上 cache_control，必须逐字节稳定 ——
    绝不能塞时间戳、随机 ID 或任何每次调用都变的东西。
    """
    return f"""你是 WorkPilot，一个运行在终端里的编码助手。
工作目录：{workspace}

工作方式：
- 回答关于代码的问题前，先用 read_file 把相关文件读出来，不要凭猜测
- 一次只做一件事，做完再进行下一步
- 回答简洁，不要复述文件的全部内容"""


def build_provider():
    """按环境变量选择 Provider。

    API key 只从环境变量读 —— 绝不接受命令行参数，
    否则会留在 shell history 里。
    """
    import os

    kind = os.environ.get("WORKPILOT_PROVIDER", "anthropic").lower()

    if kind == "anthropic":
        from workpilot.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    if kind == "openai":
        from workpilot.providers.openai_provider import OpenAIProvider
        model = os.environ.get("WORKPILOT_MODEL")
        if not model:
            raise ValueError("使用 openai provider 时必须设置 WORKPILOT_MODEL")
        return OpenAIProvider(
            model=model,
            api_key=os.environ.get("WORKPILOT_API_KEY"),
            base_url=os.environ.get("WORKPILOT_BASE_URL"),
        )

    raise ValueError(f"未知的 provider: {kind}（支持 anthropic / openai）")
