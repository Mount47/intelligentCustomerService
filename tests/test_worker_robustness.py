"""M9 加固：worker 失败/超时标记与重抛行为。"""
import pytest

from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session


class _RaisingAgent:
    def __init__(self, exc):
        self.exc = exc

    def handle(self, ctx, tool_ctx=None):
        raise self.exc


class SoftTimeLimitExceeded(Exception):
    """模拟 celery 软超时（按类名匹配）。"""


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
