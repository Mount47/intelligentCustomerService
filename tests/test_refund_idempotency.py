"""M3 硬模块：退款三层幂等（ADR-4 / §9.2）。

覆盖：层1 客户端幂等键命中、层2 业务键去重、同 key 不同参数冲突、高风险需人工、只创建一条。
真并发(多线程/进程)测试用 postgres 在 M9 补（sqlite 难真并发）。
"""
import pytest

from app.core.exceptions import IdempotencyKeyConflict
from app.db.models import RefundRequest
from app.services import refund_service

from .conftest import make_order


def test_layer1_idempotency_key_hit(db, user_order):
    u, o = user_order
    r1 = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=o.id, refund_reason="不想要了", idempotency_key="k1")
    r2 = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=o.id, refund_reason="不想要了", idempotency_key="k1")
    assert r1["created"] is True
    assert r2["idempotent_hit"] is True
    assert r1["refund_request_id"] == r2["refund_request_id"]
    assert db.query(RefundRequest).count() == 1


def test_layer2_business_key_dedup(db, user_order):
    u, o = user_order
    # 不同 idempotency_key，但 user+order+reason 相同 → business_key 相同
    r1 = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=o.id, refund_reason="质量问题", idempotency_key="ka")
    r2 = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=o.id, refund_reason="质量问题", idempotency_key="kb")
    assert r1["created"] is True
    assert r2["business_dedup"] is True
    assert r1["refund_request_id"] == r2["refund_request_id"]
    assert db.query(RefundRequest).count() == 1


def test_same_key_diff_params_conflict(db, user_order):
    u, o = user_order
    refund_service.create_refund_draft(
        db, user_id=u.id, order_id=o.id, refund_reason="原因A", idempotency_key="k1")
    with pytest.raises(IdempotencyKeyConflict):
        refund_service.create_refund_draft(
            db, user_id=u.id, order_id=o.id, refund_reason="原因B", idempotency_key="k1")


def test_high_amount_requires_human(db, user_order):
    u, _ = user_order
    big = make_order(db, u.id, amount=999)   # > 默认阈值 500
    res = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=big.id, refund_reason="贵了")
    assert res["require_human_approval"] is True
    assert res["risk_level"] == "high"
    assert res["status"] == "pending_human"


def test_fresh_food_requires_human(db, user_order):
    u, _ = user_order
    fresh = make_order(db, u.id, amount=50, product="fresh_food")
    res = refund_service.create_refund_draft(
        db, user_id=u.id, order_id=fresh.id, refund_reason="坏了")
    assert res["require_human_approval"] is True
