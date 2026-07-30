"""SLA：按优先级算截止时间 + 落 sla_records + 解决时回填 + 达成统计（闭环）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
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
    total = db.scalar(select(func.count()).select_from(SlaRecord)) or 0
    met = db.scalar(select(func.count()).select_from(SlaRecord).where(
        SlaRecord.resolved_at.is_not(None),
        SlaRecord.is_timeout.is_(False),
        SlaRecord.resolved_at <= SlaRecord.deadline,
    )) or 0
    breached = db.scalar(select(func.count()).select_from(SlaRecord).where(or_(
        and_(
            SlaRecord.resolved_at.is_not(None),
            or_(SlaRecord.is_timeout.is_(True), SlaRecord.resolved_at > SlaRecord.deadline),
        ),
        and_(SlaRecord.resolved_at.is_(None), SlaRecord.deadline < now),
    ))) or 0
    pending = max(0, total - met - breached)
    return {"total": total, "met": met, "breached": breached, "pending": pending,
            "met_rate": round(met / total, 3) if total else 1.0}
