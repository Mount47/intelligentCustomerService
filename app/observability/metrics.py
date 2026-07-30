"""运行时可观测指标（§8、ADR-12）。压测削峰观察 + 管理端 dashboard 共用。

compute_metrics：状态分布 + 队列深度 + agent 表现 + token/成本聚合。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AgentSession, AgentToolCall, RefundRequest, Ticket
from app.db.redis_client import celery_broker_client

logger = get_logger(__name__)

_PROCESSED = {"completed", "need_human", "waiting_user_input", "failed", "timeout"}


def _queue_depth(client=None) -> int | None:
    """Celery 默认队列深度（Redis LLEN）。Redis 不可达返回 None，不抛。"""
    client = client if client is not None else celery_broker_client
    if client is None:
        return None
    try:
        return int(client.llen("celery"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("queue_depth unavailable: %s", exc)
        return None


def compute_metrics(db: Session) -> dict:
    status_rows = db.execute(select(
        AgentSession.task_status, func.count(AgentSession.id),
    ).group_by(AgentSession.task_status)).all()
    by_status = {status: int(count) for status, count in status_rows}
    n = sum(by_status.values())
    processed = sum(by_status.get(k, 0) for k in _PROCESSED)
    completed = by_status.get("completed", 0)
    need_human = by_status.get("need_human", 0)

    duration_rows = db.execute(select(
        AgentSession.started_at, AgentSession.finished_at,
    ).where(
        AgentSession.started_at.is_not(None),
        AgentSession.finished_at.is_not(None),
    ).order_by(AgentSession.id.desc()).limit(2000)).all()
    durations = [(finished - started).total_seconds() * 1000
                 for started, finished in duration_rows]
    avg_ms = round(sum(durations) / len(durations), 1) if durations else 0.0

    total_tokens, total_cost = db.execute(select(
        func.coalesce(func.sum(AgentSession.total_tokens), 0),
        func.coalesce(func.sum(AgentSession.estimated_cost), 0),
    )).one()
    total_tokens = int(total_tokens or 0)
    total_cost = float(total_cost or 0)
    calls_total = db.scalar(select(func.count()).select_from(AgentToolCall)) or 0
    calls_ok = db.scalar(select(func.count()).select_from(AgentToolCall)
                         .where(AgentToolCall.success.is_(True))) or 0

    from app.services import sla_service
    return {
        "sessions_by_task_status": by_status,
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
