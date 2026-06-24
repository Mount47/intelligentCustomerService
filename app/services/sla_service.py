"""SLA：按优先级算截止时间 + 落 sla_records + 解决时回填 + 达成统计（闭环）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import SlaRecord


def compute_deadline(
    priority: str, now: datetime | None = None, settings: Settings | None = None
) -> datetime:
    settings = settings or get_settings()
    now = now or datetime.utcnow()
    hours = settings.sla_high_priority_hours if priority == "high" else settings.sla_default_hours
    return now + timedelta(hours=hours)


def create_sla_record(
    db: Session, ticket_id: int, sla_type: str, deadline: datetime
) -> SlaRecord:
    rec = SlaRecord(ticket_id=ticket_id, sla_type=sla_type, deadline=deadline)
    db.add(rec)
    db.flush()
    return rec


def mark_resolved(db: Session, ticket_id: int, now: datetime | None = None) -> list[SlaRecord]:
    """工单解决时回填其未结 SLA 的 resolved_at + 是否超时（闭环：此前只写不更新）。"""
    now = now or datetime.utcnow()
    recs = list(db.scalars(select(SlaRecord).where(
        SlaRecord.ticket_id == ticket_id, SlaRecord.resolved_at.is_(None))).all())
    for r in recs:
        r.resolved_at = now
        r.is_timeout = now > r.deadline      # 解决时已过截止 → 超时
    return recs


def sla_stats(db: Session, now: datetime | None = None) -> dict:
    """SLA 达成统计（运维可观测）。breached = 解决迟了 或 未解决已过期（正在违约）。"""
    now = now or datetime.utcnow()
    recs = list(db.scalars(select(SlaRecord)).all())
    met = breached = pending = 0
    for r in recs:
        if r.resolved_at is not None:
            breached += 1 if (r.is_timeout or r.resolved_at > r.deadline) else 0
            met += 0 if (r.is_timeout or r.resolved_at > r.deadline) else 1
        elif now > r.deadline:               # 未解决且已过期 → 当前正在违约（该告警）
            breached += 1
        else:
            pending += 1
    total = len(recs)
    return {"total": total, "met": met, "breached": breached, "pending": pending,
            "met_rate": round(met / total, 3) if total else 1.0}
