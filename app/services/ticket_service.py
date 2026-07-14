"""工单业务逻辑。状态变更必须经状态机校验（§7），不在此散落判断。"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.state_machine import StateMachine, States
from app.core.exceptions import ConcurrentStateUpdate, SupportFlowError
from app.db.models import Ticket, TicketMessage
from app.services import sla_service

_sm = StateMachine()

# 终态：不算"进行中"。其余视为进行中（去重用）
_CLOSED_STATES = (States.CLOSED, States.REJECTED, States.RESOLVED_BY_HUMAN)


def get_open_ticket(db: Session, user_id: int, order_id: int, category: str) -> Ticket | None:
    """该用户该订单是否已有进行中的某类工单（去重用：防同订单反复建催件单）。"""
    return db.scalar(
        select(Ticket).where(
            Ticket.user_id == user_id,
            Ticket.order_id == order_id,
            Ticket.category == category,
            Ticket.status.notin_(_CLOSED_STATES),
        ).order_by(Ticket.id.desc())
    )


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
    target = _sm.transition(t.status, target_state, ticket_id=ticket_id)
    return persist_agent_state(
        db, ticket_id, expected_state=t.status, expected_version=t.version,
        target_state=target, pending_context=t.pending_context,
    )


def persist_agent_state(
    db: Session, ticket_id: int, *, expected_state: str, expected_version: int,
    target_state: str, pending_context: dict | None,
) -> Ticket:
    """用 DB 条件更新持久化状态；rowcount=0 表示当前事务已过期，必须整体回滚。"""
    result = db.execute(
        update(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.status == expected_state,
            Ticket.version == expected_version,
        ).values(
            status=target_state,
            pending_context=pending_context,
            version=Ticket.version + 1,
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ConcurrentStateUpdate(
            f"ticket {ticket_id} state/version changed concurrently "
            f"(expected {expected_state}@v{expected_version})"
        )
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise SupportFlowError(f"ticket {ticket_id} not found after state update")
    # CAS 直接更新了 DB，丢掉 identity map 中这三个字段的旧值；其他待提交字段不受影响。
    db.expire(t, ["status", "version", "pending_context"])
    return t


def bind_order(db: Session, ticket_id: int, order_id: int) -> Ticket:
    """给尚未选单的 Ticket 原子绑定订单；并发绑定不同订单时只允许一个胜出。"""
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise SupportFlowError(f"ticket {ticket_id} not found")
    if t.order_id == order_id:
        return t
    if t.order_id is not None:
        raise ConcurrentStateUpdate(
            f"ticket {ticket_id} already bound to order {t.order_id}"
        )
    expected_version = t.version
    result = db.execute(
        update(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.order_id.is_(None),
            Ticket.version == expected_version,
        ).values(
            order_id=order_id,
            version=Ticket.version + 1,
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        db.expire(t, ["order_id", "version"])
        return t

    # 可能是另一事务刚绑定了同一订单（幂等成功），也可能绑定了不同订单（冲突）。
    db.expire(t, ["order_id", "version"])
    if t.order_id == order_id:
        return t
    raise ConcurrentStateUpdate(
        f"ticket {ticket_id} order binding changed concurrently"
    )


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
