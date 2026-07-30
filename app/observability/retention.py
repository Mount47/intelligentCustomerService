"""可配置审计保留期清理。生产可由 cron/Celery beat 每日执行。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import AgentToolCall


def purge_expired_audit(
    db: Session, *, days: int | None = None, now: datetime | None = None
) -> int:
    keep_days = get_settings().audit_retention_days if days is None else days
    if keep_days <= 0:
        return 0
    cutoff = (now or datetime.utcnow()) - timedelta(days=keep_days)
    result = db.execute(delete(AgentToolCall).where(AgentToolCall.created_at < cutoff))
    db.commit()
    return int(result.rowcount or 0)
