"""Logistics 工具。"""
from __future__ import annotations

from app.core.config import get_settings
from app.services import logistics_service
from app.tools.base import RegisteredTool, ToolContext, err, ok


def get_logistics_status(ctx: ToolContext, order_id: int):
    logi = logistics_service.get_logistics(ctx.db, order_id)
    if not logi:
        return err("logistics_not_found", "暂无物流信息")
    return ok({
        "status": logi.status, "carrier": logi.carrier, "tracking_no": logi.tracking_no,
        "last_location": logi.last_location,
        "last_update_time": logi.last_update_time.isoformat() if logi.last_update_time else None,
        "is_exception": logi.is_exception,
    })


def check_logistics_exception(ctx: ToolContext, order_id: int):
    stale_hours = get_settings().logistics_stale_hours
    res = logistics_service.detect_exception(ctx.db, order_id, stale_hours)
    if not res.get("found"):
        return err("logistics_not_found", "暂无物流信息")
    # ToolResult 会同时写 JSON 审计并回灌给模型，不能携带 datetime 等非 JSON 类型。
    if res.get("last_update_time") is not None:
        res["last_update_time"] = res["last_update_time"].isoformat()
    return ok(res)


TOOLS = [
    RegisteredTool("get_logistics_status", "查询订单物流状态。",
                   {"type": "object", "properties": {"order_id": {"type": "integer"}},
                    "required": ["order_id"]}, get_logistics_status),
    RegisteredTool("check_logistics_exception", "判断物流是否异常（含48h无更新）。",
                   {"type": "object", "properties": {"order_id": {"type": "integer"}},
                    "required": ["order_id"]}, check_logistics_exception),
]
