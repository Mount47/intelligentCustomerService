"""按 .env 的 LLM_PROVIDER 选适配器（ADR-11）。切模型/厂商=改配置，不动代码。"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMError
from app.llm.base import LLMClient

_CLAUDE = {"claude", "anthropic"}
_OPENAI_COMPAT = {"openai", "openai_compat", "deepseek", "qwen", "gpt", "vllm", "ollama"}
_STUB = {"stub", "mock"}


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    provider = (settings.llm_provider or "").lower()

    if provider in _CLAUDE:
        from app.llm.claude_adapter import ClaudeAdapter
        return ClaudeAdapter(
            model=settings.llm_model, api_key=settings.anthropic_api_key,
            max_tokens=settings.llm_max_tokens, thinking=settings.llm_thinking,
        )
    if provider in _OPENAI_COMPAT:
        from app.llm.openai_compat_adapter import OpenAICompatAdapter
        return OpenAICompatAdapter(
            model=settings.llm_model, api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None, max_tokens=settings.llm_max_tokens,
        )
    if provider in _STUB:
        from app.llm.stub import StubLLMClient
        return StubLLMClient()
    raise LLMError(f"unknown LLM_PROVIDER: {provider!r}")
