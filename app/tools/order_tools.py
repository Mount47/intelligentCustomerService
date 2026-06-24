"""Order 工具。"""
from __future__ import annotations

from app.db.models import Order
from app.services import order_service
from app.tools.base import RegisteredTool, ToolContext, err, ok


def _order_dict(o: Order) -> dict:
    return {
        "order_id": o.id, "order_no": o.order_no, "status": o.status,
        "total_amount": float(o.total_amount), "product_type": o.product_type,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        "shipped_at": o.shipped_at.isoformat() if o.shipped_at else None,
        "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
    }


def get_order_detail(ctx: ToolContext, order_id: int, user_id: int | None = None):
    o = order_service.get_order(ctx.db, order_id)
    if not o:
        return err("order_not_found", "订单不存在")
    if user_id is not None and o.user_id != user_id:
        return err("not_owner", "订单不属于该用户")
    return ok(_order_dict(o))


def get_user_orders(ctx: ToolContext, user_id: int):
    orders = order_service.get_user_orders(ctx.db, user_id)
    return ok({"orders": [_order_dict(o) for o in orders]})


def get_order_items(ctx: ToolContext, order_id: int, user_id: int | None = None):
    o = order_service.get_order(ctx.db, order_id)
    if not o:
        return err("order_not_found", "订单不存在")
    if user_id is not None and o.user_id != user_id:
        return err("not_owner", "订单不属于该用户")
    items = order_service.get_order_items(ctx.db, order_id)
    return ok({"order_no": o.order_no,
               "items": [{"product_name": it.product_name, "quantity": it.quantity,
                          "unit_price": float(it.unit_price)} for it in items]})


def check_order_owner(ctx: ToolContext, order_id: int, user_id: int):
    return ok({"is_owner": order_service.check_owner(ctx.db, order_id, user_id)})


TOOLS = [
    RegisteredTool("get_order_detail", "查询订单详情（金额/状态/商品类型/时间）。",
                   {"type": "object",
                    "properties": {"order_id": {"type": "integer"},
                                   "user_id": {"type": "integer"}},
                    "required": ["order_id"]}, get_order_detail),
    RegisteredTool("get_user_orders", "查询某用户的全部订单。",
                   {"type": "object", "properties": {"user_id": {"type": "integer"}},
                    "required": ["user_id"]}, get_user_orders),
    RegisteredTool("get_order_items", "查询订单的商品明细（买了哪些商品/数量/单价）。",
                   {"type": "object",
                    "properties": {"order_id": {"type": "integer"},
                                   "user_id": {"type": "integer"}},
                    "required": ["order_id"]}, get_order_items),
    RegisteredTool("check_order_owner", "校验订单是否属于该用户。",
                   {"type": "object",
                    "properties": {"order_id": {"type": "integer"},
                                   "user_id": {"type": "integer"}},
                    "required": ["order_id", "user_id"]}, check_order_owner),
]
