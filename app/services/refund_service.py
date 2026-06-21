"""退款业务逻辑 —— 含三层幂等硬模块（ADR-4 / §9.2）。

create_refund_draft：
  1) 客户端 idempotency_key（不传则据业务字段派生，**非 hash(content)**）+ request_hash 判同请求
  2) 服务端 business_key 业务去重
  3) DB 唯一约束兜底（捕 IntegrityError 回查赢家），保证并发只成一条
高金额/特殊商品/超售后期 → require_human_approval，不自动退款（status=pending_human）。
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import IdempotencyKeyConflict, SupportFlowError
from app.db.models import Order, RefundRequest
from app.services import knowledge_service, order_service


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _normalize(reason: str | None) -> str:
    return (reason or "").strip().lower()


def evaluate_refund_risk(
    order: Order, now: datetime | None = None, settings: Settings | None = None
) -> tuple[str, bool, list[str]]:
    """返回 (risk_level, require_human_approval, reasons)。高风险不自动退款。"""
    settings = settings or get_settings()
    now = now or datetime.utcnow()
    reasons: list[str] = []
    require = False
    if float(order.total_amount) > settings.refund_high_amount_threshold:
        require = True
        reasons.append(f"金额超过{settings.refund_high_amount_threshold}")
    if order.product_type in ("fresh_food", "customized_product"):
        require = True
        reasons.append(f"特殊商品({order.product_type})")
    if order.delivered_at and (now - order.delivered_at).days > settings.refund_return_window_days:
        require = True
        reasons.append(f"已签收超过{settings.refund_return_window_days}天")
    return ("high" if require else "low"), require, reasons


def check_refund_policy(
    db: Session, order_id: int, user_id: int, refund_reason: str | None = None,
    now: datetime | None = None,
) -> dict:
    order = order_service.get_order(db, order_id)
    if not order:
        return {"eligible": False, "reason": "order_not_found"}
    if order.user_id != user_id:
        return {"eligible": False, "reason": "not_owner"}
    risk, require_human, reasons = evaluate_refund_risk(order, now)
    doc = knowledge_service.get_policy_by_category(db, "refund_policy")
    return {
        "eligible": order.status != "cancelled",
        "risk_level": risk,
        "require_human_approval": require_human,
        "reasons": reasons,
        "amount": float(order.total_amount),
        "policy_ref": f"{doc.title} {doc.version}" if doc else None,
    }


def _result(rr: RefundRequest, **flags) -> dict:
    out = {
        "refund_request_id": rr.id,
        "status": rr.status,
        "risk_level": rr.risk_level,
        "require_human_approval": rr.require_human_approval,
        "amount": float(rr.amount),
        "idempotent_hit": False,
        "business_dedup": False,
        "race_dedup": False,
        "created": False,
    }
    out.update(flags)
    return out


def create_refund_draft(
    db: Session, *, user_id: int, order_id: int, refund_reason: str | None,
    idempotency_key: str | None = None, now: datetime | None = None,
) -> dict:
    now = now or datetime.utcnow()
    order = order_service.get_order(db, order_id)
    if not order:
        raise SupportFlowError("order_not_found")
    if order.user_id != user_id:
        raise SupportFlowError("not_owner")

    reason_norm = _normalize(refund_reason)
    business_key = _sha(f"{user_id}|{order_id}|refund|{reason_norm}")
    # request_hash：退款关键参数指纹（不含自由文本内容，§9.2 #1）
    request_hash = _sha(f"{order_id}|{float(order.total_amount)}|{reason_norm}")
    idem = idempotency_key or business_key   # 不传则据业务字段派生，绝不用 hash(content)

    # 层1：客户端幂等键（用户维度唯一）
    existing = db.scalar(
        select(RefundRequest).where(
            RefundRequest.user_id == user_id,
            RefundRequest.idempotency_key == idem,
        )
    )
    if existing:
        if existing.request_hash != request_hash:
            raise IdempotencyKeyConflict("same idempotency_key with different request params")
        return _result(existing, idempotent_hit=True)

    # 层2：业务键去重（防 Agent/LLM 重试换措辞）
    existing_b = db.scalar(
        select(RefundRequest).where(RefundRequest.business_key == business_key)
    )
    if existing_b:
        return _result(existing_b, business_dedup=True)

    # 风险评估：高风险不自动退款
    risk, require_human, _reasons = evaluate_refund_risk(order, now)
    rr = RefundRequest(
        order_id=order_id, user_id=user_id, refund_reason=refund_reason,
        amount=order.total_amount, risk_level=risk,
        require_human_approval=require_human,
        idempotency_key=idem, business_key=business_key, request_hash=request_hash,
        status="pending_human" if require_human else "draft",
    )
    db.add(rr)
    # 层3：DB 唯一约束兜底——并发竞争失败则回查赢家
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = db.scalar(
            select(RefundRequest).where(RefundRequest.business_key == business_key)
        ) or db.scalar(
            select(RefundRequest).where(
                RefundRequest.user_id == user_id,
                RefundRequest.idempotency_key == idem,
            )
        )
        if winner is None:  # 理论不该发生
            raise
        return _result(winner, race_dedup=True)
    db.refresh(rr)
    return _result(rr, created=True)


def get_refund_status(db: Session, refund_id: int) -> dict | None:
    rr = db.get(RefundRequest, refund_id)
    return _result(rr) if rr else None


# 进行中的退款状态（会话级幂等用：同订单已有这些状态的退款则不再新建）
_ACTIVE_REFUND_STATUSES = ("draft", "pending_human", "approved")


def get_active_refund(db: Session, user_id: int, order_id: int) -> RefundRequest | None:
    """该用户该订单当前是否已有进行中的退款。

    三层幂等防的是"同一请求重发"；这里防的是"同订单跨对话轮次/换措辞反复触发"
    （会话级幂等，§9 补充 / 见 实际问题与解决.md #8）。
    """
    return db.scalar(
        select(RefundRequest).where(
            RefundRequest.user_id == user_id,
            RefundRequest.order_id == order_id,
            RefundRequest.status.in_(_ACTIVE_REFUND_STATUSES),
        ).order_by(desc(RefundRequest.id))
    )


def cancel_active_refund(db: Session, user_id: int, order_id: int) -> dict | None:
    """撤销该订单进行中的退款（draft/pending_human → cancelled）。无则返回 None。"""
    rr = get_active_refund(db, user_id, order_id)
    if rr is None:
        return None
    rr.status = "cancelled"
    db.commit()
    return _result(rr)
