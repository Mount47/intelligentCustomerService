"""M9 加固：worker 失败/超时标记与重抛行为。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.context import Decision
from app.agent.state_machine import States
from app.db.models import AgentSession, Base, Ticket, TicketMessage, User
from app.schemas.chat import ChatMessageIn
from app.services import chat_service, ticket_service
from app.workers.runner import run_agent_session


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
