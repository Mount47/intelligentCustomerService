"""登录用户订单接口的数据结构。"""
from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class OrderItemView(CamelModel):
    id: int
    product_name: str
    quantity: int
    unit_price: float


class UserOrderView(CamelModel):
    id: int
    order_no: str
    status: str
    total_amount: float
    product_type: str
    paid_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime | None = None
    items: list[OrderItemView] = []
