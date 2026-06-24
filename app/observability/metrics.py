"""运行时可观测指标（§8、ADR-12）。压测削峰观察 + 管理端 dashboard 共用。

compute_metrics：状态分布 + 队列深度 + agent 表现 + token/成本聚合。
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AgentSession, AgentToolCall, RefundRequest, Ticket
from app.db.redis_client import redis_client

logger = get_logger(__name__)

_PROCESSED = {"completed", "need_human", "waiting_user_input", "failed", "timeout"}


def _queue_depth() -> int | None:
    """Celery 默认队列深度（Redis LLEN）。Redis 不可达返回 None，不抛。"""
    try:
        return int(redis_client.llen("celery"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("queue_depth unavailable: %s", exc)
        return None


def compute_metrics(db: Session) -> dict:
    sessions = list(db.scalars(select(AgentSession)).all())
    n = len(sessions)
    by_status = Counter(s.task_status for s in sessions)
    processed = sum(by_status.get(k, 0) for k in _PROCESSED)
    completed = by_status.get("completed", 0)
    need_human = by_status.get("need_human", 0)

    durations = [
        (s.finished_at - s.started_at).total_seconds() * 1000
        for s in sessions if s.started_at and s.finished_at
    ]
    avg_ms = round(sum(durations) / len(durations), 1) if durations else 0.0

    total_tokens = sum(s.total_tokens or 0 for s in sessions)
    total_cost = float(sum(s.estimated_cost or 0 for s in sessions))
    calls_total = db.scalar(select(func.count()).select_from(AgentToolCall)) or 0
    calls_ok = db.scalar(select(func.count()).select_from(AgentToolCall)
                         .where(AgentToolCall.success.is_(True))) or 0

    from app.services import sla_service
    return {
        "sessions_by_task_status": dict(by_status),
        "queue_depth": _queue_depth(),
        "sla": sla_service.sla_stats(db),
        "totals": {
            "sessions": n,
            "tickets": db.scalar(select(func.count()).select_from(Ticket)) or 0,
            "refund_requests": db.scalar(select(func.count()).select_from(RefundRequest)) or 0,
            "tool_calls": calls_total,
        },
        "agent": {
            "resolution_rate": round(completed / processed, 3) if processed else 0.0,
            "handoff_rate": round(need_human / processed, 3) if processed else 0.0,
            "tool_call_success_rate": round(calls_ok / calls_total, 3) if calls_total else 1.0,
            "avg_processing_ms": avg_ms,
        },
        "cost": {
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "avg_tokens_per_session": round(total_tokens / n, 1) if n else 0.0,
        },
    }
