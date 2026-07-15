"""按 .env 的 LLM_PROVIDER 选适配器（ADR-11）。切模型/厂商=改配置，不动代码。"""
from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMError
from app.llm.base import LLMClient

_CLAUDE = {"claude", "anthropic"}
_OPENAI_COMPAT = {"openai", "openai_compat", "deepseek", "qwen", "gpt", "vllm", "ollama"}
_STUB = {"stub", "mock"}


def build_llm_client(settings: Settings | None = None, model: str | None = None) -> LLMClient:
    """构建 provider 客户端，并套上下文 backstop / 可选熔断降级。

    model 覆盖：同一 provider/key/base_url 下换模型（如裁判用更强的 qwen-max）。
    """
    settings = settings or get_settings()
    client = _build_raw(settings, model)
    if settings.circuit_breaker_enabled:
        from app.llm.circuit_breaker import CircuitBreakerLLMClient
        # fallback：配了降级模型且与主模型不同 → 熔断时无缝切它；否则熔断=快速失败
        fb_model = settings.llm_fallback_model
        fallback = (_build_raw(settings, fb_model)
                    if fb_model and fb_model != (model or settings.llm_model) else None)
        client = CircuitBreakerLLMClient(
            client, fallback=fallback,
            fail_max=settings.circuit_breaker_fail_max,
            reset_timeout=settings.circuit_breaker_reset_sec)
    from app.llm.context_backstop import ContextBackstopLLMClient
    return ContextBackstopLLMClient(client, char_budget=settings.history_token_budget)


def _build_raw(settings: Settings, model: str | None = None) -> LLMClient:
    """构建裸适配器（不套熔断），供主客户端与 fallback 复用。"""
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
