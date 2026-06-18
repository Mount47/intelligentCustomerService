"""组装 ToolRegistry：收集各域工具。AgentCore.tool_registry 用它。"""
from __future__ import annotations

from app.tools import (
    knowledge_tools,
    logistics_tools,
    order_tools,
    refund_tools,
    ticket_tools,
)
from app.tools.base import ToolRegistry

_MODULES = [order_tools, logistics_tools, refund_tools, ticket_tools, knowledge_tools]


def build_tool_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for mod in _MODULES:
        for tool in mod.TOOLS:
            reg.register(tool)
    return reg
