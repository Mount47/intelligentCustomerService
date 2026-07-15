"""上下文超限软着陆：确定性压缩一次，再失败交给 Agent 转人工。"""
from __future__ import annotations

from app.core.logging import get_logger
from app.llm.base import LLMClient, LLMResponse, Msg, ToolSpec
from app.llm.errors import ContextWindowExceeded

logger = get_logger(__name__)


def _trim_content(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    marker = "\n[内容已紧急截断]\n"
    if limit <= len(marker) + 2:
        return content[:limit]
    available = limit - len(marker)
    head = available * 2 // 3
    tail = available - head
    return content[:head] + marker + content[-tail:]


def emergency_compact(messages: list[Msg], *, char_budget: int) -> list[Msg]:
    """丢弃当前用户轮次之前的软历史，并压缩当前轮次；保留工具调用配对后缀。"""
    if not messages:
        return messages

    # 最后一个 user 是当前请求；它之后可能已有 assistant tool_use + tool_result，必须成组保留。
    current_user = next(
        (index for index in range(len(messages) - 1, -1, -1)
         if messages[index].role == "user"),
        len(messages) - 1,
    )
    suffix = messages[current_user:]
    budget = max(128, char_budget)
    per_message = max(1, budget // max(1, len(suffix)))
    return [
        Msg(
            role=message.role,
            content=_trim_content(message.content or "", per_message),
            tool_call_id=message.tool_call_id,
            tool_calls=message.tool_calls,
        )
        for message in suffix
    ]


class ContextBackstopLLMClient:
    """包装任意 LLMClient；仅在输入确实能缩短时做一次重试。"""

    def __init__(self, client: LLMClient, *, char_budget: int = 3000) -> None:
        self._client = client
        self._char_budget = char_budget
        self.model_name = getattr(client, "model_name", "")

    def chat(self, *, system: str, messages: list[Msg],
             tools: list[ToolSpec] | None = None, stream: bool = False) -> LLMResponse:
        try:
            return self._client.chat(system=system, messages=messages, tools=tools, stream=stream)
        except ContextWindowExceeded:
            compacted = emergency_compact(messages, char_budget=self._char_budget)
            if compacted == messages:
                raise
            logger.warning(
                "LLM context exceeded; retry once after emergency compaction (%d → %d messages)",
                len(messages), len(compacted),
            )
            return self._client.chat(
                system=system, messages=compacted, tools=tools, stream=stream,
            )
