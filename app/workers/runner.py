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
from app.llm.base import Msg
from app.services import ticket_service
from app.tools.base import ToolContext

logger = get_logger(__name__)

# 对话记忆窗口（P1）：只取当前消息之前的最近 N 条历史，控 token；
# 更长历史的摘要/向量检索留待 P4/P5（见 DECISIONS 记忆分层）。
_HISTORY_MAX_MSGS = 20
# 工单消息发送方 → LLM 对话角色：人工与 agent 同属 assistant 侧
_ROLE_MAP = {"user": "user", "agent": "assistant", "human": "assistant", "system": "system"}


def _load_history(db: Session, ticket_id: int, before_id: int) -> list[Msg]:
    """加载本工单在当前消息之前的对话历史，转成 LLM 视角（P1 短期对话记忆）。"""
    rows = list(db.scalars(select(TicketMessage).where(
        TicketMessage.ticket_id == ticket_id,
        TicketMessage.id < before_id).order_by(TicketMessage.id)).all())
    rows = rows[-_HISTORY_MAX_MSGS:]
    return [Msg(role=_ROLE_MAP.get(m.sender_type, "user"), content=m.content) for m in rows]


def task_status_for(state: str) -> str:
    if state == States.INFO_REQUIRED:
        return "waiting_user_input"
    if state == States.NEED_HUMAN:
        return "need_human"
    return "completed"


def run_agent_session(db: Session, session_id: int, agent=None,
                      raise_on_error: bool = False) -> None:
    """处理一个会话。raise_on_error=True 时（celery 路径）失败会重抛以便重试；
    默认 False（线程/测试路径）吞掉异常并标记 failed/timeout。"""
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
                           order_id=ticket.order_id,
                           history=_load_history(db, sess.ticket_id, msg.id))
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
    except Exception as exc:  # noqa: BLE001 — 异步任务不崩
        logger.exception("agent session %s failed", session_id)
        # 超时单独标记（不导入 celery，按类名判断 SoftTimeLimitExceeded）
        is_timeout = type(exc).__name__ == "SoftTimeLimitExceeded"
        sess.task_status = "timeout" if is_timeout else "failed"
        sess.error_message = ("处理超时" if is_timeout else str(exc))[:500]
        sess.retry_count = (sess.retry_count or 0) + 1
        if raise_on_error:
            raise            # 交给 celery 重试（finally 仍会落库当前态）
    finally:
        sess.finished_at = datetime.utcnow()
        db.commit()
