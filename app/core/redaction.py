"""审计数据脱敏：保留排障结构，不把凭据、联系方式和账号原文写入工具审计。"""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|phone|mobile|email|"
    r"bank|account|card|idempotency)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY = re.compile(r"\b(?:sk|ak)-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"(?<!\d)\d{12,19}(?!\d)")
_MAX_AUDIT_STRING = 2000


def redact_text(value: str) -> str:
    text = _EMAIL.sub("<redacted-email>", value)
    text = _PHONE.sub("<redacted-phone>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _API_KEY.sub("<redacted-key>", text)
    text = _LONG_DIGITS.sub("<redacted-account>", text)
    if len(text) > _MAX_AUDIT_STRING:
        text = text[:_MAX_AUDIT_STRING] + "…<truncated>"
    return text


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
