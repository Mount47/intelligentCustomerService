"""工单业务逻辑。状态变更必须经状态机校验（§7），不在此散落判断。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.state_machine import StateMachine, States
from app.core.exceptions import SupportFlowError
from app.db.models import Ticket, TicketMessage
from app.services import sla_service

_sm = StateMachine()


def create_ticket(
    db: Session, user_id: int, category: str, priority: str = "normal",
    order_id: int | None = None,
) -> Ticket:
    deadline = sla_service.compute_deadline(priority)
    t = Ticket(
        user_id=user_id, category=category, priority=priority, order_id=order_id,
        status=States.CREATED, sla_deadline=deadline, created_by_agent=True,
    )
    db.add(t)
    db.flush()
    sla_service.create_sla_record(db, t.id, "resolution", deadline)
    return t


def update_status(db: Session, ticket_id: int, target_state: str) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if not t:
        raise SupportFlowError(f"ticket {ticket_id} not found")
    t.status = _sm.transition(t.status, target_state, ticket_id=ticket_id)
    db.flush()
    return t


def handoff(db: Session, ticket_id: int, reason: str, assigned_to: str | None = None) -> Ticket:
    """转人工：状态 → need_human（经状态机校验合法性）。"""
    t = update_status(db, ticket_id, States.NEED_HUMAN)
    if assigned_to:
        t.assigned_to = assigned_to
    db.flush()
    return t


def add_message(
    db: Session, ticket_id: int, sender_type: str, content: str,
    user_id: int | None = None, client_message_id: str | None = None,
) -> TicketMessage:
    m = TicketMessage(
        ticket_id=ticket_id, sender_type=sender_type, content=content,
        user_id=user_id, client_message_id=client_message_id,
    )
    db.add(m)
    db.flush()
    return m
