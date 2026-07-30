"""Celery 应用。异步消费 Agent 推理任务（§10、§15）。

M1 仅建实例 + 一个 ping 任务验证 worker 起得来；
process_agent_message 在 M6 落（agent_tasks.py）。
`include` 声明任务模块，worker 启动时加载并注册 agent.process_message，
否则 worker 收到该任务会因未注册而丢弃（KeyError: 'agent.process_message'）。
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    task_postrun,
    task_prerun,
)

from app.core.config import get_settings
from app.observability.tracing import TraceFilter, clear_trace_id, use_or_create_trace_id

_settings = get_settings()

celery_app = Celery(
    "supportflow",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["app.workers.agent_tasks", "app.workers.quality_tasks"],
)

celery_app.conf.update(
    task_acks_late=True,            # 任务执行完才 ack，崩溃可重投
    task_track_started=True,        # 记录 processing 状态
    worker_prefetch_multiplier=1,   # LLM 任务重，限制预取以平滑削峰
    task_time_limit=120,            # 硬超时（秒）
    task_soft_time_limit=100,       # 软超时，给清理机会
)


def _install_trace_filter(logger: logging.Logger, **_kwargs) -> None:
    """Celery 会接管 root handler；重新注入 trace filter/格式，保留 API→worker trace。"""
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] [%(trace_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for handler in logger.handlers:
        handler.addFilter(TraceFilter())
        handler.setFormatter(formatter)


after_setup_logger.connect(_install_trace_filter)
after_setup_task_logger.connect(_install_trace_filter)


@task_prerun.connect
def _bind_task_trace(kwargs=None, **_signal_kwargs) -> None:
    """每个 Celery 任务建立独立日志上下文；业务任务通过关键字参数继承 API trace。"""
    use_or_create_trace_id((kwargs or {}).get("trace_id"))


@task_postrun.connect
def _clear_task_trace(**_signal_kwargs) -> None:
    """任务结束后清空长驻进程上下文，防止后续任务串用 trace。"""
    clear_trace_id()


from app.observability.otel import configure_celery_otel
configure_celery_otel()


@celery_app.task(name="health.ping")
def ping() -> str:
    """烟雾测试：确认 worker 能消费任务。"""
    return "pong"
