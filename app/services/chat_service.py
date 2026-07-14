"""Chat 接入与会话视图（前端契约对齐，§14/§10/ADR-10）。

accept_message：消息幂等去重 → 建/取 ticket → 落用户消息 → 建 agent_session(queued)。
build_chat_session：组装前端 ChatSession（messages + steps 时间线 + toolCalls + tokenUsage）。
"""
from __future__ import annotations

import json
import re
import time

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.exceptions import ResourceAccessDenied, SupportFlowError
from app.db.models import AgentSession, AgentToolCall, Order, Ticket, TicketMessage
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


def resolve_order_from_text(db: Session, user_id: int, content: str) -> int | None:
    """从消息文本里提取订单号并解析为该用户的订单 id（校验归属）。

    支持 order_no（如 DEMO-REFUND-LOW / SO20260618xxxx）或纯数字 order_id。
    让用户自然地说"我要退款 订单 XXX"即可单轮跑通，无需前端单独传 orderId。
    """
    tokens = set(re.findall(r"[A-Za-z0-9\-]{3,}", content or ""))
    if not tokens:
        return None
    o = db.scalar(select(Order).where(
        Order.user_id == user_id, Order.order_no.in_(tokens)))
    if o:
        return o.id
    for t in tokens:                       # 纯数字按 order_id 尝试（仍校验归属）
        if t.isdigit():
            od = db.get(Order, int(t))
            if od and od.user_id == user_id:
                return od.id
    return None


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

    # 2 建/取 ticket（order_id 优先用入参，否则从消息文本解析）
    order_id = payload.order_id
    if order_id is not None:
        order = db.get(Order, order_id)
        if order is None or order.user_id != payload.user_id:
            raise ResourceAccessDenied("order does not belong to user")
    if order_id is None:
        order_id = resolve_order_from_text(db, payload.user_id, payload.content)
    if payload.ticket_id is not None:
        ticket = db.get(Ticket, payload.ticket_id)
        if ticket is None:
            raise SupportFlowError("ticket not found")
        if ticket.user_id != payload.user_id:
            raise ResourceAccessDenied("ticket does not belong to user")
        if order_id is not None and ticket.order_id not in (None, order_id):
            raise ResourceAccessDenied("ticket is already bound to another order")
        if ticket.order_id is None and order_id is not None:
            ticket = ticket_service.bind_order(db, ticket.id, order_id)
    else:
        ticket = ticket_service.create_ticket(
            db, payload.user_id, category="chat", order_id=order_id)

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


# 处理中的态（流式继续推送）；其余视为终态，推完即收尾
_NON_TERMINAL = {"queued", "processing"}


def stream_session_events(fetch_view, *, interval: float = 1.0, max_iters: int = 120,
                          sleep=time.sleep):
    """SSE 事件流：轮询会话视图，状态/回复/步骤一有变化就推一条 `data:`，终态后推 `done` 收尾。

    把客户端轮询换成服务端推送（流式 UX），不改异步处理本身——进度仍由 worker 写库，
    这里只负责"有变化就推、到终态就停"。fetch_view 每次须返回最新视图（见 stream endpoint：
    每轮 expire 缓存重读，才看得到 worker 的提交）。max_iters 兜底防无限流。
    """
    last_sig = object()
    for _ in range(max_iters):
        view = fetch_view()
        if view is None:
            yield 'event: error\ndata: {"detail":"session not found"}\n\n'
            return
        d = view.model_dump(by_alias=True)
        sig = (d.get("taskStatus"), d.get("latestReply"), len(d.get("steps") or []))
        if sig != last_sig:                       # 仅在有变化时推，省带宽、前端好处理
            yield f"data: {json.dumps(d, ensure_ascii=False, default=str)}\n\n"
            last_sig = sig
        if d.get("taskStatus") not in _NON_TERMINAL:
            yield "event: done\ndata: {}\n\n"
            return
        sleep(interval)
    yield "event: done\ndata: {}\n\n"             # 达上限也收尾
