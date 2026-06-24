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
    cors_origins: str = "*"   # 逗号分隔的允许来源；本地联调默认全放开

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
    # LLM-as-Judge 裁判模型：同 provider/key 下换模型（空=与 Agent 同模型）。
    # 建议用更强/不同源的(如 Agent=qwen-plus，裁判=qwen-max)，压制自评偏好。
    judge_model: str = ""
    stub_delay_ms: int = 0          # 压测用：给 stub 模型加模拟处理时延，让队列削峰可见

    # 业务阈值（配置化）
    refund_high_amount_threshold: float = 500.0
    refund_return_window_days: int = 7
    logistics_stale_hours: int = 48

    # SLA（小时）
    sla_default_hours: int = 24
    sla_high_priority_hours: int = 4

    # 限流：每用户每分钟最多发起的消息数（<=0 关闭）。接入层前置削峰闸。
    chat_rate_limit_per_min: int = 60

    # LLM 熔断降级：连续失败 fail_max 次 → 熔断 reset_sec 秒，期间快速失败/走 fallback。
    circuit_breaker_enabled: bool = True
    circuit_breaker_fail_max: int = 3
    circuit_breaker_reset_sec: float = 30.0
    llm_fallback_model: str = ""    # 降级模型（同 provider 换更稳/更便宜；空=熔断时直接快速失败）


@lru_cache
def get_settings() -> Settings:
    """进程级单例，避免重复读取 .env。"""
    return Settings()
