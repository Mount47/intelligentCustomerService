"""事务发件箱：与业务数据同事务记录待投递事件，再由 relay 补投（可靠投递）。

问题：`accept_message` 提交后才调用 `celery.delay`。两者不在一个事务里，进程在中间
崩溃、或 broker 不可达时，会话会永远停在 queued，用户等不到回复。

做法：
  1. `record_event` 在业务事务内 `db.add`，不自行提交——由调用方的 commit 一并落库；
  2. API 提交后仍然内联投递一次（快路，不牺牲延迟），成功则 `mark_sent`；
  3. 内联投递失败就把事件留在 pending，由 `app.workers.outbox_relay` 轮询补投。

投递语义是 at-least-once。重复投递安全，因为 `runner._claim_session` 只允许
queued/failed/timeout 进入 processing，重复消费是幂等 no-op。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import OutboxEvent

logger = get_logger(__name__)

AGENT_TOPIC = "agent.process_message"

STATUS_PENDING = "pending"
STATUS_SENT = "sent"


def dedup_key_for(topic: str, session_id: int) -> str:
    return f"{topic}:{session_id}"


def record_event(
    db: Session,
    topic: str,
    payload: dict,
    dedup_key: str,
    *,
    visible_after_sec: float = 0.0,
) -> OutboxEvent:
    """在当前事务内登记一个待投递事件；不提交，由调用方的事务边界决定成败。

    visible_after_sec 把事件对 relay 的可见时间后移一小段，避免 relay 和 API 的
    内联投递在正常情况下抢同一条事件、造成无谓的重复投递。
    """
    available_at = datetime.utcnow() + timedelta(seconds=visible_after_sec)
    event = OutboxEvent(
        topic=topic,
        payload=payload,
        dedup_key=dedup_key,
        status=STATUS_PENDING,
        available_at=available_at,
    )
    db.add(event)
    return event


def mark_sent(db: Session, dedup_key: str) -> bool:
    """内联投递成功后销账。失败不抛——销账失败最多让 relay 多投一次，是安全方向。"""
    try:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.dedup_key == dedup_key))
        if event is None or event.status == STATUS_SENT:
            return False
        event.status = STATUS_SENT
        event.sent_at = datetime.utcnow()
        event.attempts += 1
        db.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — 销账失败只影响重复投递，不影响正确性
        db.rollback()
        logger.warning("outbox mark_sent failed for %s: %s", dedup_key, exc)
        return False


def backlog_stats(db: Session, *, stuck_attempts: int = 3) -> dict:
    """积压与失败统计，供 /api/admin/metrics 暴露与告警。"""
    pending = db.scalar(
        select(func.count()).select_from(OutboxEvent)
        .where(OutboxEvent.status == STATUS_PENDING)
    ) or 0
    stuck = db.scalar(
        select(func.count()).select_from(OutboxEvent)
        .where(OutboxEvent.status == STATUS_PENDING,
               OutboxEvent.attempts >= stuck_attempts)
    ) or 0
    oldest = db.scalar(
        select(func.min(OutboxEvent.created_at))
        .where(OutboxEvent.status == STATUS_PENDING)
    )
    age_sec = round((datetime.utcnow() - oldest).total_seconds(), 1) if oldest else 0.0
    return {
        "pending": int(pending),
        "stuck": int(stuck),          # 连续投递失败达阈值，需要人工介入
        "oldest_pending_age_sec": age_sec,
    }
