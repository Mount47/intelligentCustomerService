"""M8：观测指标 + 接入路径烟雾测（不依赖 docker；真实并发数字靠本地 docker+locust）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent_core import build_default_agent
from app.core.security import issue_access_token
from app.db.models import Base, User
from app.db.session import get_db
from app.main import app
from app.observability.metrics import _queue_depth, compute_metrics
from app.schemas.chat import ChatMessageIn
from app.schemas.common import to_frontend_task_status
from app.services import chat_service
from app.workers.runner import run_agent_session

from .conftest import make_order


def test_waiting_user_input_is_not_reported_as_resolved():
    assert to_frontend_task_status("waiting_user_input") == "waiting_user_input"
    assert to_frontend_task_status("completed") == "final"


def test_queue_depth_reads_celery_broker_client():
    class FakeBroker:
        def llen(self, queue_name):
            assert queue_name == "celery"
            return 37

    assert _queue_depth(FakeBroker()) == 37


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
    # 低风险退款→待确认、缺单号→索取（均 waiting_user_input）；高风险→need_human
    assert m["sessions_by_task_status"].get("waiting_user_input", 0) >= 2
    assert m["sessions_by_task_status"].get("need_human", 0) >= 1    # 高风险
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
    monkeypatch.setattr(
        "app.api.chat._dispatch", lambda sid, trace_id: dispatched.append(sid))
    with TestSession() as s:
        user = User(username="u1", role="admin")
        s.add(user)
        s.commit()
        token = issue_access_token(user.id)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {token}"
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


def test_admin_human_ticket_resolution_closes_sla_and_persists_timeline(client):
    created = client.post(
        "/api/chat/message",
        json={"userId": 1, "content": "我要转人工", "clientMessageId": "admin-human-flow"},
    ).json()
    # 接入测试不运行 worker，先把 created 工单指派到人工队列。
    assigned = client.patch(
        f"/api/admin/tickets/{created['ticketId']}",
        json={"action": "assign", "assignedTo": "agent-007", "note": "开始处理"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["status"] == "need_human"
    assert assigned.json()["assignedTo"] == "agent-007"

    resolved = client.patch(
        f"/api/admin/tickets/{created['ticketId']}",
        json={"action": "resolve", "note": "已电话联系并解决"},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "resolved_by_human"
    assert len(body["stateTimeline"]) >= 3
    assert any("已电话联系并解决" in m["content"] for m in body["messages"])


def test_admin_lists_support_bounded_pagination_and_filters(client):
    for i in range(4):
        client.post(
            "/api/chat/message",
            json={"userId": 1, "content": "你好", "clientMessageId": f"page-{i}"},
        )
    first_page = client.get("/api/admin/tickets?limit=2&offset=0").json()
    second_page = client.get("/api/admin/tickets?limit=2&offset=2").json()
    assert len(first_page) == len(second_page) == 2
    assert {row["id"] for row in first_page}.isdisjoint(
        {row["id"] for row in second_page})
    queued = client.get("/api/admin/sessions?task_status=queued&limit=2").json()
    assert len(queued) == 2
    assert all(row["taskStatus"] == "queued" for row in queued)
    assert client.get("/api/admin/tickets?limit=500").status_code == 422
