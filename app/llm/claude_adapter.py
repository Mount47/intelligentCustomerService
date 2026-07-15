"""Claude 适配器（anthropic SDK）。

把 provider 无关的 (system, messages, tools) 映射到 anthropic messages.create，
并把响应归一化回 LLMResponse（text / tool_calls / stop_reason / usage）。
SDK 延迟导入：未装 anthropic 或离线注入 client 时也能用（测试友好）。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.llm.base import LLMResponse, Msg, ToolCall, ToolSpec, Usage
from app.llm.errors import normalize_provider_error

logger = get_logger(__name__)


def _to_anthropic_messages(messages: list[Msg]) -> list[dict]:
    """Msg 列表 → anthropic messages（system 已单独传，不在此）。"""
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            continue  # 由 system 参数承载
        if m.role == "tool":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content,
                }],
            })
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id,
                               "name": tc.name, "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _parse_anthropic(resp) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
        elif btype == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        # thinking 块忽略（不外泄原始思维链）
    u = resp.usage
    usage = Usage(
        prompt_tokens=getattr(u, "input_tokens", 0) or 0,
        completion_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
    )
    usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=resp.stop_reason or "end_turn",
        usage=usage,
    )


class ClaudeAdapter:
    def __init__(self, *, model: str, api_key: str = "", max_tokens: int = 16000,
                 thinking: str = "adaptive", client=None) -> None:
        self.model_name = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._client = client

    def _client_or_make(self):
        if self._client is None:
            import anthropic  # 延迟导入
            self._client = anthropic.Anthropic(api_key=self._api_key or None)
        return self._client

    def chat(self, *, system: str, messages: list[Msg],
             tools: list[ToolSpec] | None = None, stream: bool = False) -> LLMResponse:
        kwargs: dict = {
            "model": self.model_name,
            "max_tokens": self._max_tokens,
            "system": system or "",
            "messages": _to_anthropic_messages(messages),
        }
        if self._thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        try:
            resp = self._client_or_make().messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 统一 provider 可恢复异常，未知类型原样抛出
            normalized = normalize_provider_error(exc)
            if normalized is exc:
                raise
            raise normalized from exc
        return _parse_anthropic(resp)
