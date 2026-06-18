"""Agent 会话处理（纯函数，不依赖 celery，便于测试与复用）。

取最新用户消息 → 跑 AgentCore → 写回 agent 回复 + 更新 session(state/token/task_status)。
失败自行标记 failed，不抛到调用方之外。celery 任务（agent_tasks）只是它的包装。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.context import AgentContext
from app.agent.state_machine import States
from app.core.logging import get_logger
from app.db.models import AgentSession, Ticket, TicketMessage
from app.services import ticket_service
from app.tools.base import ToolContext

logger = get_logger(__name__)


def task_status_for(state: str) -> str:
    if state == States.INFO_REQUIRED:
        return "waiting_user_input"
    if state == States.NEED_HUMAN:
        return "need_human"
    return "completed"


def run_agent_session(db: Session, session_id: int, agent=None) -> None:
    sess = db.get(AgentSession, session_id)
    if sess is None:
        logger.warning("run_agent_session: session %s not found", session_id)
        return

    from app.observability.tracing import new_trace_id
    new_trace_id()   # 本次会话全链路 trace_id

    sess.task_status = "processing"
    sess.started_at = datetime.utcnow()
    db.commit()

    try:
        msg = db.scalar(select(TicketMessage).where(
            TicketMessage.ticket_id == sess.ticket_id,
            TicketMessage.sender_type == "user").order_by(desc(TicketMessage.id)))
        ticket = db.get(Ticket, sess.ticket_id)
        if msg is None or ticket is None:
            raise RuntimeError("missing user message or ticket")

        if agent is None:  # 生产路径：按 .env 选 provider
            from app.agent.agent_core import build_default_agent
            from app.llm.registry import build_llm_client
            agent = build_default_agent(build_llm_client())

        ctx = AgentContext(session_id=sess.id, ticket_id=sess.ticket_id,
                           user_id=sess.user_id, message=msg.content,
                           order_id=ticket.order_id)
        decision = agent.handle(ctx, tool_ctx=ToolContext(db=db, session_id=sess.id))

        ticket_service.add_message(db, sess.ticket_id, "agent", decision.reply)
        acct = ctx.token_acct
        sess.current_intent = ctx.intent
        sess.current_skill = ctx.skill
        sess.current_state = ctx.state
        sess.final_status = decision.next_state
        sess.model_name = acct.model_name
        sess.prompt_tokens = acct.prompt_tokens
        sess.completion_tokens = acct.completion_tokens
        sess.total_tokens = acct.total_tokens
        sess.estimated_cost = acct.estimated_cost
        sess.cache_hit = acct.cache_hit
        sess.task_status = task_status_for(ctx.state)
        ticket.status = ctx.state  # 工单状态由 agent 最终态驱动
    except Exception as exc:  # noqa: BLE001 — 异步任务不崩，标记 failed
        logger.exception("agent session %s failed", session_id)
        sess.task_status = "failed"
        sess.error_message = str(exc)[:500]
    finally:
        sess.finished_at = datetime.utcnow()
        db.commit()
