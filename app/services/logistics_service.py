"""物流业务逻辑：状态查询 + 异常判断（48h 无更新 / 标记异常）。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Logistics

_NOT_RECEIVED = ("没收到", "未收到", "没收", "未签收", "没到货")
_DELIVERY_PLACES = ("门口", "驿站", "快递柜", "丰巢", "前台", "物业", "代收点", "收件箱", "邻居")
_ABSENCE_WORDS = ("没有", "没找到", "找不到", "不在", "是空的", "都没", "也没")


def reports_not_received(message: str) -> bool:
    """识别直接和间接的“签收但实物不存在”表达，供意图层与技能层共用。"""
    normalized = re.sub(r"[\s，。！？、,.!?]", "", message or "")
    if any(cue in normalized for cue in _NOT_RECEIVED):
        return True
    has_place = any(place in normalized for place in _DELIVERY_PLACES)
    has_absence = any(word in normalized for word in _ABSENCE_WORDS)
    searched_but_missing = bool(re.search(
        r"(?:找遍|找了|看了|查了).*(?:没有|没找到|找不到)", normalized
    ))
    return (has_place and has_absence) or searched_but_missing


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
