"""当前登录用户的订单查询接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import Principal, get_current_principal
from app.db.models import Order
from app.db.session import get_db
from app.schemas.order import OrderItemView, UserOrderView
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _to_view(order: Order) -> UserOrderView:
    return UserOrderView(
        id=order.id,
        order_no=order.order_no,
        status=order.status,
        total_amount=float(order.total_amount),
        product_type=order.product_type,
        paid_at=order.paid_at,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        created_at=order.created_at,
        items=[
            OrderItemView(
                id=item.id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=float(item.unit_price),
            )
            for item in order.items
        ],
    )


@router.get("", response_model=list[UserOrderView])
def list_my_orders(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[UserOrderView]:
    """只返回令牌所属用户的订单，不接受前端传入用户编号。"""
    return [_to_view(order) for order in order_service.get_user_orders(db, principal.user_id)]


@router.get("/{order_id}", response_model=UserOrderView)
def get_my_order(
    order_id: int,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> UserOrderView:
    """查询本人订单；他人订单统一返回不存在，避免泄露订单是否存在。"""
    order = order_service.get_order(db, order_id)
    if order is None or order.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="order not found")
    return _to_view(order)
