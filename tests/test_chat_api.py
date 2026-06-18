"""M6 异步 API 闭环：POST 入队立即返回 + 消息幂等去重 + GET 轮询 + worker 真处理。

API 测试用 TestClient + 依赖覆盖 sqlite + monkeypatch 入队（不连真实 broker）。
worker 处理用 stub agent 同步跑 run_agent_session。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.db.models import Base, TicketMessage, User
from app.db.session import get_db
from app.main import app
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session

from .conftest import make_order


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    dispatched: list[int] = []
    monkeypatch.setattr("app.api.chat._dispatch", lambda sid: dispatched.append(sid))
    with TestSession() as s:
        s.add(User(username="u1"))
        s.commit()
    c = TestClient(app, raise_server_exceptions=False)
    c.dispatched = dispatched
    try:
        yield c
    finally:
        app.dependency_overrides.clear()


def test_post_message_enqueues_and_returns(client):
    r = client.post("/api/chat/message", json={"user_id": 1, "content": "你好"})
    assert r.status_code == 200
    body = r.json()
    assert body["taskStatus"] == "queued" and body["dedup"] is False
    assert body["sessionId"] and body["ticketId"]
    assert client.dispatched == [body["sessionId"]]    # 入队一次


def test_message_idempotency_no_double_enqueue(client):
    payload = {"user_id": 1, "content": "我要退款", "client_message_id": "m-1"}
    r1 = client.post("/api/chat/message", json=payload).json()
    r2 = client.post("/api/chat/message", json=payload).json()
    assert r2["dedup"] is True
    assert r1["sessionId"] == r2["sessionId"]
    assert client.dispatched == [r1["sessionId"]]      # 只入队一次


def test_get_session_initial_state(client):
    sid = client.post("/api/chat/message", json={"user_id": 1, "content": "你好"}).json()["sessionId"]
    r = client.get(f"/api/chat/session/{sid}")
    assert r.status_code == 200
    assert r.json()["taskStatus"] == "queued"    # 未处理（入队被 stub）
    assert client.get("/api/chat/session/99999").status_code == 404


# ---------- worker 真处理（用 stub agent 同步跑） ----------
def test_run_agent_session_end_to_end(db, user_order):
    u, o = user_order
    sess, dedup = chat_service.accept_message(
        db, ChatMessageIn(user_id=u.id, content="我要退款", order_id=o.id))
    assert dedup is False and sess.task_status == "queued"

    run_agent_session(db, sess.id, agent=build_default_agent())  # stub LLM

    db.refresh(sess)
    assert sess.task_status == "completed"
    assert sess.current_intent == "refund_request"
    assert sess.current_skill == "refund_handling"
    assert sess.final_status == "resolved_by_agent"
    # agent 回复已写回
    agent_msg = db.query(TicketMessage).filter_by(ticket_id=sess.ticket_id, sender_type="agent").one()
    assert "退款" in agent_msg.content
    # 会话视图 + steps 时间线
    view = chat_service.get_session_view(db, sess.id)
    kinds = [s.kind for s in view.steps]
    assert "intent" in kinds and "skill" in kinds and "state" in kinds
    assert view.latest_reply == agent_msg.content


def test_run_agent_session_high_risk_need_human(db, user_order):
    u, _ = user_order
    big = make_order(db, u.id, amount=999)
    sess, _ = chat_service.accept_message(
        db, ChatMessageIn(user_id=u.id, content="太贵了要退款", order_id=big.id))
    run_agent_session(db, sess.id, agent=build_default_agent())
    db.refresh(sess)
    assert sess.task_status == "need_human"
    assert sess.final_status == "need_human"
