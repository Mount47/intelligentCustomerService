"""M8：观测指标 + 接入路径烟雾测（不依赖 docker；真实并发数字靠本地 docker+locust）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.db.models import Base, User
from app.db.session import get_db
from app.main import app
from app.observability.metrics import compute_metrics
from app.schemas.chat import ChatMessageIn
from app.services import chat_service
from app.workers.runner import run_agent_session

from .conftest import make_order


def test_metrics_compute(db, user_order):
    u, o = user_order
    big = make_order(db, u.id, amount=999)
    agent = build_default_agent()
    for content, oid in [("我要退款", o.id), ("太贵了退款", big.id), ("我要退款", None)]:
        sess, _ = chat_service.accept_message(
            db, ChatMessageIn(user_id=u.id, content=content, order_id=oid))
        run_agent_session(db, sess.id, agent=agent)

    m = compute_metrics(db)
    assert m["totals"]["sessions"] == 3
    assert m["sessions_by_task_status"].get("completed", 0) >= 1     # 低风险退款
    assert m["sessions_by_task_status"].get("need_human", 0) >= 1    # 高风险
    assert m["sessions_by_task_status"].get("waiting_user_input", 0) >= 1  # 缺单号
    assert 0.0 <= m["agent"]["resolution_rate"] <= 1.0
    assert m["queue_depth"] is None or isinstance(m["queue_depth"], int)
    assert "avg_tokens_per_session" in m["cost"]


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


def test_admin_metrics_endpoint(client):
    for _ in range(3):
        client.post("/api/chat/message", json={"userId": 1, "content": "你好"})
    r = client.get("/api/admin/metrics")
    assert r.status_code == 200
    body = r.json()
    # 前端 AdminMetrics 契约（camelCase）+ 削峰超集
    assert {"ticketCount", "resolvedRate", "handoffRate", "activeSessions",
            "queueDepth", "sessionsByTaskStatus"} <= set(body)
    assert body["activeSessions"] >= 3
    assert body["sessionsByTaskStatus"].get("queued", 0) >= 3   # 入队被 stub，停在 queued


def test_ingest_path_many(client):
    n = 30
    for i in range(n):
        r = client.post("/api/chat/message",
                        json={"userId": 1, "content": "我要退款", "clientMessageId": f"m-{i}"})
        assert r.status_code == 200 and r.json()["taskStatus"] == "queued"
    assert len(client.dispatched) == n                              # 全部入队
    assert client.get("/api/admin/metrics").json()["sessionsByTaskStatus"].get("queued", 0) == n


def test_admin_tickets_and_sessions(client):
    for _ in range(2):
        client.post("/api/chat/message", json={"userId": 1, "content": "我要退款"})
    tickets = client.get("/api/admin/tickets").json()
    sessions = client.get("/api/admin/sessions").json()
    assert len(tickets) >= 2 and len(sessions) >= 2
    assert {"id", "category", "status", "currentState"} <= set(tickets[0])
    assert {"id", "taskStatus", "totalTokens"} <= set(sessions[0])
    # 工单详情
    tid = tickets[0]["id"]
    detail = client.get(f"/api/admin/tickets/{tid}").json()
    assert "messages" in detail and "stateTimeline" in detail
    assert client.get("/api/admin/tickets/999999").status_code == 404
