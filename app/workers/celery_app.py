"""Celery 应用。异步消费 Agent 推理任务（§10、§15）。

M1 仅建实例 + 一个 ping 任务验证 worker 起得来；
process_agent_message 在 M6 落（agent_tasks.py）。
`include` 声明任务模块，worker 启动时加载并注册 agent.process_message，
否则 worker 收到该任务会因未注册而丢弃（KeyError: 'agent.process_message'）。
"""
from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "supportflow",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["app.workers.agent_tasks"],
)

celery_app.conf.update(
    task_acks_late=True,            # 任务执行完才 ack，崩溃可重投
    task_track_started=True,        # 记录 processing 状态
    worker_prefetch_multiplier=1,   # LLM 任务重，限制预取以平滑削峰
    task_time_limit=120,            # 硬超时（秒）
    task_soft_time_limit=100,       # 软超时，给清理机会
)


@celery_app.task(name="health.ping")
def ping() -> str:
    """烟雾测试：确认 worker 能消费任务。"""
    return "pong"
