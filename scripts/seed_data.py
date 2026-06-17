"""种子数据（§18）：10 用户 / 50 订单（各状态+各商品类型）/ 物流（各状态）/ 政策文档。

幂等：已存在用户则跳过（避免重复 seed）。固定随机种子保证可复现。
用法：python -m scripts.seed_data
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.core.logging import get_logger, setup_logging
from app.db.init_db import init_db
from app.db.models import KnowledgeDoc, Logistics, Order, User
from app.db.session import SessionLocal

logger = get_logger(__name__)

ORDER_STATUSES = ["pending_payment", "paid", "shipped", "delivered", "cancelled"]
PRODUCT_TYPES = ["normal", "fresh_food", "customized_product"]
LOGI_STATUSES = ["pending", "in_transit", "delivered", "exception"]

POLICIES = [
    ("退款政策", "refund_policy",
     "未发货订单可全额退款。已发货未签收需先拦截。普通商品签收后7天内可申请退款；"
     "生鲜、定制商品一经售出原则上不支持无理由退款，需人工审核。单笔金额超过500元需人工审批。"),
    ("退货政策", "return_policy",
     "签收后7天内、商品完好可申请退货退款；定制商品、生鲜不支持7天无理由退货。"),
    ("物流异常处理政策", "logistics_policy",
     "物流超过48小时无更新视为异常，可发起催件工单；显示签收但用户未收到需转人工核实。"),
    ("发票申请规则", "invoice_policy",
     "已支付订单可申请电子发票，需提供抬头、税号（企业）、接收邮箱。"),
    ("优惠券使用规则", "coupon_policy",
     "优惠券有有效期与品类限制，过期不可用；退款时优惠券一般不返还。"),
    ("投诉升级规则", "complaint_policy",
     "商品质量投诉创建高优先级工单并转人工；不得承诺超出售后政策的赔偿。"),
    ("人工客服转接规则", "handoff_policy",
     "用户明确要求人工、情绪激烈、高风险退款、工具失败或知识库无答案时，转人工。"),
]


def seed() -> None:
    init_db()
    rng = random.Random(42)
    with SessionLocal() as db:
        if db.query(User).count() > 0:
            logger.info("seed skipped: users already exist")
            return

        users = [
            User(username=f"user{i:02d}", phone=f"1380000{i:04d}",
                 email=f"user{i:02d}@example.com",
                 user_level="vip" if i % 5 == 0 else "normal")
            for i in range(1, 11)
        ]
        db.add_all(users)
        db.flush()  # 拿到 user.id

        now = datetime.utcnow()
        orders: list[Order] = []
        for n in range(1, 51):
            u = rng.choice(users)
            status = rng.choice(ORDER_STATUSES)
            ptype = rng.choice(PRODUCT_TYPES)
            paid = shipped = delivered = None
            if status in ("paid", "shipped", "delivered"):
                paid = now - timedelta(days=rng.randint(1, 20))
            if status in ("shipped", "delivered"):
                shipped = (paid or now) + timedelta(days=1)
            if status == "delivered":
                delivered = (shipped or now) + timedelta(days=rng.randint(1, 10))
            orders.append(Order(
                user_id=u.id, order_no=f"SO{now:%Y%m%d}{n:04d}", status=status,
                total_amount=round(rng.uniform(20, 1200), 2), product_type=ptype,
                paid_at=paid, shipped_at=shipped, delivered_at=delivered,
            ))
        db.add_all(orders)
        db.flush()

        for o in orders:
            if o.status in ("shipped", "delivered"):
                lstatus = "delivered" if o.status == "delivered" else rng.choice(
                    ["in_transit", "in_transit", "exception"]
                )
                is_exc = lstatus == "exception"
                # 一部分制造 48h 无更新，供物流异常链路测试
                last_update = o.shipped_at + timedelta(hours=rng.choice([2, 30, 60, 80]))
                db.add(Logistics(
                    order_id=o.id, carrier=rng.choice(["顺丰", "中通", "京东物流"]),
                    tracking_no=f"YT{rng.randint(10**10, 10**11)}",
                    status=lstatus,
                    last_location=rng.choice(["上海转运中心", "广州分拨", "派送中"]),
                    last_update_time=last_update,
                    is_exception=is_exc,
                    exception_reason="长时间无揽收/中转停滞" if is_exc else None,
                ))

        db.add_all(KnowledgeDoc(title=t, category=c, content=body) for t, c, body in POLICIES)
        db.commit()
        logger.info("seeded: %d users, %d orders, %d policies",
                    len(users), len(orders), len(POLICIES))


if __name__ == "__main__":
    setup_logging()
    seed()
