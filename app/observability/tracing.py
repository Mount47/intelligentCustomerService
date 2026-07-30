"""轻量 tracing：每次 Agent 处理生成 trace_id，经 contextvar 贯穿日志（§8 可观测）。

setup_logging 装上 TraceFilter 后，所有日志自动带 [trace_id]，串联一次会话的全链路。
"""
from __future__ import annotations

import contextvars
import logging
import re
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)


def clear_trace_id() -> None:
    """清除当前执行上下文，避免长驻 worker 的下一项任务继承旧请求。"""
    _trace_id.set("-")


def use_or_create_trace_id(candidate: str | None) -> str:
    """接受安全的上游 trace id；非法/缺失时生成新的，避免日志注入。"""
    if candidate and _SAFE_TRACE_ID.fullmatch(candidate):
        set_trace_id(candidate)
        return candidate
    return new_trace_id()


def get_trace_id() -> str:
    return _trace_id.get()


class TraceFilter(logging.Filter):
    """给每条日志记录注入 trace_id（无则 '-'）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True
