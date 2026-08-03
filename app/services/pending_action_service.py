"""待确认写操作的统一安全校验。

文本“确认”和结构化 ``/chat/action`` 都必须经过这里。校验只读取事实；调用方在
失败时清理 pending_action，并按各自协议返回提示，避免异常进入 Worker 重试。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.agent.state_machine import States
from app.core.exceptions import InvalidPendingAction
from app.db.models import Order, Ticket

PENDING_ACTION_TTL_SECONDS = 3600
_REFUND_ACTION_TYPES = {"refund_request", "return_request"}


@dataclass(frozen=True)
class ValidatedPendingAction:
    action_type: str
    order: Order
    amount: Decimal
    created_at: datetime


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_refund_pending_action(
    db: Session,
    ticket: Ticket | None,
    user_id: int,
    *,
    now: datetime | None = None,
    allow_submitted_confirm: bool = False,
) -> ValidatedPendingAction:
    """校验退款/退货待确认动作，并返回经过类型收窄的可信数据。"""
    if ticket is None or ticket.user_id != user_id:
        raise InvalidPendingAction("待确认操作不存在，请重新发起申请")
    if ticket.status != States.WAITING_USER_CONFIRM:
        raise InvalidPendingAction("当前没有等待确认的操作，请重新发起申请")

    pending = ticket.pending_action
    if not isinstance(pending, dict) or not pending:
        raise InvalidPendingAction("待确认操作数据不完整，请重新发起申请")
    required = ("type", "order_id", "amount", "created_at")
    if any(key not in pending or pending[key] is None for key in required):
        raise InvalidPendingAction("待确认操作数据不完整，请重新发起申请")
    if pending["type"] not in _REFUND_ACTION_TYPES:
        raise InvalidPendingAction("待确认操作类型无效，请重新发起申请")

    submitted = pending.get("submitted_action")
    allowed_submitted = {None, "confirm"} if allow_submitted_confirm else {None}
    if submitted not in allowed_submitted:
        raise InvalidPendingAction("该操作已经提交或状态异常，请重新发起申请")

    try:
        if isinstance(pending["order_id"], bool):
            raise ValueError
        order_id = int(pending["order_id"])
        amount = Decimal(str(pending["amount"]))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        created_at = _utc_naive(datetime.fromisoformat(str(pending["created_at"])))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise InvalidPendingAction("待确认操作数据格式错误，请重新发起申请") from None

    check_time = _utc_naive(now or datetime.utcnow())
    age = check_time - created_at
    if age < -timedelta(minutes=5):
        raise InvalidPendingAction("待确认操作时间异常，请重新发起申请")
    if age.total_seconds() > PENDING_ACTION_TTL_SECONDS:
        raise InvalidPendingAction("确认已超时，请重新发起申请")

    order = db.get(Order, order_id)
    if order is None or order.user_id != user_id:
        raise InvalidPendingAction("订单归属或状态已经变化，请重新发起申请")
    if ticket.order_id != order.id:
        raise InvalidPendingAction("工单绑定的订单已经变化，请重新发起申请")
    if Decimal(str(order.total_amount)) != amount:
        raise InvalidPendingAction("订单金额已经变化，请重新发起申请")

    return ValidatedPendingAction(
        action_type=str(pending["type"]),
        order=order,
        amount=amount,
        created_at=created_at,
    )


def clear_pending_action(ticket: Ticket | None) -> None:
    """清理损坏或失效的待确认动作；调用方负责提交事务。"""
    if ticket is None:
        return
    ticket.pending_action = None
    flag_modified(ticket, "pending_action")

