"""M2 模型测试：建表 + 退款双唯一约束 + 消息幂等。用内存 sqlite，不依赖 postgres。"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    Order,
    RefundRequest,
    Ticket,
    TicketMessage,
    User,
)
from app.db.init_db import ensure_mvp_schema_compat


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def test_all_tables_created():
    assert len(Base.metadata.tables) == 13


def _user_order(db):
    u = User(username="u1")
    db.add(u)
    db.flush()
    o = Order(user_id=u.id, order_no="SO-1", status="paid", total_amount=100)
    db.add(o)
    db.flush()
    return u, o


def _refund(u, o, idem, business, rhash="h1"):
    return RefundRequest(
        order_id=o.id, user_id=u.id, amount=100,
        idempotency_key=idem, business_key=business, request_hash=rhash,
    )


def test_refund_unique_user_idempotency_key(db):
    u, o = _user_order(db)
    db.add(_refund(u, o, "idem-1", "biz-1"))
    db.commit()
    # 同 (user_id, idempotency_key) → 违反 uq_refund_user_idem
    db.add(_refund(u, o, "idem-1", "biz-2"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_refund_unique_business_key(db):
    u, o = _user_order(db)
    db.add(_refund(u, o, "idem-A", "biz-X"))
    db.commit()
    # 同 business_key → 违反 uq_refund_business
    db.add(_refund(u, o, "idem-B", "biz-X"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_message_idempotency(db):
    u, _ = _user_order(db)
    t = Ticket(user_id=u.id, category="refund")
    db.add(t)
    db.flush()
    db.add(TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user",
                         content="hi", client_message_id="cmsg-1"))
    db.commit()
    db.add(TicketMessage(ticket_id=t.id, user_id=u.id, sender_type="user",
                         content="hi again", client_message_id="cmsg-1"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_mvp_schema_compat_adds_pending_context_to_existing_ticket_table():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id INTEGER PRIMARY KEY)"))

    ensure_mvp_schema_compat(engine)
    ensure_mvp_schema_compat(engine)  # 幂等：重复初始化不应再次 ALTER

    columns = {c["name"] for c in inspect(engine).get_columns("tickets")}
    assert "pending_context" in columns
    assert "version" in columns
