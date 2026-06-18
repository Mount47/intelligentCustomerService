"""轻量 tracing：每次 Agent 处理生成 trace_id，经 contextvar 贯穿日志（§8 可观测）。

setup_logging 装上 TraceFilter 后，所有日志自动带 [trace_id]，串联一次会话的全链路。
"""
from __future__ import annotations

import contextvars
import logging
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)


def get_trace_id() -> str:
    return _trace_id.get()


class TraceFilter(logging.Filter):
    """给每条日志记录注入 trace_id（无则 '-'）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True
