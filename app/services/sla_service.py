"""SLA：按优先级计算截止时间 + 落 sla_records。"""
from __future__ import annotations

from datetime import datetime, timedelta

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
