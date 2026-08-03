"""事务发件箱（可靠投递）：同事务落库 + 内联投递失败不丢消息 + relay 补投 + 重复投递幂等。

验收对应 `docs/architecture/待办事项.md` 原优先级一：
队列短暂不可用后仍能补投；重复投递不会重复处理业务；有积压数量和失败告警。
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import issue_access_token
from app.db.models import AgentSession, Base, OutboxEvent, User
from app.db.session import get_db
from app.main import app
from app.schemas.chat import ChatMessageIn
from app.services import chat_service, outbox_service
from app.workers import outbox_relay


@pytest.fixture
def env(monkeypatch):
    """API + sqlite + 可控的投递桩。默认 relay 静默期设为 0，便于立即取件。"""
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
    monkeypatch.setenv("OUTBOX_RELAY_DELAY_SEC", "0")
    from app.core import config
    config.get_settings.cache_clear()

    published: list[tuple[int, str]] = []
    fail = {"on": False}

    def _fake_dispatch(sid, trace_id):
        if fail["on"]:
            raise RuntimeError("broker unavailable")
        published.append((sid, trace_id))

    monkeypatch.setattr("app.api.chat._dispatch", _fake_dispatch)

    with TestSession() as s:
        user = User(username="u-outbox")
        s.add(user)
        s.commit()
        token = issue_access_token(user.id)
        user_id = user.id

    client = TestClient(app, raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {token}"
    try:
        yield {"client": client, "session_factory": TestSession, "published": published,
               "fail": fail, "user_id": user_id}
    finally:
        app.dependency_overrides.clear()
        config.get_settings.cache_clear()


def _events(session_factory) -> list[OutboxEvent]:
    with session_factory() as s:
        return list(s.scalars(select(OutboxEvent).order_by(OutboxEvent.id)).all())


# ── 同事务落库 ────────────────────────────────────────────────────────────

def test_event_committed_in_same_transaction_as_session(db):
    """消息、会话、待投递事件必须一起可见——这是发件箱的全部意义。"""
    user = User(username="tx")
    db.add(user)
    db.flush()
    sess, dedup = chat_service.accept_message(
        db, ChatMessageIn(user_id=user.id, content="我要退款"))
    assert dedup is False

    event = db.scalar(select(OutboxEvent).where(
        OutboxEvent.dedup_key == f"agent.process_message:{sess.id}"))
    assert event is not None
    assert event.status == "pending"
    assert event.topic == "agent.process_message"
    assert event.payload["session_id"] == sess.id
    assert event.attempts == 0


def test_dedup_key_rejects_duplicate_event(db):
    """同一会话只允许一行，防止重试路径把同一事件重复写进发件箱。"""
    user = User(username="dup")
    db.add(user)
    db.flush()
    sess, _ = chat_service.accept_message(
        db, ChatMessageIn(user_id=user.id, content="查订单"))
    outbox_service.record_event(
        db, topic="agent.process_message", payload={"session_id": sess.id},
        dedup_key=f"agent.process_message:{sess.id}")
    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_message_does_not_create_second_event(db):
    """客户端重发同一 client_message_id 走幂等快路，不该再产生一条待投递事件。"""
    user = User(username="idem")
    db.add(user)
    db.flush()
    payload = ChatMessageIn(user_id=user.id, content="我要退款", client_message_id="c-1")
    chat_service.accept_message(db, payload)
    _, dedup = chat_service.accept_message(db, payload)
    assert dedup is True
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.status == "pending")) is not None
    assert len(list(db.scalars(select(OutboxEvent)).all())) == 1


# ── 内联投递快路与失败兜底 ────────────────────────────────────────────────

def test_inline_dispatch_success_marks_event_sent(env):
    resp = env["client"].post("/api/chat/message", json={"content": "我要退款"})
    assert resp.status_code == 200
    assert len(env["published"]) == 1

    events = _events(env["session_factory"])
    assert len(events) == 1
    assert events[0].status == "sent"
    assert events[0].sent_at is not None


def test_broker_outage_keeps_request_successful_and_event_pending(env):
    """broker 挂了不该连坐用户请求：仍返回 200，事件留在 pending 等补投。"""
    env["fail"]["on"] = True
    resp = env["client"].post("/api/chat/message", json={"content": "我要退款"})

    assert resp.status_code == 200
    assert resp.json()["taskStatus"] == "queued"
    assert env["published"] == []

    events = _events(env["session_factory"])
    assert len(events) == 1
    assert events[0].status == "pending"


# ── relay 补投 ────────────────────────────────────────────────────────────

def test_relay_redelivers_pending_event_after_outage(env, monkeypatch):
    """队列短暂不可用后仍能补投（验收第 1 条）。"""
    env["fail"]["on"] = True
    env["client"].post("/api/chat/message", json={"content": "我要退款"})
    assert env["published"] == []

    delivered: list[tuple[int, str]] = []
    monkeypatch.setattr(outbox_relay, "_publish",
                        lambda e: delivered.append((e.payload["session_id"],
                                                    e.payload.get("trace_id"))))
    with env["session_factory"]() as s:
        stats = outbox_relay.drain_once(s, Settings())

    assert stats == {"claimed": 1, "sent": 1, "failed": 0}
    assert len(delivered) == 1
    events = _events(env["session_factory"])
    assert events[0].status == "sent"
    assert events[0].attempts == 1


def test_relay_skips_events_not_yet_visible(env, monkeypatch):
    """静默期内的事件归内联快路，relay 不抢，避免正常情况下的重复投递。"""
    env["fail"]["on"] = True
    env["client"].post("/api/chat/message", json={"content": "我要退款"})
    with env["session_factory"]() as s:
        event = s.scalar(select(OutboxEvent))
        event.available_at = datetime.utcnow() + timedelta(seconds=60)
        s.commit()

    monkeypatch.setattr(outbox_relay, "_publish",
                        lambda e: pytest.fail("should not publish yet"))
    with env["session_factory"]() as s:
        stats = outbox_relay.drain_once(s, Settings())
    assert stats["claimed"] == 0


def test_relay_backs_off_and_records_error_on_failure(env, monkeypatch):
    env["fail"]["on"] = True
    env["client"].post("/api/chat/message", json={"content": "我要退款"})

    def _boom(_event):
        raise RuntimeError("still unavailable")

    monkeypatch.setattr(outbox_relay, "_publish", _boom)
    with env["session_factory"]() as s:
        stats = outbox_relay.drain_once(s, Settings())

    assert stats == {"claimed": 1, "sent": 0, "failed": 1}
    events = _events(env["session_factory"])
    assert events[0].status == "pending"          # 仍待补投，不丢
    assert events[0].attempts == 1
    assert "still unavailable" in events[0].last_error
    assert events[0].available_at > datetime.utcnow()   # 已退避


def test_relay_backoff_grows_exponentially_and_is_capped():
    settings = Settings(outbox_backoff_base_sec=2.0, outbox_backoff_max_sec=10.0)
    assert outbox_relay._backoff_sec(1, settings) == 2.0
    assert outbox_relay._backoff_sec(2, settings) == 4.0
    assert outbox_relay._backoff_sec(3, settings) == 8.0
    assert outbox_relay._backoff_sec(9, settings) == 10.0   # 封顶


# ── 重复投递幂等（验收第 2 条）────────────────────────────────────────────

def test_redelivery_of_finished_session_is_noop(env, monkeypatch):
    """补投可能与内联投递重叠。已处理完的会话再被投一次，必须不产生第二次业务处理。"""
    from app.workers import runner

    env["client"].post("/api/chat/message", json={"content": "我要退款"})
    with env["session_factory"]() as s:
        sess = s.scalar(select(AgentSession))
        sess.task_status = "completed"
        s.commit()
        session_id = sess.id

    with env["session_factory"]() as s:
        assert runner._claim_session(s, session_id) is None   # CAS 拒绝重复处理


def test_relay_publish_rejects_unknown_topic(db):
    event = OutboxEvent(topic="totally.unknown", payload={"session_id": 1},
                        dedup_key="totally.unknown:1")
    with pytest.raises(ValueError, match="unknown outbox topic"):
        outbox_relay._publish(event)


# ── 积压与失败告警（验收第 3 条）──────────────────────────────────────────

def test_backlog_stats_counts_pending_and_stuck(db):
    now = datetime.utcnow()
    db.add_all([
        OutboxEvent(topic="agent.process_message", payload={}, dedup_key="a",
                    status="pending", attempts=0, available_at=now),
        OutboxEvent(topic="agent.process_message", payload={}, dedup_key="b",
                    status="pending", attempts=5, available_at=now),
        OutboxEvent(topic="agent.process_message", payload={}, dedup_key="c",
                    status="sent", attempts=1, available_at=now),
    ])
    db.commit()

    stats = outbox_service.backlog_stats(db, stuck_attempts=3)
    assert stats["pending"] == 2
    assert stats["stuck"] == 1          # 只有 attempts>=3 的算需要人工介入
    assert stats["oldest_pending_age_sec"] >= 0


def test_metrics_exposes_outbox_backlog(db):
    from app.observability.metrics import compute_metrics

    db.add(OutboxEvent(topic="agent.process_message", payload={}, dedup_key="m1",
                       status="pending", attempts=0, available_at=datetime.utcnow()))
    db.commit()

    metrics = compute_metrics(db)
    assert metrics["outbox"]["pending"] == 1
    assert "stuck" in metrics["outbox"]


def test_admin_metrics_endpoint_surfaces_outbox(env):
    """积压必须真的到达 /api/admin/metrics——compute_metrics 有值不代表响应模型带得出去。"""
    from app.core.security import issue_access_token
    from app.db.models import User

    env["fail"]["on"] = True
    env["client"].post("/api/chat/message", json={"content": "我要退款"})

    with env["session_factory"]() as s:
        admin = User(username="admin-outbox", role="admin")
        s.add(admin)
        s.commit()
        admin_token = issue_access_token(admin.id)

    resp = env["client"].get("/api/admin/metrics",
                             headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outbox"]["pending"] == 1
