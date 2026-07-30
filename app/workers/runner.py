"""Agent 会话处理（纯函数，不依赖 celery，便于测试与复用）。

取最新用户消息 → 跑 AgentCore → 写回 agent 回复 + 更新 session(state/token/task_status)。
失败自行标记 failed，不抛到调用方之外。celery 任务（agent_tasks）只是它的包装。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from app.agent.context import AgentContext
from app.agent.memory import apply_token_budget
from app.agent.state_machine import States
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AgentSession, Ticket, TicketMessage
from app.llm.base import Msg
from app.services import sla_service, ticket_service
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


_CLAIMABLE_TASK_STATUSES = ("queued", "failed", "timeout")


def _claim_session(db: Session, session_id: int) -> AgentSession | None:
    """原子抢占一次 Session。

    Celery 使用 late ack，worker 可能在业务事务提交后、ACK 前退出，随后 broker 会重投。
    只有 queued/failed/timeout 能进入 processing；completed/need_human/waiting 等状态的
    重投直接成为幂等 no-op，避免重复回复和重复业务副作用。
    """
    now = datetime.utcnow()
    result = db.execute(
        update(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.task_status.in_(_CLAIMABLE_TASK_STATUSES),
        ).values(
            task_status="processing",
            started_at=now,
            finished_at=None,
            error_message=None,
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(AgentSession, session_id)


def _source_message(db: Session, sess: AgentSession) -> TicketMessage | None:
    """读取精确触发本 Session 的消息；仅为迁移前历史 Session 保留兼容回退。"""
    if sess.source_message_id is not None:
        msg = db.get(TicketMessage, sess.source_message_id)
        if msg is None:
            raise RuntimeError(f"source message {sess.source_message_id} not found")
        if msg.ticket_id != sess.ticket_id or msg.user_id != sess.user_id \
                or msg.sender_type != "user":
            raise RuntimeError("source message does not match agent session identity")
        return msg
    logger.warning(
        "agent session %s has no source_message_id; using legacy latest-message fallback",
        sess.id,
    )
    return db.scalar(select(TicketMessage).where(
        TicketMessage.ticket_id == sess.ticket_id,
        TicketMessage.sender_type == "user").order_by(desc(TicketMessage.id)))


def run_agent_session(db: Session, session_id: int, agent=None,
                      raise_on_error: bool = False, trace_id: str | None = None) -> None:
    """处理一个会话。raise_on_error=True 时（celery 路径）失败会重抛以便重试；
    默认 False（线程/测试路径）吞掉异常并标记 failed/timeout。"""
    existing = db.get(AgentSession, session_id)
    if existing is None:
        logger.warning("run_agent_session: session %s not found", session_id)
        return

    from app.observability.tracing import new_trace_id, set_trace_id
    if trace_id:
        set_trace_id(trace_id)  # API → broker → worker 继承同一 trace
    else:
        new_trace_id()          # 直接调用/旧任务兼容：worker 自建 trace

    sess = _claim_session(db, session_id)
    if sess is None:
        logger.info(
            "run_agent_session: session %s already claimed or terminal; duplicate delivery ignored",
            session_id,
        )
        return

    try:
        msg = _source_message(db, sess)
        ticket = db.get(Ticket, sess.ticket_id)
        if msg is None or ticket is None:
            raise RuntimeError("missing user message or ticket")
        expected_ticket_state = ticket.status
        expected_ticket_version = ticket.version

        if agent is None:  # 生产路径：按 .env 选 provider
            from app.agent.agent_core import build_default_agent
            from app.llm.registry import build_llm_client
            agent = build_default_agent(build_llm_client())

        # 跨轮状态：确认协议与待补槽位都需恢复；其他状态的新消息视为新请求。
        resumable = {
            States.WAITING_USER_CONFIRM,
            States.INFO_REQUIRED,
        }
        start_state = ticket.status if ticket.status in resumable else None
        ctx = AgentContext(session_id=sess.id, ticket_id=sess.ticket_id,
                           user_id=sess.user_id, message=msg.content,
                           order_id=ticket.order_id, state=start_state,
                           pending_context=ticket.pending_context,
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
        # 最终状态用 DB CAS 持久化；若其他 worker 已推进同一工单，当前事务整体回滚。
        ticket_service.persist_agent_state(
            db, ticket.id,
            expected_state=expected_ticket_state,
            expected_version=expected_ticket_version,
            target_state=ctx.state,
            pending_context=ctx.pending_context,
            actor_type="agent",
            actor_id=acct.model_name,
            reason=decision.handoff_reason,
            session_id=sess.id,
        )
        if ctx.state == States.RESOLVED_BY_AGENT:   # 解决 → 回填 SLA(闭环)；转人工/等待态留给后续
            sla_service.mark_resolved(db, sess.ticket_id)
    except Exception as exc:  # noqa: BLE001 — 异步任务不崩
        logger.exception("agent session %s failed", session_id)
        # Agent/Skill/工具的写都在本事务内；失败或 CAS 冲突必须先回滚，禁止半成品副作用落库。
        db.rollback()
        sess = db.get(AgentSession, session_id)
        if sess is None:
            raise RuntimeError(f"agent session {session_id} disappeared after rollback") from exc
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
