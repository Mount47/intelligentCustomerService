"""Chat 接入与会话视图（§14、§10、ADR-10 steps 时间线）。

accept_message：消息幂等去重 → 建/取 ticket → 落用户消息 → 建 agent_session(queued)。
get_session_view：组装轮询视图（task_status + latest_reply + steps 时间线 + token）。
"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.exceptions import SupportFlowError
from app.db.models import AgentSession, AgentToolCall, Ticket, TicketMessage
from app.schemas.chat import ChatMessageIn, SessionView, Step, TokenView
from app.services import ticket_service

# 工具名 → 中文标签（思考过程时间线展示用）
_TOOL_LABELS = {
    "get_order_detail": "查询订单",
    "get_user_orders": "查询用户订单",
    "check_order_owner": "校验订单归属",
    "get_logistics_status": "查询物流",
    "check_logistics_exception": "判断物流异常",
    "check_refund_policy": "核对退款政策",
    "create_refund_draft": "创建退款草稿",
    "get_refund_status": "查询退款状态",
    "create_ticket": "创建工单",
    "update_ticket_status": "更新工单状态",
    "handoff_to_human": "转人工",
    "add_ticket_message": "追加消息",
    "search_policy_docs": "检索政策",
    "get_policy_by_category": "取政策",
}


def accept_message(db: Session, payload: ChatMessageIn) -> tuple[AgentSession, bool]:
    # 1 消息幂等去重（§9.1）：命中则返回已有 session，不重复入队
    if payload.client_message_id:
        dup = db.scalar(select(TicketMessage).where(
            TicketMessage.user_id == payload.user_id,
            TicketMessage.client_message_id == payload.client_message_id,
        ))
        if dup:
            sess = db.scalar(select(AgentSession).where(
                AgentSession.ticket_id == dup.ticket_id).order_by(desc(AgentSession.id)))
            if sess:
                return sess, True

    # 2 建/取 ticket
    if payload.ticket_id is not None:
        ticket = db.get(Ticket, payload.ticket_id)
        if ticket is None:
            raise SupportFlowError("ticket not found")
    else:
        ticket = ticket_service.create_ticket(
            db, payload.user_id, category="chat", order_id=payload.order_id)

    # 3 落用户消息
    ticket_service.add_message(
        db, ticket.id, "user", payload.content,
        user_id=payload.user_id, client_message_id=payload.client_message_id)

    # 4 建 agent_session(queued)
    sess = AgentSession(user_id=payload.user_id, ticket_id=ticket.id, task_status="queued")
    db.add(sess)
    db.flush()
    db.commit()
    return sess, False


def get_session_view(db: Session, session_id: int) -> SessionView | None:
    sess = db.get(AgentSession, session_id)
    if sess is None:
        return None

    steps: list[Step] = []
    if sess.current_intent:
        steps.append(Step(kind="intent", label="识别意图", detail=sess.current_intent))
    if sess.current_skill:
        steps.append(Step(kind="skill", label="选择技能", detail=sess.current_skill))
    for tc in db.scalars(select(AgentToolCall).where(
            AgentToolCall.session_id == session_id).order_by(AgentToolCall.id)).all():
        steps.append(Step(kind="tool", label=_TOOL_LABELS.get(tc.tool_name, tc.tool_name),
                          detail=tc.error_message, ok=tc.success))
    if sess.final_status:
        steps.append(Step(kind="state", label="处理结果", detail=sess.final_status))

    latest_reply = None
    if sess.ticket_id is not None:
        m = db.scalar(select(TicketMessage).where(
            TicketMessage.ticket_id == sess.ticket_id,
            TicketMessage.sender_type == "agent").order_by(desc(TicketMessage.id)))
        latest_reply = m.content if m else None

    return SessionView(
        session_id=sess.id, ticket_id=sess.ticket_id, task_status=sess.task_status,
        current_intent=sess.current_intent, current_skill=sess.current_skill,
        current_state=sess.current_state, latest_reply=latest_reply,
        final_status=sess.final_status, error_message=sess.error_message,
        retry_count=sess.retry_count, steps=steps,
        tokens=TokenView(model=sess.model_name, prompt=sess.prompt_tokens,
                         completion=sess.completion_tokens, total=sess.total_tokens,
                         cost=float(sess.estimated_cost or 0)),
        created_at=sess.created_at, started_at=sess.started_at, finished_at=sess.finished_at,
    )
