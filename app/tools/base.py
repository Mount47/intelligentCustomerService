"""工具层基建（§5.3、§10 工具要求）。

- 所有工具返回结构化 ToolResult（ok/data/error），失败不抛、不崩主流程
- harness 经 ToolRegistry.execute 调用：计时 + 写 agent_tool_calls + 生成 ToolCallRecord
- 危险/写工具内部做权限+幂等（refund/ticket）
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.context import ToolCallRecord, ToolResult
from app.core.logging import get_logger
from app.db.models import AgentToolCall
from app.llm.base import ToolSpec

logger = get_logger(__name__)


def ok(data: dict | None = None) -> ToolResult:
    return {"ok": True, "data": data or {}, "error": None}


def err(code: str, message: str) -> ToolResult:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


@dataclass
class ToolContext:
    """工具执行上下文。session_id 用于把调用写入 agent_tool_calls（无则跳过落库）。"""
    db: Session
    session_id: int | None = None


@dataclass
class RegisteredTool:
    name: str
    description: str
    input_schema: dict
    func: Callable[..., ToolResult]   # func(ctx, **args) -> ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def schema_props(self, name: str) -> set[str]:
        """某工具 input_schema 声明的参数名集合（供 harness 做上下文参数绑定）。"""
        t = self._tools.get(name)
        return set((t.input_schema or {}).get("properties", {})) if t else set()

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        """供 LLM 的工具规格（按白名单过滤）。"""
        sel = names or self.names()
        return [
            ToolSpec(t.name, t.description, t.input_schema)
            for n in sel
            if (t := self._tools.get(n))
        ]

    def execute(self, ctx: ToolContext, name: str, args: dict) -> tuple[ToolResult, ToolCallRecord]:
        start = time.perf_counter()
        tool = self._tools.get(name)
        if tool is None:
            result = err("unknown_tool", f"未注册的工具: {name}")
        else:
            try:
                result = tool.func(ctx, **(args or {}))
            except Exception as exc:  # noqa: BLE001 — 工具失败不崩主流程
                logger.exception("tool %s failed", name)
                result = err("tool_exception", str(exc))
        latency = int((time.perf_counter() - start) * 1000)
        success = bool(result.get("ok"))
        error_msg = None if success else (result.get("error") or {}).get("message")

        record = ToolCallRecord(
            tool_name=name, input_json=args or {}, result=result,
            success=success, latency_ms=latency, error_message=error_msg,
        )
        # 自动写审计（有 session 时）
        if ctx.session_id is not None:
            ctx.db.add(AgentToolCall(
                session_id=ctx.session_id, tool_name=name, input_json=args or {},
                output_json=result.get("data"), success=success,
                latency_ms=latency, error_message=error_msg,
            ))
            ctx.db.flush()
        logger.info("tool %s ok=%s latency=%dms", name, success, latency)
        return result, record
