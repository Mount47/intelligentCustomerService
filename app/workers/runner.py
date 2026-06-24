"""Agent 会话处理（纯函数，不依赖 celery，便于测试与复用）。

取最新用户消息 → 跑 AgentCore → 写回 agent 回复 + 更新 session(state/token/task_status)。
失败自行标记 failed，不抛到调用方之外。celery 任务（agent_tasks）只是它的包装。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agent.context import AgentContext
from app.agent.memory import apply_token_budget
from app.agent.state_machine import States
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AgentSession, Ticket, TicketMessage
from app.llm.base import Msg
from app.services import ticket_service
from app.tools.base import ToolContext

logger = get_logger(__name__)

# 外层行上限：一次最多读这么多条进内存，再在其上按 token 预算切（P4 滚动摘要）。
_HISTORY_MAX_ROWS = 100
# 工单消息发送方 → LLM 对话角色：人工与 agent 同属 assistant 侧
_ROLE_MAP = {"user": "user", "agent": "assistant", "human": "assistant", "system": "system"}


def _load_history(db: Session, ticket_id: int, before_id: int,
                  *, token_budget: int | None = None, llm=None) -> list[Msg]:
    """加载本工单当前消息之前的历史，转 LLM 视角，并按 token 预算滚动摘要（P1+P4）。

    最近若干条原样保留；超 token 预算的更早一段摘成一条前置消息（防溢出/防忘最初诉求）。
    硬事实（order_id/金额）不靠这里——在 ticket/DB 查库（ADR-21）。
    """
    if token_budget is None:
        token_budget = get_settings().history_token_budget
    rows = list(db.scalars(select(TicketMessage).where(
        TicketMessage.ticket_id == ticket_id,
        TicketMessage.id < before_id).order_by(TicketMessage.id)).all())
    rows = rows[-_HISTORY_MAX_ROWS:]
    msgs = [Msg(role=_ROLE_MAP.get(m.sender_type, "user"), content=m.content) for m in rows]
    return apply_token_budget(msgs, token_budget=token_budget, llm=llm)


def task_status_for(state: str) -> str:
    if state in (States.INFO_REQUIRED, States.WAITING_USER_CONFIRM):
        return "waiting_user_input"   # 等待用户补信息/确认
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

        # 跨轮状态：仅"等待确认"态需续接（其余 new message 视为新请求，从 CREATED 起）
        start_state = (States.WAITING_USER_CONFIRM
                       if ticket.status == States.WAITING_USER_CONFIRM else None)
        ctx = AgentContext(session_id=sess.id, ticket_id=sess.ticket_id,
                           user_id=sess.user_id, message=msg.content,
                           order_id=ticket.order_id, state=start_state,
                           history=_load_history(db, sess.ticket_id, msg.id,
                                                 llm=getattr(agent, "llm", None)))
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
