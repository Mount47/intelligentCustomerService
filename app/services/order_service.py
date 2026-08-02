"""订单业务逻辑。Agent 不直接碰 DB，经此 service 或 tool（§3 分层纪律）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Order, OrderItem


def get_order(db: Session, order_id: int) -> Order | None:
    return db.get(Order, order_id)


def get_user_orders(db: Session, user_id: int) -> list[Order]:
    return list(db.scalars(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.user_id == user_id)
        .order_by(Order.id.desc())
    ).all())


def get_order_items(db: Session, order_id: int) -> list[OrderItem]:
    """订单的商品明细（条目级事实，供"我买了什么"查询）。"""
    return list(db.scalars(
        select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)).all())


def check_owner(db: Session, order_id: int, user_id: int) -> bool:
    o = db.get(Order, order_id)
    return bool(o and o.user_id == user_id)
