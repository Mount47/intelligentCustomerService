"""种子数据：随机基础数据、可复现场景数据、完整演示用户和政策文档。

幂等：基础数据只在空库生成；完整演示用户采用增量补齐，可反复执行且不清除现有数据。
固定随机种子保证可复现。
用法：python -m scripts.seed_data
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.core.logging import get_logger, setup_logging
from app.core.security import hash_password
from app.db.init_db import init_db
from app.db.models import KnowledgeDoc, Logistics, Order, OrderItem, User
from app.db.session import SessionLocal

logger = get_logger(__name__)

ORDER_STATUSES = ["pending_payment", "paid", "shipped", "delivered", "cancelled"]
PRODUCT_TYPES = ["normal", "fresh_food", "customized_product"]
LOGI_STATUSES = ["pending", "in_transit", "delivered", "exception"]

# 各商品类型的候选商品名，给订单造条目级明细（支撑"我买了什么"）
PRODUCT_CATALOG = {
    "normal": ["蓝牙耳机", "保温杯", "运动鞋", "机械键盘", "双肩包"],
    "fresh_food": ["阳光玫瑰葡萄", "智利车厘子", "现切牛排", "海南芒果"],
    "customized_product": ["定制刻字钢笔", "定制相册", "定制T恤"],
}


def _add_items(db, order: Order, rng: random.Random) -> None:
    """给订单造 1~3 条商品明细，单价合计大致贴近订单金额。"""
    names = PRODUCT_CATALOG.get(order.product_type, PRODUCT_CATALOG["normal"])
    k = rng.randint(1, min(3, len(names)))
    picks = rng.sample(names, k)
    total = float(order.total_amount)
    for i, name in enumerate(picks):
        qty = rng.randint(1, 2)
        # 末条用余额兜底，保证条目合计 ≈ 订单金额
        unit = round(total / (k * qty), 2) if i < k - 1 else round(max(total / qty, 1), 2)
        total -= unit * qty
        db.add(OrderItem(order_id=order.id, product_name=name, quantity=qty, unit_price=max(unit, 1)))

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


# 确定性"场景订单"：每条覆盖一个 Skill 分支，固定 order_no，可复现。
# (order_no, 建议消息, 预期场景, 订单条件)
SCENARIOS = [
    ("DEMO-REFUND-LOW", "我要退款", "退款·低风险→等待用户确认", {"amount": 120}),
    ("DEMO-REFUND-500", "我要退款", "退款·金额边界500→等待用户确认", {"amount": 500}),
    ("DEMO-REFUND-501", "我要退款", "退款·金额边界501→人工", {"amount": 501}),
    ("DEMO-REFUND-HIGH", "我要退款", "退款·高金额→人工", {"amount": 999}),
    ("DEMO-REFUND-FRESH", "这个生鲜我要退款", "退款·生鲜→人工", {"amount": 60, "product": "fresh_food"}),
    ("DEMO-REFUND-CUSTOM", "这个定制的我要退款", "退款·定制→人工", {"amount": 80, "product": "customized_product"}),
    ("DEMO-REFUND-OVERDUE", "我要申请退款", "退款·签收超7天→人工", {"amount": 100, "delivered_days_ago": 10}),
    ("DEMO-REFUND-INWINDOW", "我要退款", "退款·签收7天内→等待用户确认", {"amount": 100, "delivered_days_ago": 2}),
    ("DEMO-REFUND-CANCELLED", "我要退款", "退款·已取消→不可退转人工", {"amount": 100, "status": "cancelled"}),
    ("DEMO-REFUND-OTHEROWNER", "我要退款", "退款·非本人订单→转人工", {"amount": 100, "other_owner": True}),
    ("DEMO-RETURN", "我要退货", "退货·窗口内→等待用户确认", {"amount": 100, "delivered_days_ago": 2}),
    ("DEMO-LOGI-NORMAL", "我的快递到哪了", "物流·正常播报",
     {"status": "shipped", "logistics": {"status": "in_transit", "hours_ago": 2}}),
    ("DEMO-LOGI-STALE", "我的快递怎么还没动", "物流·48h无更新→催件",
     {"status": "shipped", "logistics": {"status": "in_transit", "hours_ago": 60}}),
    ("DEMO-LOGI-EXC", "我的快递怎么了", "物流·异常标记→催件",
     {"status": "shipped", "logistics": {"status": "in_transit", "hours_ago": 5, "is_exception": True, "reason": "中转停滞"}}),
    ("DEMO-LOGI-NOTRECV", "物流显示签收了但我没收到", "物流·签收未收到→转人工",
     {"status": "delivered", "delivered_days_ago": 1, "logistics": {"status": "delivered", "hours_ago": 20}}),
    ("DEMO-LOGI-NOINFO", "我的快递到哪了", "物流·无信息→转人工", {"status": "paid"}),
]

# 纯意图场景（不依赖订单，由消息内容触发）
INTENT_ONLY = [
    ("我要开发票", "发票(v1兜底)"),
    ("我的优惠券不能用", "优惠券(v1兜底)"),
    ("东西质量太差我要投诉", "投诉→转人工"),
    ("我要转人工", "要求人工→转人工"),
    ("今天天气怎么样", "超范围→兜底"),
    ("想了解你们的售后规则", "政策问答→兜底"),
]

# 第二个演示用户的数据更贴近日常操作：订单名称、金额、时间、商品明细和物流节点都固定，
# 登录网页后可以直接逐单验证，不必记技术边界编号。
SHOWCASE_USERNAME = "demo_vip"
SHOWCASE_PASSWORD = "supportflow-user"
SHOWCASE_ORDERS = [
    {
        "order_no": "VIP-WAIT-PAY", "status": "pending_payment", "amount": 269,
        "items": [("机械键盘", 1, 269)], "message": "这笔订单是什么状态？",
        "expected": "订单·待付款，无物流",
    },
    {
        "order_no": "VIP-WAIT-SHIP", "status": "paid", "amount": 199,
        "paid_hours_ago": 12, "items": [("蓝牙耳机", 1, 199)],
        "message": "什么时候发货？", "expected": "订单·已付款待发货",
    },
    {
        "order_no": "VIP-LOGI-PICKUP", "status": "shipped", "amount": 119,
        "paid_hours_ago": 30, "shipped_hours_ago": 6,
        "items": [("保温杯", 2, 59.5)],
        "logistics": {"status": "pending", "hours_ago": 4, "location": "杭州电商仓"},
        "message": "快递揽收了吗？", "expected": "物流·等待揽收",
    },
    {
        "order_no": "VIP-LOGI-NORMAL", "status": "shipped", "amount": 239,
        "paid_hours_ago": 50, "shipped_hours_ago": 28,
        "items": [("双肩包", 1, 239)],
        "logistics": {"status": "in_transit", "hours_ago": 3, "location": "苏州分拨中心"},
        "message": "我的快递到哪里了？", "expected": "物流·正常运输，播报最近位置",
    },
    {
        "order_no": "VIP-LOGI-DELIVERY", "status": "shipped", "amount": 329,
        "paid_hours_ago": 72, "shipped_hours_ago": 48,
        "items": [("运动鞋", 1, 329)],
        "logistics": {
            "status": "in_transit", "hours_ago": 1, "location": "上海市浦东新区派送网点"
        },
        "message": "今天能收到吗？", "expected": "物流·正在派送，只播报已有事实",
    },
    {
        "order_no": "VIP-LOGI-STALE", "status": "shipped", "amount": 458,
        "paid_hours_ago": 120, "shipped_hours_ago": 96,
        "items": [("机械键盘", 1, 329), ("无线鼠标", 1, 129)],
        "logistics": {"status": "in_transit", "hours_ago": 72, "location": "武汉转运中心"},
        "message": "为什么物流一直不动？",
        "expected": "物流·超过48小时，位置与时间照常播报并创建催件工单",
    },
    {
        "order_no": "VIP-LOGI-EXCEPTION", "status": "shipped", "amount": 369,
        "paid_hours_ago": 72, "shipped_hours_ago": 50,
        "items": [("运动鞋", 1, 369)],
        "logistics": {
            "status": "exception", "hours_ago": 6, "location": "郑州中转场",
            "is_exception": True, "reason": "受强降雨影响，运输暂时延迟",
        },
        "message": "快递是不是出问题了？", "expected": "物流·明确异常并创建催件工单",
    },
    {
        "order_no": "VIP-DELIVERED-NOTRECV", "status": "delivered", "amount": 299,
        "paid_hours_ago": 96, "shipped_hours_ago": 72, "delivered_days_ago": 0,
        "items": [("蓝牙耳机", 1, 299)],
        "logistics": {"status": "delivered", "hours_ago": 5, "location": "小区前台"},
        "message": "物流显示签收了但我没收到", "expected": "物流·签收未收到，转人工核实",
    },
    {
        "order_no": "VIP-REFUND-RECENT", "status": "delivered", "amount": 168,
        "paid_hours_ago": 120, "shipped_hours_ago": 96, "delivered_days_ago": 2,
        "items": [("双肩包", 1, 168)],
        "logistics": {"status": "delivered", "hours_ago": 48, "location": "本人签收"},
        "message": "我想申请退款", "expected": "退款·普通商品7天内，等待二次确认",
    },
    {
        "order_no": "VIP-REFUND-OVERDUE", "status": "delivered", "amount": 399,
        "paid_hours_ago": 360, "shipped_hours_ago": 336, "delivered_days_ago": 10,
        "items": [("运动鞋", 1, 399)],
        "logistics": {"status": "delivered", "hours_ago": 240, "location": "本人签收"},
        "message": "我想申请退款", "expected": "退款·签收超过7天，转人工审核",
    },
    {
        "order_no": "VIP-REFUND-HIGH", "status": "paid", "amount": 899,
        "paid_hours_ago": 8, "items": [("机械键盘", 1, 899)],
        "message": "我要退款", "expected": "退款·金额超过500元，转人工审核",
    },
    {
        "order_no": "VIP-REFUND-FRESH", "status": "delivered", "amount": 128,
        "product": "fresh_food", "paid_hours_ago": 72, "shipped_hours_ago": 50,
        "delivered_days_ago": 1, "items": [("阳光玫瑰葡萄", 2, 64)],
        "logistics": {"status": "delivered", "hours_ago": 24, "location": "本人签收"},
        "message": "葡萄不想要了，申请退款", "expected": "退款·生鲜商品，转人工审核",
    },
    {
        "order_no": "VIP-REFUND-CUSTOM", "status": "delivered", "amount": 258,
        "product": "customized_product", "paid_hours_ago": 120, "shipped_hours_ago": 96,
        "delivered_days_ago": 3, "items": [("定制相册", 1, 258)],
        "logistics": {"status": "delivered", "hours_ago": 72, "location": "本人签收"},
        "message": "定制相册可以退吗？", "expected": "退款咨询·定制商品政策",
    },
    {
        "order_no": "VIP-CANCELLED", "status": "cancelled", "amount": 99,
        "items": [("保温杯", 1, 99)], "message": "取消的订单还能退款吗？",
        "expected": "订单·已取消，不重复退款",
    },
]


def _seed_scenarios(db, now: datetime) -> tuple[int, list[tuple]]:
    """造场景订单，返回 (demo 用户 id, [(order_id, order_no, 消息, 预期)])。"""
    demo = User(
        username="demo", phone="13900000000", email="demo@example.com",
        password_hash=hash_password("supportflow-user"))
    other = User(
        username="demo_other", phone="13900000001", email="other@example.com",
        password_hash=hash_password("supportflow-user"))
    db.add_all([demo, other])
    db.flush()
    rng = random.Random(7)   # 场景订单的明细也要可复现
    rows: list[tuple] = []
    for order_no, message, expected, spec in SCENARIOS:
        owner = other.id if spec.get("other_owner") else demo.id
        o = Order(user_id=owner, order_no=order_no, status=spec.get("status", "paid"),
                  total_amount=spec.get("amount", 100),
                  product_type=spec.get("product", "normal"), paid_at=now)
        dd = spec.get("delivered_days_ago")
        if dd is not None:
            o.delivered_at = now - timedelta(days=dd)
            o.status = "delivered"
        db.add(o)
        db.flush()
        _add_items(db, o, rng)
        lg = spec.get("logistics")
        if lg:
            db.add(Logistics(
                order_id=o.id, carrier="顺丰", tracking_no=f"DEMO{o.id}",
                status=lg.get("status", "in_transit"),
                last_update_time=now - timedelta(hours=lg.get("hours_ago", 2)),
                last_location="上海转运中心", is_exception=lg.get("is_exception", False),
                exception_reason=lg.get("reason")))
            db.flush()
        rows.append((o.id, order_no, message, expected))
    return demo.id, rows


def _seed_showcase_user(db, now: datetime) -> tuple[int, list[tuple], int]:
    """增量补齐第二个演示用户；返回用户编号、对照行和本次新增订单数。"""
    user = db.query(User).filter(User.username == SHOWCASE_USERNAME).first()
    if user is None:
        user = User(
            username=SHOWCASE_USERNAME,
            phone="13900000002",
            email="demo_vip@example.com",
            user_level="vip",
            password_hash=hash_password(SHOWCASE_PASSWORD),
        )
        db.add(user)
        db.flush()

    rows: list[tuple] = []
    created_orders = 0
    for index, spec in enumerate(SHOWCASE_ORDERS, start=1):
        order = db.query(Order).filter(Order.order_no == spec["order_no"]).first()
        if order is None:
            status = spec["status"]
            paid_at = (
                now - timedelta(hours=spec["paid_hours_ago"])
                if spec.get("paid_hours_ago") is not None else None
            )
            shipped_at = (
                now - timedelta(hours=spec["shipped_hours_ago"])
                if spec.get("shipped_hours_ago") is not None else None
            )
            delivered_at = (
                now - timedelta(days=spec["delivered_days_ago"])
                if spec.get("delivered_days_ago") is not None else None
            )
            order = Order(
                user_id=user.id,
                order_no=spec["order_no"],
                status=status,
                total_amount=spec["amount"],
                product_type=spec.get("product", "normal"),
                paid_at=paid_at,
                shipped_at=shipped_at,
                delivered_at=delivered_at,
            )
            db.add(order)
            db.flush()
            for product_name, quantity, unit_price in spec["items"]:
                db.add(OrderItem(
                    order_id=order.id,
                    product_name=product_name,
                    quantity=quantity,
                    unit_price=unit_price,
                ))
            logistics = spec.get("logistics")
            if logistics:
                db.add(Logistics(
                    order_id=order.id,
                    carrier="顺丰",
                    tracking_no=f"VIP{now:%Y%m%d}{index:04d}",
                    status=logistics["status"],
                    last_location=logistics["location"],
                    last_update_time=now - timedelta(hours=logistics["hours_ago"]),
                    is_exception=logistics.get("is_exception", False),
                    exception_reason=logistics.get("reason"),
                ))
            created_orders += 1
        elif order.user_id != user.id:
            logger.warning("showcase order_no belongs to another user: %s", spec["order_no"])
            continue
        rows.append((order.id, spec["order_no"], spec["message"], spec["expected"]))
    return user.id, rows, created_orders


def _print_table(
    user_id: int,
    rows: list[tuple],
    *,
    username: str = "demo",
    note: str = "非本人场景订单属于 demo_other",
) -> None:
    print(f"\n===== 场景演示对照表 ({username} 用户 id={user_id}；{note}) =====")
    print("说明：网页端登录后从本人订单中选择，页面会自动关联订单；")
    print("      也可用 API/demo_local 测试（请求带 orderId）。\n")
    print(f"{'order_id':>8}  {'order_no':<22}  {'建议消息':<16}  预期场景")
    print("-" * 88)
    for oid, order_no, message, expected in rows:
        print(f"{oid:>8}  {order_no:<22}  {message:<16}  {expected}")
    print("\n  纯意图场景（无需 orderId，userId 用任意已存在用户）：")
    for message, expected in INTENT_ONLY:
        print(f"{'—':>8}  {'—':<22}  {message:<16}  {expected}")
    print(f"\n示例：POST /api/chat/message  {{\"userId\": {user_id}, "
          f"\"content\": \"我要退款\", \"orderId\": <上表 order_id>}}\n")


def seed() -> None:
    init_db()
    rng = random.Random(42)
    with SessionLocal() as db:
        if db.query(User).count() > 0:
            now = datetime.utcnow()
            showcase_id, showcase_rows, created = _seed_showcase_user(db, now)
            db.commit()
            logger.info(
                "existing database kept; showcase user ensured: %s, %d new orders",
                SHOWCASE_USERNAME,
                created,
            )
            _print_table(
                showcase_id,
                showcase_rows,
                username=SHOWCASE_USERNAME,
                note=f"密码 {SHOWCASE_PASSWORD}，本次新增 {created} 笔订单",
            )
            return

        users = [
            User(username=f"user{i:02d}", phone=f"1380000{i:04d}",
                 email=f"user{i:02d}@example.com",
                 user_level="vip" if i % 5 == 0 else "normal",
                 password_hash=hash_password("supportflow-user"))
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
            _add_items(db, o, rng)
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

        demo_id, rows = _seed_scenarios(db, now)
        showcase_id, showcase_rows, _ = _seed_showcase_user(db, now)
        db.add(User(
            username="admin", email="admin@example.com", role="admin",
            password_hash=hash_password("supportflow-admin")))

        db.add_all(KnowledgeDoc(title=t, category=c, content=body) for t, c, body in POLICIES)
        db.commit()
        logger.info(
            "seeded: %d users, %d random orders + %d boundary orders + %d showcase orders, %d policies",
            len(users) + 4,
            len(orders),
            len(SCENARIOS),
            len(SHOWCASE_ORDERS),
            len(POLICIES),
        )
        _print_table(demo_id, rows)
        _print_table(
            showcase_id,
            showcase_rows,
            username=SHOWCASE_USERNAME,
            note=f"密码 {SHOWCASE_PASSWORD}，完整业务演示数据",
        )


if __name__ == "__main__":
    setup_logging()
    seed()
