"""Ticket 工具（写操作）。状态变更经状态机（ticket_service）。"""
from __future__ import annotations

from app.core.exceptions import SupportFlowError
from app.services import ticket_service
from app.tools.base import RegisteredTool, ToolContext, err, ok


def create_ticket(ctx: ToolContext, user_id: int, category: str,
                  priority: str = "normal", order_id: int | None = None):
    t = ticket_service.create_ticket(ctx.db, user_id, category, priority, order_id)
    return ok({"ticket_id": t.id, "status": t.status, "priority": t.priority})


def update_ticket_status(ctx: ToolContext, ticket_id: int, status: str):
    try:
        t = ticket_service.update_status(ctx.db, ticket_id, status)
        return ok({"ticket_id": t.id, "status": t.status})
    except SupportFlowError as e:
        return err("invalid_transition", str(e))


def handoff_to_human(ctx: ToolContext, ticket_id: int, reason: str):
    try:
        t = ticket_service.handoff(ctx.db, ticket_id, reason)
        return ok({"ticket_id": t.id, "status": t.status, "reason": reason})
    except SupportFlowError as e:
        return err("handoff_failed", str(e))


def add_ticket_message(ctx: ToolContext, ticket_id: int, sender_type: str, content: str):
    m = ticket_service.add_message(ctx.db, ticket_id, sender_type, content)
    return ok({"message_id": m.id})


TOOLS = [
    RegisteredTool("create_ticket", "创建工单。",
                   {"type": "object",
                    "properties": {"user_id": {"type": "integer"},
                                   "category": {"type": "string"},
                                   "priority": {"type": "string"},
                                   "order_id": {"type": "integer"}},
                    "required": ["user_id", "category"]}, create_ticket),
    RegisteredTool("update_ticket_status", "推进工单状态（经状态机校验）。",
                   {"type": "object",
                    "properties": {"ticket_id": {"type": "integer"},
                                   "status": {"type": "string"}},
                    "required": ["ticket_id", "status"]}, update_ticket_status),
    RegisteredTool("handoff_to_human", "转人工（状态→need_human）。",
                   {"type": "object",
                    "properties": {"ticket_id": {"type": "integer"},
                                   "reason": {"type": "string"}},
                    "required": ["ticket_id", "reason"]}, handoff_to_human),
    RegisteredTool("add_ticket_message", "向工单追加一条消息。",
                   {"type": "object",
                    "properties": {"ticket_id": {"type": "integer"},
                                   "sender_type": {"type": "string"},
                                   "content": {"type": "string"}},
                    "required": ["ticket_id", "sender_type", "content"]}, add_ticket_message),
]
