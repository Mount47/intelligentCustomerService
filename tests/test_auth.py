"""Bearer 身份认证与 admin RBAC。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password, issue_access_token, verify_password
from app.db.models import Base, User
from app.db.session import get_db
from app.main import app


@pytest.fixture
def auth_client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr("app.api.chat._dispatch", lambda session_id, trace_id: None)
    with Session() as db:
        user = User(
            username="alice",
            password_hash=hash_password("alice-password"),
            role="user",
        )
        admin = User(
            username="ops",
            password_hash=hash_password("admin-password"),
            role="admin",
        )
        db.add_all([user, admin])
        db.commit()
        ids = {"user": user.id, "admin": admin.id}
    client = TestClient(app, raise_server_exceptions=False)
    client.user_id = ids["user"]
    client.admin_id = ids["admin"]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct-horse")
    second = hash_password("correct-horse")
    assert first != second
    assert verify_password("correct-horse", first)
    assert not verify_password("wrong-password", first)


def test_login_issues_token_and_token_authenticates_user(auth_client):
    response = auth_client.post(
        "/api/auth/token",
        json={"username": "alice", "password": "alice-password"},
    )
    assert response.status_code == 200
    token = response.json()["accessToken"]
    send = auth_client.post(
        "/api/chat/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "你好"},
    )
    assert send.status_code == 200


def test_missing_or_tampered_token_is_rejected(auth_client):
    assert auth_client.post("/api/chat/message", json={"content": "你好"}).status_code == 401
    assert auth_client.post(
        "/api/chat/message",
        headers={"Authorization": "Bearer broken.token"},
        json={"content": "你好"},
    ).status_code == 401


def test_request_user_id_cannot_override_authenticated_identity(auth_client):
    token = issue_access_token(auth_client.user_id)
    response = auth_client.post(
        "/api/chat/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"userId": auth_client.admin_id, "content": "伪造身份"},
    )
    assert response.status_code == 403


def test_admin_rbac_denies_user_and_allows_admin(auth_client):
    user_token = issue_access_token(auth_client.user_id)
    admin_token = issue_access_token(auth_client.admin_id)
    denied = auth_client.get(
        "/api/admin/metrics", headers={"Authorization": f"Bearer {user_token}"})
    allowed = auth_client.get(
        "/api/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"})
    assert denied.status_code == 403
    assert allowed.status_code == 200
