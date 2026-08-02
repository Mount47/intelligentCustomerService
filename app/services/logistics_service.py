"""物流业务逻辑：状态查询 + 异常判断（48h 无更新 / 标记异常）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Logistics


def get_logistics(db: Session, order_id: int) -> Logistics | None:
    return db.scalar(select(Logistics).where(Logistics.order_id == order_id))


def is_stale(logi: Logistics, stale_hours: int, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return bool(
        logi.last_update_time
        and (now - logi.last_update_time) > timedelta(hours=stale_hours)
    )


def detect_exception(
    db: Session, order_id: int, stale_hours: int, now: datetime | None = None
) -> dict:
    """返回结构化判断。'显示签收但用户未收到' 需结合用户描述，由 Skill 判，不在此。"""
    logi = get_logistics(db, order_id)
    if not logi:
        return {"found": False}
    stale = is_stale(logi, stale_hours, now)
    is_exc = bool(logi.is_exception or stale or logi.status == "exception")
    reason = logi.exception_reason or (f"超过{stale_hours}小时无更新" if stale else None)
    return {
        "found": True,
        "status": logi.status,
        "carrier": logi.carrier,
        "tracking_no": logi.tracking_no,
        "last_location": logi.last_location,
        "last_update_time": logi.last_update_time,
        "is_exception": is_exc,
        "exception_reason": reason,
        "stale": stale,
        "delivered": logi.status == "delivered",
    }
