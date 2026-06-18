"""OpenAI 兼容适配器（openai SDK + base_url）。

覆盖 GPT / DeepSeek / Qwen / 本地 vLLM·Ollama —— 它们都暴露 OpenAI 兼容的
chat.completions + function calling。SDK 延迟导入，测试可注入 client。
"""
from __future__ import annotations

import json

from app.core.logging import get_logger
from app.llm.base import LLMResponse, Msg, ToolCall, ToolSpec, Usage

logger = get_logger(__name__)

# OpenAI finish_reason → 统一 stop_reason
_STOP_MAP = {"stop": "end_turn", "tool_calls": "tool_use",
             "length": "max_tokens", "content_filter": "refusal"}


def _to_openai_messages(system: str, messages: list[Msg]) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "system":
            out.append({"role": "system", "content": m.content})
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append({
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                    for tc in m.tool_calls
                ],
            })
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _parse_openai(resp) -> LLMResponse:
    choice = resp.choices[0]
    msg = choice.message
    tool_calls: list[ToolCall] = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (ValueError, TypeError):
            args = {}
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
    u = getattr(resp, "usage", None)
    usage = Usage(
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        total_tokens=getattr(u, "total_tokens", 0) or 0,
    )
    return LLMResponse(
        text=msg.content or "",
        tool_calls=tool_calls,
        stop_reason=_STOP_MAP.get(choice.finish_reason, "end_turn"),
        usage=usage,
    )


class OpenAICompatAdapter:
    def __init__(self, *, model: str, api_key: str = "", base_url: str | None = None,
                 max_tokens: int = 16000, client=None) -> None:
        self.model_name = model
        self._api_key = api_key
        self._base_url = base_url
        self._max_tokens = max_tokens
        self._client = client

    def _client_or_make(self):
        if self._client is None:
            from openai import OpenAI  # 延迟导入
            self._client = OpenAI(api_key=self._api_key or None, base_url=self._base_url or None)
        return self._client

    def chat(self, *, system: str, messages: list[Msg],
             tools: list[ToolSpec] | None = None, stream: bool = False) -> LLMResponse:
        kwargs: dict = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "messages": _to_openai_messages(system, messages),
        }
        if tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.input_schema}}
                for t in tools
            ]
        resp = self._client_or_make().chat.completions.create(**kwargs)
        return _parse_openai(resp)
