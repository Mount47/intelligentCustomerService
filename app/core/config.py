"""集中配置：所有配置走 .env，代码内不写死（含 LLM provider/模型、业务阈值）。

通过 `get_settings()` 获取单例。详见 docs/项目设计/01-总体设计.md §13 约定。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 应用
    app_name: str = "SupportFlow"
    app_env: str = "local"
    debug: bool = True
    log_level: str = "INFO"

    # 数据库 / Redis
    database_url: str = "postgresql+psycopg://supportflow:supportflow@db:5432/supportflow"
    redis_url: str = "redis://redis:6379/0"

    # Celery
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
    # 任务调度：celery（生产）| thread（本地无 broker，后台线程直接跑）
    agent_dispatch: str = "celery"

    # LLM —— provider 可插拔，模型名不写死代码（ADR-11）
    llm_provider: str = "claude"   # claude | openai_compat | deepseek | qwen | gpt | vllm | ollama | stub
    llm_model: str = "claude-opus-4-8"
    llm_max_tokens: int = 16000
    llm_thinking: str = "adaptive"  # adaptive | off（仅 Claude 4.x 生效）
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""       # OpenAI兼容厂商的 base_url（DeepSeek/Qwen/本地 vLLM 等）

    # 业务阈值（配置化）
    refund_high_amount_threshold: float = 500.0
    refund_return_window_days: int = 7
    logistics_stale_hours: int = 48

    # SLA（小时）
    sla_default_hours: int = 24
    sla_high_priority_hours: int = 4


@lru_cache
def get_settings() -> Settings:
    """进程级单例，避免重复读取 .env。"""
    return Settings()
