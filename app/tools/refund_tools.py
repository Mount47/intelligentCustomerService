"""Refund 工具（写操作，危险）。create_refund_draft 走三层幂等（refund_service）。"""
from __future__ import annotations

from app.core.exceptions import IdempotencyKeyConflict, SupportFlowError
from app.services import refund_service
from app.tools.base import RegisteredTool, ToolContext, err, ok


def check_refund_policy(ctx: ToolContext, order_id: int, user_id: int, refund_reason: str = ""):
    return ok(refund_service.check_refund_policy(ctx.db, order_id, user_id, refund_reason))


def create_refund_draft(
    ctx: ToolContext, order_id: int, user_id: int, refund_reason: str = "",
    idempotency_key: str | None = None,
):
    try:
        res = refund_service.create_refund_draft(
            ctx.db, user_id=user_id, order_id=order_id,
            refund_reason=refund_reason, idempotency_key=idempotency_key,
        )
        return ok(res)
    except IdempotencyKeyConflict as e:
        return err("idempotency_key_conflict", str(e))
    except SupportFlowError as e:
        return err("refund_failed", str(e))


def get_refund_status(ctx: ToolContext, refund_id: int):
    res = refund_service.get_refund_status(ctx.db, refund_id)
    if res is None:
        return err("refund_not_found", "退款单不存在")
    return ok(res)


TOOLS = [
    RegisteredTool("check_refund_policy", "核对退款资格/风险/是否需人工，引用政策。",
                   {"type": "object",
                    "properties": {"order_id": {"type": "integer"},
                                   "user_id": {"type": "integer"},
                                   "refund_reason": {"type": "string"}},
                    "required": ["order_id", "user_id"]}, check_refund_policy),
    RegisteredTool("create_refund_draft", "创建退款草稿（幂等）。高风险将标记需人工审核。",
                   {"type": "object",
                    "properties": {"order_id": {"type": "integer"},
                                   "user_id": {"type": "integer"},
                                   "refund_reason": {"type": "string"},
                                   "idempotency_key": {"type": "string"}},
                    "required": ["order_id", "user_id"]}, create_refund_draft),
    RegisteredTool("get_refund_status", "查询退款单状态。",
                   {"type": "object", "properties": {"refund_id": {"type": "integer"}},
                    "required": ["refund_id"]}, get_refund_status),
]
