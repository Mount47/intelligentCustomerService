"""M9 加固：worker 失败/超时标记与重抛行为。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.agent_core import build_default_agent
from app.agent.context import Decision
from app.agent.state_machine import States
from app.db.models import AgentSession, Base, RefundRequest, Ticket, TicketMessage, User
from app.schemas.chat import ChatMessageIn
from app.services import chat_service, ticket_service
from app.workers.runner import run_agent_session
from app.observability.tracing import get_trace_id

from .conftest import make_order


class _RaisingAgent:
    def __init__(self, exc):
        self.exc = exc

    def handle(self, ctx, tool_ctx=None):
        raise self.exc


class SoftTimeLimitExceeded(Exception):
    """模拟 celery 软超时（按类名匹配）。"""


class _ConflictingWriteAgent:
    """先让另一事务推进工单，再在当前事务制造一个应随 CAS 冲突回滚的副作用。"""

    def __init__(self, Session, ticket_id):
        self.Session = Session
        self.ticket_id = ticket_id

    def handle(self, ctx, tool_ctx=None):
        with self.Session() as other:
            ticket_service.update_status(other, self.ticket_id, States.NEED_HUMAN)
            other.commit()
        tool_ctx.db.add(Ticket(user_id=ctx.user_id, category="should_rollback"))
        tool_ctx.db.flush()
        ctx.intent = "general_policy_query"
        ctx.skill = "general"
        ctx.state = States.RESOLVED_BY_AGENT
        return Decision("这条回复也必须回滚", States.RESOLVED_BY_AGENT)


class _StaleHighRiskRefundAgent:
    """先制造 stale Ticket，再执行真实高风险退款 Skill，验证退款写也受外层事务保护。"""

    def __init__(self, Session, ticket_id):
        self.Session = Session
        self.ticket_id = ticket_id
        self.inner = build_default_agent()

    def handle(self, ctx, tool_ctx=None):
        with self.Session() as other:
            ticket_service.update_status(other, self.ticket_id, States.NEED_HUMAN)
            other.commit()
        return self.inner.handle(ctx, tool_ctx)


class _TraceCapturingAgent:
    def __init__(self):
        self.trace_id = None

    def handle(self, ctx, tool_ctx=None):
        self.trace_id = get_trace_id()
        ctx.intent = "general_policy_query"
        ctx.skill = "general"
        ctx.state = States.RESOLVED_BY_AGENT
        return Decision("trace captured", States.RESOLVED_BY_AGENT)


class _MessageCapturingAgent:
    def __init__(self):
        self.messages = []

    def handle(self, ctx, tool_ctx=None):
        self.messages.append(ctx.message)
        ctx.intent = "general_policy_query"
        ctx.skill = "general"
        ctx.state = States.RESOLVED_BY_AGENT
        return Decision(f"processed:{ctx.message}", States.RESOLVED_BY_AGENT)


def _session(db, user_order):
    u, o = user_order
    sess, _ = chat_service.accept_message(
        db, ChatMessageIn(user_id=u.id, content="我要退款", order_id=o.id))
    return sess


def test_failure_marks_failed_and_counts_retry(db, user_order):
    sess = _session(db, user_order)
    run_agent_session(db, sess.id, agent=_RaisingAgent(ValueError("boom")))  # 默认不重抛
    db.refresh(sess)
    assert sess.task_status == "failed"
    assert sess.retry_count == 1
    assert "boom" in (sess.error_message or "")


def test_timeout_marked_distinctly(db, user_order):
    sess = _session(db, user_order)
    run_agent_session(db, sess.id, agent=_RaisingAgent(SoftTimeLimitExceeded()))
    db.refresh(sess)
    assert sess.task_status == "timeout"
    assert sess.retry_count == 1


def test_raise_on_error_propagates_and_persists(db, user_order):
    sess = _session(db, user_order)
    with pytest.raises(ValueError):
        run_agent_session(db, sess.id, agent=_RaisingAgent(ValueError("x")), raise_on_error=True)
    db.refresh(sess)
    assert sess.task_status == "failed"   # 重抛前已落库


def test_runner_preserves_api_trace_id(db, user_order):
    sess = _session(db, user_order)
    agent = _TraceCapturingAgent()
    run_agent_session(db, sess.id, agent=agent, trace_id="api-to-worker-123")
    assert agent.trace_id == "api-to-worker-123"


def test_runner_uses_session_source_message_not_latest_ticket_message(db, user_order):
    user, order = user_order
    first, _ = chat_service.accept_message(
        db, ChatMessageIn(
            user_id=user.id, content="第一轮消息", order_id=order.id,
            client_message_id="worker-source-first"))
    second, _ = chat_service.accept_message(
        db, ChatMessageIn(
            user_id=user.id, ticket_id=first.ticket_id, content="第二轮消息",
            client_message_id="worker-source-second"))
    agent = _MessageCapturingAgent()

    # 第二轮已入库后才处理第一轮；旧实现会错误读取“第二轮消息”。
    run_agent_session(db, first.id, agent=agent)

    assert agent.messages == ["第一轮消息"]
    db.refresh(first)
    db.refresh(second)
    assert first.task_status == "completed"
    assert second.task_status == "queued"


def test_terminal_session_redelivery_is_idempotent_noop(db, user_order):
    sess = _session(db, user_order)
    agent = _MessageCapturingAgent()
    run_agent_session(db, sess.id, agent=agent)
    replies_before = db.query(TicketMessage).filter_by(
        ticket_id=sess.ticket_id, sender_type="agent").count()

    run_agent_session(db, sess.id, agent=agent)

    replies_after = db.query(TicketMessage).filter_by(
        ticket_id=sess.ticket_id, sender_type="agent").count()
    assert agent.messages == ["我要退款"]
    assert replies_after == replies_before == 1


def test_runner_cas_conflict_rolls_back_all_side_effects(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runner-cas.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        user = User(username="runner-cas")
        db.add(user)
        db.flush()
        ticket = Ticket(user_id=user.id, category="chat")
        db.add(ticket)
        db.flush()
        db.add(TicketMessage(ticket_id=ticket.id, user_id=user.id,
                             sender_type="user", content="测试冲突"))
        sess = AgentSession(user_id=user.id, ticket_id=ticket.id, task_status="queued")
        db.add(sess)
        db.commit()
        ticket_id, session_id = ticket.id, sess.id

        run_agent_session(
            db, session_id,
            agent=_ConflictingWriteAgent(Session, ticket_id),
        )
        db.expire_all()

        current_session = db.get(AgentSession, session_id)
        current_ticket = db.get(Ticket, ticket_id)
        assert current_session.task_status == "failed"
        assert current_session.retry_count == 1
        assert "concurrently" in (current_session.error_message or "")
        assert current_ticket.status == States.NEED_HUMAN
        assert current_ticket.version == 1
        assert db.query(Ticket).filter_by(category="should_rollback").count() == 0
        assert db.query(TicketMessage).filter_by(
            ticket_id=ticket_id, sender_type="agent").count() == 0


def test_runner_cas_conflict_rolls_back_refund_write(tmp_path):
    """核心副作用不能被 service 内部 commit 提前落库。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'runner-refund-cas.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        user = User(username="runner-refund-cas")
        db.add(user)
        db.flush()
        order = make_order(db, user.id, amount=999)
        ticket = ticket_service.create_ticket(
            db, user.id, category="chat", order_id=order.id)
        ticket_service.add_message(
            db, ticket.id, "user", "高金额订单我要退款", user_id=user.id)
        sess = AgentSession(user_id=user.id, ticket_id=ticket.id, task_status="queued")
        db.add(sess)
        db.commit()
        ticket_id, session_id, order_id = ticket.id, sess.id, order.id

        run_agent_session(
            db, session_id,
            agent=_StaleHighRiskRefundAgent(Session, ticket_id),
        )
        db.expire_all()

        current_session = db.get(AgentSession, session_id)
        assert current_session.task_status == "failed"
        assert "concurrently" in (current_session.error_message or "")
        assert db.query(RefundRequest).filter_by(order_id=order_id).count() == 0
        assert db.query(Ticket).filter_by(category="refund").count() == 0
        assert db.query(TicketMessage).filter_by(
            ticket_id=ticket_id, sender_type="agent").count() == 0
