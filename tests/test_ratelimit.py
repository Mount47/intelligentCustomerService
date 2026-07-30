"""入口限流：固定窗口计数 + fail-open + API 429 接入。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.ratelimit import allow_request
from app.core.security import issue_access_token
from app.db.models import Base, User
from app.db.session import get_db
from app.main import app


class _FakeRedis:
    """内存假 Redis：支持 incr/expire，供 CI 下确定性测限流（不连真 Redis）。"""
    def __init__(self):
        self.store: dict[str, int] = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, sec):
        pass


class _BrokenRedis:
    def incr(self, key):
        raise ConnectionError("redis down")

    def expire(self, key, sec):
        pass


def test_allow_under_limit_then_deny():
    cli = _FakeRedis()
    results = [allow_request("u1", limit=3, window_sec=60, client=cli)[0] for _ in range(4)]
    assert results == [True, True, True, False]   # 第 4 次超额被拒


def test_limit_zero_disables():
    cli = _FakeRedis()
    for _ in range(100):
        assert allow_request("u1", limit=0, client=cli)[0] is True


def test_fail_open_when_redis_down():
    # Redis 异常 → 放行（限流不可用不该误杀正常流量）
    assert allow_request("u1", limit=1, client=_BrokenRedis()) == (True, 0)


def test_separate_buckets_independent():
    cli = _FakeRedis()
    assert allow_request("a", limit=1, client=cli)[0] is True
    assert allow_request("a", limit=1, client=cli)[0] is False   # a 用完
    assert allow_request("b", limit=1, client=cli)[0] is True    # b 不受影响


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
    monkeypatch.setattr("app.api.chat._dispatch", lambda sid, trace_id: None)
    with TestSession() as s:
        user = User(username="u1")
        s.add(user)
        s.commit()
        token = issue_access_token(user.id)
    try:
        client = TestClient(app, raise_server_exceptions=False)
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
    finally:
        app.dependency_overrides.clear()


def test_api_returns_429_when_limited(client, monkeypatch):
    # 限流判定为拒 → POST 在入队前返回 429
    monkeypatch.setattr("app.api.chat.allow_request", lambda *a, **k: (False, 99))
    r = client.post("/api/chat/message", json={"user_id": 1, "content": "你好"})
    assert r.status_code == 429
