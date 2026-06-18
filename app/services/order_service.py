"""订单业务逻辑。Agent 不直接碰 DB，经此 service 或 tool（§3 分层纪律）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Order


def get_order(db: Session, order_id: int) -> Order | None:
    return db.get(Order, order_id)


def get_user_orders(db: Session, user_id: int) -> list[Order]:
    return list(db.scalars(select(Order).where(Order.user_id == user_id)).all())


def check_owner(db: Session, order_id: int, user_id: int) -> bool:
    o = db.get(Order, order_id)
    return bool(o and o.user_id == user_id)
