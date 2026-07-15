"""LLM provider 异常归一化。

只识别可确定处理的上下文窗口超限；其他异常保留原样交给熔断/重试策略。
"""
from __future__ import annotations

from app.core.exceptions import LLMError


class ContextWindowExceeded(LLMError):
    """输入超过模型上下文窗口；压缩输入才可能恢复，原样重试没有意义。"""

    code = "context_window_exceeded"


_CONTEXT_CODES = {
    "context_length_exceeded",
    "context_window_exceeded",
    "input_too_long",
    "prompt_too_long",
}
_CONTEXT_MARKERS = (
    "maximum context length",
    "context length exceeded",
    "context window exceeded",
    "prompt is too long",
    "input is too long",
    "input too long",
    "input token length",
    "too many tokens",
)


def is_context_window_error(exc: Exception) -> bool:
    """按 provider code 优先、稳定消息片段兜底，保守识别上下文超限。"""
    values: list[object] = [
        getattr(exc, "code", None),
        getattr(exc, "type", None),
        getattr(exc, "body", None),
        str(exc),
    ]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            values.extend((error.get("code"), error.get("type"), error.get("message")))

    normalized = " ".join(str(value).lower() for value in values if value is not None)
    tokens = {part.strip("'\" ,:{}[]()") for part in normalized.split()}
    return bool(_CONTEXT_CODES & tokens) or any(marker in normalized for marker in _CONTEXT_MARKERS)


def normalize_provider_error(exc: Exception) -> Exception:
    """把已知可恢复类别转成 provider 无关异常，未知异常不改类型。"""
    if isinstance(exc, ContextWindowExceeded):
        return exc
    if is_context_window_error(exc):
        return ContextWindowExceeded("LLM 输入超过上下文窗口")
    return exc
