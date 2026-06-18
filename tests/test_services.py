"""M3 services：order / logistics / knowledge / ticket。"""
from datetime import datetime, timedelta

import pytest

from app.agent.state_machine import States
from app.core.exceptions import InvalidStateTransition
from app.db.models import KnowledgeDoc, Logistics
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
