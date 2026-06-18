"""Stub LLM —— 仅用于 M1.5 走通骨架/单测，不调真实 API。

返回固定文本 + end_turn + 一份假 usage（让 token 记账链路也能跑通）。
真实 ClaudeAdapter 在 M4/M5 落（app/llm/claude_adapter.py）。
"""
from __future__ import annotations

import time

from app.llm.base import LLMResponse, Msg, ToolSpec, Usage


class StubLLMClient:
    model_name = "stub-llm"

    def __init__(self, canned: str = "（骨架占位回复）您的问题已收到，我们会尽快为您处理。") -> None:
        self._canned = canned

    def chat(
        self,
        *,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        # 压测可配模拟处理时延（STUB_DELAY_MS），让队列削峰更直观
        from app.core.config import get_settings
        delay = get_settings().stub_delay_ms
        if delay > 0:
            time.sleep(delay / 1000)
        # 不调工具：直接 end_turn，带一份假 usage 验证记账链路
        return LLMResponse(
            text=self._canned,
            stop_reason="end_turn",
            usage=Usage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )
