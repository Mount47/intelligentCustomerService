"""SSE 流式推送：生成器逻辑(变化才推/终态收尾/兜底) + 端点 event-stream 接入。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import AgentSession, Base, Ticket, TicketMessage, User
from app.db.session import get_db
from app.main import app
from app.services.chat_service import stream_session_events


class _FakeView:
    """只实现 model_dump(by_alias=True)，喂给生成器够用。"""
    def __init__(self, status, reply=None, steps=0):
        self._d = {"taskStatus": status, "latestReply": reply, "steps": [{}] * steps}

    def model_dump(self, by_alias=True):
        return self._d


def _collect(gen):
    return list(gen)


def test_emits_on_change_and_done_on_terminal():
    views = iter([_FakeView("processing"), _FakeView("processing"),  # 同态不重复推
                  _FakeView("completed", reply="好的", steps=2)])
    out = _collect(stream_session_events(lambda: next(views), sleep=lambda _: None))
    data_events = [e for e in out if e.startswith("data:")]
    assert len(data_events) == 2                 # processing 一次 + completed 一次（中间同态不推）
    assert out[-1].startswith("event: done")     # 终态收尾
    assert "好的" in data_events[-1]


def test_session_not_found_emits_error():
    out = _collect(stream_session_events(lambda: None, sleep=lambda _: None))
    assert out == ['event: error\ndata: {"detail":"session not found"}\n\n']


def test_max_iters_caps_infinite_stream():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return _FakeView("processing")           # 永不终态

    out = _collect(stream_session_events(fetch, max_iters=3, sleep=lambda _: None))
    assert calls["n"] == 3                        # 到上限就停
    assert out[-1].startswith("event: done")      # 兜底也收尾


def test_waiting_user_input_is_a_stream_terminal_state():
    out = _collect(stream_session_events(
        lambda: _FakeView("waiting_user_input", reply="请回复确认或取消"),
        sleep=lambda _: None,
    ))
    assert "waiting_user_input" in out[0]
    assert out[-1].startswith("event: done")


@pytest.fixture
def client():
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
    with TestSession() as s:
        u = User(username="u1"); s.add(u); s.flush()
        t = Ticket(user_id=u.id, category="chat"); s.add(t); s.flush()
        s.add(TicketMessage(ticket_id=t.id, sender_type="agent", content="已为您处理"))
        s.add(AgentSession(id=1, user_id=u.id, ticket_id=t.id, task_status="completed"))
        s.commit()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_stream_endpoint_returns_event_stream(client):
    r = client.get("/api/chat/session/1/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert "已为您处理" in body          # 终态会话的回复被推出
    assert "event: done" in body         # 收尾
