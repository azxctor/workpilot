"""工具协议与注册表。"""
from typing import Protocol

from workpilot.agent.events import Danger


class Tool(Protocol):
    name: str
    danger: Danger
    schema: dict

    def run(self, **kwargs) -> str: ...


class Registry:
    """工具注册表。Agent Loop 只认识它，不认识任何具体工具。"""

    def __init__(self, tools: list):
        self._tools = {t.name: t for t in tools}

    def get(self, name: str):
        return self._tools[name]

    def schemas(self) -> list[dict]:
        # 必须按名字排序 —— 工具顺序一抖动，prompt cache 就整片失效
        return [t.schema for _, t in sorted(self._tools.items())]


def build_default_registry(workspace) -> Registry:
    """组装默认工具集。新增工具只需在这里登记一次。"""
    from workpilot.tools.fs import (EditFileTool, ListFilesTool, ReadFileTool,
                                    WriteFileTool)
    from workpilot.tools.search import GrepTool
    from workpilot.tools.shell import BashTool

    return Registry([
        ReadFileTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        EditFileTool(workspace=workspace),
        ListFilesTool(workspace=workspace),
        GrepTool(workspace=workspace),
        BashTool(workspace=workspace),
    ])
