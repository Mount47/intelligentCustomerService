"""按 .env 的 LLM_PROVIDER 选适配器（ADR-11）。切模型/厂商=改配置，不动代码。"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMError
from app.llm.base import LLMClient

_CLAUDE = {"claude", "anthropic"}
_OPENAI_COMPAT = {"openai", "openai_compat", "deepseek", "qwen", "gpt", "vllm", "ollama"}
_STUB = {"stub", "mock"}


def build_llm_client(settings: Settings | None = None, model: str | None = None) -> LLMClient:
    """model 覆盖：同一 provider/key/base_url 下换模型（如裁判用更强的 qwen-max）。"""
    settings = settings or get_settings()
    provider = (settings.llm_provider or "").lower()
    use_model = model or settings.llm_model

    if provider in _CLAUDE:
        from app.llm.claude_adapter import ClaudeAdapter
        return ClaudeAdapter(
            model=use_model, api_key=settings.anthropic_api_key,
            max_tokens=settings.llm_max_tokens, thinking=settings.llm_thinking,
        )
    if provider in _OPENAI_COMPAT:
        from app.llm.openai_compat_adapter import OpenAICompatAdapter
        return OpenAICompatAdapter(
            model=use_model, api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None, max_tokens=settings.llm_max_tokens,
        )
    if provider in _STUB:
        from app.llm.stub import StubLLMClient
        return StubLLMClient()
    raise LLMError(f"unknown LLM_PROVIDER: {provider!r}")
