"""共享测试夹具：内存 sqlite + StaticPool（commit 后数据不丢，单连接共享）。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Order, User


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def user_order(db):
    """造一个 user + 普通已支付订单，返回 (user, order)。"""
    u = User(username="alice")
    db.add(u)
    db.flush()
    o = Order(user_id=u.id, order_no="SO-T1", status="paid",
              total_amount=100, product_type="normal", paid_at=datetime.utcnow())
    db.add(o)
    db.flush()
    return u, o


def make_order(db, user_id, *, amount=100, product="normal",
               status="paid", delivered_days_ago=None):
    o = Order(user_id=user_id, order_no=f"SO-{amount}-{product}-{status}",
              status=status, total_amount=amount, product_type=product,
              paid_at=datetime.utcnow())
    if delivered_days_ago is not None:
        o.delivered_at = datetime.utcnow() - timedelta(days=delivered_days_ago)
        o.status = "delivered"
    db.add(o)
    db.flush()
    return o
