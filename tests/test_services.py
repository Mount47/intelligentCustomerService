"""M3 services：order / logistics / knowledge / ticket。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.state_machine import States
from app.core.exceptions import ConcurrentStateUpdate, InvalidStateTransition
from app.db.models import Base, KnowledgeDoc, Logistics, Order, Ticket, User
from app.services import (
    knowledge_service,
    logistics_service,
    order_service,
    ticket_service,
)


def test_order_owner(db, user_order):
    u, o = user_order
    assert order_service.check_owner(db, o.id, u.id) is True
    assert order_service.check_owner(db, o.id, u.id + 999) is False


def test_logistics_stale_is_exception(db, user_order):
    _, o = user_order
    db.add(Logistics(order_id=o.id, status="in_transit", carrier="顺丰",
                     last_update_time=datetime.utcnow() - timedelta(hours=60)))
    db.flush()
    res = logistics_service.detect_exception(db, o.id, stale_hours=48)
    assert res["found"] is True
    assert res["stale"] is True
    assert res["is_exception"] is True


def test_knowledge_search_by_keyword(db):
    db.add(KnowledgeDoc(title="退款政策", category="refund_policy", content="...退款规则..."))
    db.flush()
    hits = knowledge_service.search_policy_docs(db, "我要退款")
    assert hits and hits[0].category == "refund_policy"
    assert knowledge_service.get_policy_by_category(db, "refund_policy") is not None


def test_ticket_create_and_legal_transition(db, user_order):
    u, o = user_order
    t = ticket_service.create_ticket(db, u.id, category="refund", order_id=o.id)
    assert t.status == States.CREATED
    assert t.sla_deadline is not None
    t = ticket_service.update_status(db, t.id, States.INTENT_DETECTED)
    assert t.status == States.INTENT_DETECTED


def test_ticket_illegal_transition_raises(db, user_order):
    u, _ = user_order
    t = ticket_service.create_ticket(db, u.id, category="refund")
    with pytest.raises(InvalidStateTransition):
        ticket_service.update_status(db, t.id, States.CLOSED)  # created 不可直达 closed


def test_ticket_handoff(db, user_order):
    u, _ = user_order
    t = ticket_service.create_ticket(db, u.id, category="complaint", priority="high")
    ticket_service.update_status(db, t.id, States.INTENT_DETECTED)
    t = ticket_service.handoff(db, t.id, reason="用户要求人工")
    assert t.status == States.NEED_HUMAN


def test_ticket_state_cas_rejects_stale_writer(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'state-cas.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as setup:
        user = User(username="state-cas")
        setup.add(user)
        setup.flush()
        ticket = Ticket(user_id=user.id, category="chat")
        setup.add(ticket)
        setup.commit()
        ticket_id = ticket.id

    first, stale = Session(), Session()
    try:
        first.get(Ticket, ticket_id)
        stale_ticket = stale.get(Ticket, ticket_id)  # 缓存 created@v0

        ticket_service.update_status(first, ticket_id, States.INTENT_DETECTED)
        first.commit()

        assert stale_ticket.status == States.CREATED and stale_ticket.version == 0
        with pytest.raises(ConcurrentStateUpdate):
            ticket_service.update_status(stale, ticket_id, States.INTENT_DETECTED)
        stale.rollback()
    finally:
        first.close()
        stale.close()

    with Session() as check:
        current = check.get(Ticket, ticket_id)
        assert current.status == States.INTENT_DETECTED
        assert current.version == 1


def test_ticket_order_binding_cas_rejects_different_concurrent_order(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'order-bind-cas.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as setup:
        user = User(username="order-bind-cas")
        setup.add(user)
        setup.flush()
        first_order = Order(user_id=user.id, order_no="CAS-O1", status="paid", total_amount=10)
        second_order = Order(user_id=user.id, order_no="CAS-O2", status="paid", total_amount=20)
        ticket = Ticket(user_id=user.id, category="chat")
        setup.add_all([first_order, second_order, ticket])
        setup.commit()
        ids = ticket.id, first_order.id, second_order.id

    winner, stale = Session(), Session()
    try:
        winner.get(Ticket, ids[0])
        stale.get(Ticket, ids[0])
        ticket_service.bind_order(winner, ids[0], ids[1])
        winner.commit()

        with pytest.raises(ConcurrentStateUpdate):
            ticket_service.bind_order(stale, ids[0], ids[2])
        stale.rollback()
    finally:
        winner.close()
        stale.close()

    with Session() as check:
        current = check.get(Ticket, ids[0])
        assert current.order_id == ids[1]
        assert current.version == 1
