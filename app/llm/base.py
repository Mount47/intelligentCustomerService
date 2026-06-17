"""LLMClient 抽象层（provider 可插拔，§6）。

统一接口与 provider 无关的数据结构。Claude / OpenAI兼容 / 本地适配器都实现 LLMClient。
模型名运行时由 .env 注入，不写死（ADR-7/8 #6）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Msg:
    role: str            # user | assistant | tool | system
    content: str


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"        # end_turn | tool_use | max_tokens | refusal
    usage: Usage = field(default_factory=Usage)


class LLMClient(Protocol):
    """provider 无关接口。适配器实现 chat（含 tool calling、流式、usage 上报）。"""

    model_name: str

    def chat(
        self,
        *,
        system: str,
        messages: list[Msg],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
    ) -> LLMResponse: ...
