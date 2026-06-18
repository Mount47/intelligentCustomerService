"""Chat 接入与会话视图（前端契约对齐，§14/§10/ADR-10）。

accept_message：消息幂等去重 → 建/取 ticket → 落用户消息 → 建 agent_session(queued)。
build_chat_session：组装前端 ChatSession（messages + steps 时间线 + toolCalls + tokenUsage）。
"""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.exceptions import SupportFlowError
from app.db.models import AgentSession, AgentToolCall, Ticket, TicketMessage
from app.schemas.chat import (
    AgentTimelineStep,
    ChatMessageIn,
    ChatMessageView,
    ChatSession,
    ToolCallView,
    TokenUsage,
)
from app.schemas.common import to_frontend_task_status
from app.services import ticket_service

# 工具名 → 中文标签（思考过程时间线展示用）
TOOL_LABELS = {
    "get_order_detail": "查询订单", "get_user_orders": "查询用户订单",
    "check_order_owner": "校验订单归属", "get_logistics_status": "查询物流",
    "check_logistics_exception": "判断物流异常", "check_refund_policy": "核对退款政策",
    "create_refund_draft": "创建退款草稿", "get_refund_status": "查询退款状态",
    "create_ticket": "创建工单", "update_ticket_status": "更新工单状态",
    "handoff_to_human": "转人工", "add_ticket_message": "追加消息",
    "search_policy_docs": "检索政策", "get_policy_by_category": "取政策",
}


def accept_message(db: Session, payload: ChatMessageIn) -> tuple[AgentSession, bool]:
    # 1 消息幂等去重（§9.1）
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


def _build_steps(sess: AgentSession, tool_calls: list[AgentToolCall]) -> list[AgentTimelineStep]:
    steps: list[AgentTimelineStep] = []
    if sess.current_intent:
        steps.append(AgentTimelineStep(id="intent", kind="intent", title="识别意图",
                                       detail=sess.current_intent))
    if sess.current_skill:
        steps.append(AgentTimelineStep(id="skill", kind="skill", title="选择技能",
                                       detail=sess.current_skill))
    for tc in tool_calls:
        steps.append(AgentTimelineStep(
            id=f"tool-{tc.id}", kind="tool", title=TOOL_LABELS.get(tc.tool_name, tc.tool_name),
            detail=tc.error_message, status="success" if tc.success else "failed",
            latency_ms=tc.latency_ms, created_at=tc.created_at))
    if sess.final_status:
        steps.append(AgentTimelineStep(id="state", kind="state", title="处理结果",
                                       detail=sess.final_status))
    return steps


def build_chat_session(db: Session, sess: AgentSession) -> ChatSession:
    msgs = []
    if sess.ticket_id is not None:
        msgs = list(db.scalars(select(TicketMessage).where(
            TicketMessage.ticket_id == sess.ticket_id).order_by(TicketMessage.id)).all())
    tool_calls = list(db.scalars(select(AgentToolCall).where(
        AgentToolCall.session_id == sess.id).order_by(AgentToolCall.id)).all())
    latest_reply = next((m.content for m in reversed(msgs) if m.sender_type == "agent"), None)
    latency = None
    if sess.started_at and sess.finished_at:
        latency = int((sess.finished_at - sess.started_at).total_seconds() * 1000)

    return ChatSession(
        id=sess.id, ticket_id=sess.ticket_id,
        task_status=to_frontend_task_status(sess.task_status),
        current_intent=sess.current_intent, current_skill=sess.current_skill,
        current_state=sess.current_state, final_status=sess.final_status,
        latest_reply=latest_reply,
        messages=[ChatMessageView(id=m.id, sender=m.sender_type, content=m.content,
                                  created_at=m.created_at) for m in msgs],
        steps=_build_steps(sess, tool_calls),
        tool_calls=[ToolCallView(
            id=tc.id, session_id=tc.session_id, tool_name=tc.tool_name,
            input_json=tc.input_json, output_json=tc.output_json, success=tc.success,
            latency_ms=tc.latency_ms, error_message=tc.error_message,
            created_at=tc.created_at) for tc in tool_calls],
        token_usage=TokenUsage(
            model_name=sess.model_name, prompt_tokens=sess.prompt_tokens,
            completion_tokens=sess.completion_tokens, total_tokens=sess.total_tokens,
            estimated_cost=float(sess.estimated_cost or 0), cache_hit=sess.cache_hit),
        total_latency_ms=latency,
    )


def get_session_view(db: Session, session_id: int) -> ChatSession | None:
    sess = db.get(AgentSession, session_id)
    return build_chat_session(db, sess) if sess else None
