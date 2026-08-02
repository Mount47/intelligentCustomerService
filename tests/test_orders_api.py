"""用户订单接口：只允许查看当前登录用户自己的订单。"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import issue_access_token
from app.db.models import Base, Order, OrderItem, User
from app.db.session import get_db
from app.main import app


@pytest.fixture
def orders_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    with Session() as db:
        owner = User(username="order-owner")
        other = User(username="other-owner")
        db.add_all([owner, other])
        db.flush()
        own_order = Order(
            user_id=owner.id,
            order_no="OWN-001",
            status="paid",
            total_amount=199,
            product_type="normal",
            paid_at=datetime.utcnow(),
        )
        other_order = Order(
            user_id=other.id,
            order_no="OTHER-001",
            status="shipped",
            total_amount=599,
            product_type="normal",
        )
        db.add_all([own_order, other_order])
        db.flush()
        db.add(OrderItem(
            order_id=own_order.id,
            product_name="降噪耳机",
            quantity=1,
            unit_price=199,
        ))
        db.commit()
        owner_id = owner.id
        own_order_id = own_order.id
        other_order_id = other_order.id

    client = TestClient(app, raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {issue_access_token(owner_id)}"
    client.own_order_id = own_order_id
    client.other_order_id = other_order_id
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def test_list_only_returns_current_users_orders(orders_client):
    response = orders_client.get("/api/orders")
    assert response.status_code == 200
    orders = response.json()
    assert [order["orderNo"] for order in orders] == ["OWN-001"]
    assert orders[0]["items"][0]["productName"] == "降噪耳机"
    assert orders[0]["totalAmount"] == 199


def test_get_own_order_and_hide_other_users_order(orders_client):
    own = orders_client.get(f"/api/orders/{orders_client.own_order_id}")
    other = orders_client.get(f"/api/orders/{orders_client.other_order_id}")
    assert own.status_code == 200
    assert own.json()["orderNo"] == "OWN-001"
    assert other.status_code == 404


def test_orders_require_login(orders_client):
    response = orders_client.get("/api/orders", headers={"Authorization": ""})
    assert response.status_code == 401
