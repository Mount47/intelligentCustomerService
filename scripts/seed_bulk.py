"""批量种子数据：50 用户 / 500 订单 / 物流 / 历史工单，供真实对话测试。

与 seed_data.py 互补：seed_data 造场景订单(可复现/评测)，本脚本造"量"(接近真实分布)。
幂等：检查 bulk 用户是否已存在，已存在则跳过。

用法：python -m scripts.seed_bulk
"""
from __future__ import annotations

import hashlib
import random
import uuid
from datetime import datetime, timedelta

from app.core.logging import get_logger, setup_logging
from app.db.init_db import init_db
from app.db.models import (
    AgentSession,
    AgentToolCall,
    KnowledgeDoc,
    Logistics,
    Order,
    OrderItem,
    RefundRequest,
    SlaRecord,
    Ticket,
    TicketMessage,
    User,
)
from app.db.session import SessionLocal

logger = get_logger(__name__)

# ── 数据池 ──────────────────────────────────────────────────────────────────

SURNAMES = "王李张刘陈杨黄赵周吴徐孙马胡朱郭何罗高林"
GIVEN_NAMES = [
    "伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋",
    "勇", "艳", "杰", "娟", "涛", "明", "超", "秀英", "华", "慧",
    "建国", "志强", "建华", "建平", "建军", "小红", "小明", "雪梅", "海燕", "玉兰",
]

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "重庆", "西安",
          "苏州", "天津", "长沙", "郑州", "东莞", "青岛", "合肥", "佛山", "宁波", "昆明"]

CARRIERS = ["顺丰速运", "中通快递", "圆通速递", "韵达快递", "京东物流", "极兔速递", "申通快递", "EMS"]

LOCATIONS = [
    "北京转运中心", "上海浦东分拨中心", "广州白云转运站", "深圳龙华营业部",
    "杭州萧山分拨中心", "成都双流转运中心", "武汉光谷营业部", "南京栖霞分拨站",
    "派送中-已到达目的地城市", "快件正在派送中，请注意查收", "正在揽收中",
    "已到达中转站", "正在运输中", "海关清关中",
]

PRODUCT_CATALOG = {
    "normal": [
        ("蓝牙耳机 Pro", 159.0), ("机械键盘 K2", 349.0), ("运动鞋 Air", 499.0),
        ("保温杯 500ml", 89.0), ("双肩包商务款", 239.0), ("充电宝 20000mAh", 129.0),
        ("无线鼠标", 79.0), ("手机壳硅胶款", 29.0), ("数据线三合一", 39.0),
        ("台灯护眼LED", 199.0), ("加湿器迷你", 69.0), ("收纳箱折叠", 45.0),
        ("雨伞自动折叠", 59.0), ("毛巾浴巾套装", 78.0), ("保鲜盒套装", 55.0),
        ("拖鞋居家款", 35.0), ("抱枕靠垫", 49.0), ("水杯玻璃款", 42.0),
        ("帆布袋单肩", 25.0), ("笔记本 A5", 18.0),
    ],
    "fresh_food": [
        ("阳光玫瑰葡萄 2斤", 68.0), ("智利车厘子 JJ级", 128.0),
        ("现切牛排 西冷200g*3", 159.0), ("海南芒果 5斤", 49.0),
        ("大闸蟹礼盒 8只", 298.0), ("三文鱼刺身 200g", 89.0),
        ("有机蔬菜套餐", 59.0), ("鲜虾仁 500g", 79.0),
        ("蓝莓 4盒装", 69.0), ("牛油果 6个", 45.0),
    ],
    "customized_product": [
        ("定制刻字钢笔", 188.0), ("定制相册 精装", 99.0),
        ("定制T恤 纯棉", 79.0), ("定制马克杯", 49.0),
        ("定制手机壳 照片款", 59.0), ("定制抱枕", 89.0),
        ("定制台历 2026", 39.0), ("定制帆布袋", 45.0),
    ],
}

REFUND_REASONS = [
    "不想要了", "买错了", "质量问题", "与描述不符", "尺寸不合适",
    "发错货了", "包装破损", "物流太慢不想等了", "找到更便宜的了", "重复下单",
]

TICKET_CATEGORIES = ["refund", "logistics", "order_query", "complaint", "general"]
TICKET_STATUSES = ["created", "in_progress", "waiting_user_confirm", "resolved", "need_human"]

USER_MESSAGES_REFUND = [
    "我想退款", "这个商品我不想要了，能退吗", "质量有问题，我要退货退款",
    "发错颜色了，我要退", "跟图片差太多了要退款", "尺码不对，申请退换",
]
USER_MESSAGES_LOGISTICS = [
    "我的快递到哪了", "快递怎么还没到", "物流信息好几天没更新了",
    "显示签收了但我没收到", "快递一直在转运中心不动",
]
USER_MESSAGES_QUERY = [
    "我的订单状态是什么", "帮我查一下这个订单", "我买了什么来着",
    "订单什么时候发货", "我的订单多少钱",
]
USER_MESSAGES_GENERAL = [
    "你们退货政策是什么", "多久可以退款", "生鲜能退吗",
    "怎么联系人工客服", "退款多久到账",
]

AGENT_REPLIES_REFUND = [
    "已为您创建退款申请（草稿），退款金额 {amount} 元。审核通过后将原路退回。",
    "您的退款申请已提交，由于金额较高需人工审核，预计1-3个工作日内处理。",
    "该商品属于生鲜类目，需人工客服审核后处理。已为您转接人工。",
]
AGENT_REPLIES_LOGISTICS = [
    "您的包裹目前在{location}，由{carrier}承运，单号{tracking_no}。",
    "物流信息已超过48小时未更新，已为您创建催件工单。",
    "已为您查询，快递目前正常运输中，预计明天送达。",
]


def _gen_phone(rng: random.Random) -> str:
    prefixes = ["138", "139", "150", "151", "152", "186", "187", "188", "135", "136"]
    return rng.choice(prefixes) + "".join(str(rng.randint(0, 9)) for _ in range(8))


def _gen_order_no(rng: random.Random, seq: int, date: datetime) -> str:
    return f"ORD{date:%Y%m%d}{seq:05d}"


def _business_key(user_id: int, order_id: int, reason: str) -> str:
    raw = f"{user_id}|{order_id}|refund_request|{reason[:20]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def seed_bulk() -> None:
    init_db()
    rng = random.Random(2026)
    now = datetime.utcnow()

    with SessionLocal() as db:
        if db.query(User).filter(User.username == "bulk_user001").first():
            logger.info("bulk seed skipped: bulk users already exist")
            return

        # ── 1. 用户 (50) ──────────────────────────────────────────────
        users: list[User] = []
        for i in range(1, 51):
            surname = rng.choice(SURNAMES)
            given = rng.choice(GIVEN_NAMES)
            level = rng.choices(["normal", "vip"], weights=[0.8, 0.2])[0]
            u = User(
                username=f"bulk_user{i:03d}",
                phone=_gen_phone(rng),
                email=f"user{i}_{surname}@example.com",
                user_level=level,
            )
            users.append(u)
        db.add_all(users)
        db.flush()
        logger.info("bulk: created %d users", len(users))

        # ── 2. 订单 (500) ─────────────────────────────────────────────
        status_weights = {
            "pending_payment": 0.05,
            "paid": 0.15,
            "shipped": 0.25,
            "delivered": 0.45,
            "cancelled": 0.10,
        }
        statuses = list(status_weights.keys())
        weights = list(status_weights.values())

        ptype_weights = {"normal": 0.7, "fresh_food": 0.15, "customized_product": 0.15}
        ptypes = list(ptype_weights.keys())
        pweights = list(ptype_weights.values())

        orders: list[Order] = []
        for seq in range(1, 501):
            u = rng.choice(users)
            status = rng.choices(statuses, weights=weights)[0]
            ptype = rng.choices(ptypes, weights=pweights)[0]

            days_ago = rng.randint(1, 60)
            created = now - timedelta(days=days_ago)
            paid = shipped = delivered = None

            if status in ("paid", "shipped", "delivered"):
                paid = created + timedelta(minutes=rng.randint(5, 120))
            if status in ("shipped", "delivered"):
                shipped = paid + timedelta(hours=rng.randint(4, 48))
            if status == "delivered":
                delivered = shipped + timedelta(days=rng.randint(1, 7))

            catalog = PRODUCT_CATALOG[ptype]
            num_items = rng.randint(1, 3)
            picks = rng.sample(catalog, min(num_items, len(catalog)))
            total = sum(price * rng.randint(1, 2) for _, price in picks)

            o = Order(
                user_id=u.id,
                order_no=_gen_order_no(rng, seq, created),
                status=status,
                total_amount=round(total, 2),
                product_type=ptype,
                paid_at=paid,
                shipped_at=shipped,
                delivered_at=delivered,
            )
            orders.append(o)

        db.add_all(orders)
        db.flush()

        # 商品明细
        for o in orders:
            catalog = PRODUCT_CATALOG[o.product_type]
            num_items = rng.randint(1, 3)
            picks = rng.sample(catalog, min(num_items, len(catalog)))
            for name, price in picks:
                qty = rng.randint(1, 2)
                db.add(OrderItem(
                    order_id=o.id,
                    product_name=name,
                    quantity=qty,
                    unit_price=price,
                ))

        logger.info("bulk: created %d orders with items", len(orders))

        # ── 3. 物流 ──────────────────────────────────────────────────
        logi_count = 0
        for o in orders:
            if o.status not in ("shipped", "delivered"):
                continue
            logi_status = "delivered" if o.status == "delivered" else rng.choices(
                ["in_transit", "in_transit", "in_transit", "exception"],
                weights=[0.5, 0.25, 0.15, 0.1],
            )[0]
            is_exc = logi_status == "exception"
            hours_since = rng.choice([2, 6, 12, 24, 36, 50, 72, 96]) if not is_exc else rng.randint(48, 120)
            last_update = (o.shipped_at or now) + timedelta(hours=rng.randint(1, max(2, hours_since)))

            db.add(Logistics(
                order_id=o.id,
                carrier=rng.choice(CARRIERS),
                tracking_no=f"SF{rng.randint(10**11, 10**12)}",
                status=logi_status,
                last_location=rng.choice(LOCATIONS),
                last_update_time=last_update,
                is_exception=is_exc,
                exception_reason=rng.choice(["中转停滞", "派送异常", "地址不详", "收件人拒收"]) if is_exc else None,
            ))
            logi_count += 1
        logger.info("bulk: created %d logistics records", logi_count)

        # ── 4. 历史工单 + 消息 (200 已关闭工单，模拟历史) ─────────────
        ticket_count = 0
        for _ in range(200):
            u = rng.choice(users)
            user_orders = [o for o in orders if o.user_id == u.id]
            category = rng.choices(
                TICKET_CATEGORIES, weights=[0.35, 0.25, 0.2, 0.1, 0.1]
            )[0]
            status = rng.choices(
                ["resolved", "resolved", "resolved", "need_human"],
                weights=[0.6, 0.2, 0.1, 0.1],
            )[0]
            days_ago = rng.randint(1, 45)
            created_at = now - timedelta(days=days_ago, hours=rng.randint(0, 23))

            order_id = None
            if user_orders and category in ("refund", "logistics", "order_query"):
                order_id = rng.choice(user_orders).id

            priority = "high" if category == "complaint" else rng.choices(
                ["normal", "normal", "high"], weights=[0.6, 0.3, 0.1]
            )[0]

            t = Ticket(
                user_id=u.id,
                order_id=order_id,
                category=category,
                priority=priority,
                status=status,
                sla_deadline=created_at + timedelta(hours=24 if priority == "normal" else 4),
                created_by_agent=True,
                created_at=created_at,
                updated_at=created_at + timedelta(minutes=rng.randint(1, 120)),
            )
            db.add(t)
            db.flush()

            # 每个工单 2~5 条消息
            msg_time = created_at
            num_msgs = rng.randint(2, 5)
            for m_idx in range(num_msgs):
                msg_time += timedelta(seconds=rng.randint(10, 300))
                if m_idx % 2 == 0:
                    if category == "refund":
                        content = rng.choice(USER_MESSAGES_REFUND)
                    elif category == "logistics":
                        content = rng.choice(USER_MESSAGES_LOGISTICS)
                    elif category == "order_query":
                        content = rng.choice(USER_MESSAGES_QUERY)
                    else:
                        content = rng.choice(USER_MESSAGES_GENERAL)
                    sender_type = "user"
                else:
                    if category == "refund":
                        content = rng.choice(AGENT_REPLIES_REFUND).format(
                            amount=rng.randint(30, 500), location="", carrier="", tracking_no=""
                        )
                    elif category == "logistics":
                        content = rng.choice(AGENT_REPLIES_LOGISTICS).format(
                            location=rng.choice(LOCATIONS),
                            carrier=rng.choice(CARRIERS),
                            tracking_no=f"SF{rng.randint(10**11, 10**12)}",
                        )
                    else:
                        content = "好的，已为您查询相关信息。"
                    sender_type = "agent"

                db.add(TicketMessage(
                    ticket_id=t.id,
                    user_id=u.id if sender_type == "user" else None,
                    sender_type=sender_type,
                    content=content,
                    client_message_id=str(uuid.uuid4()) if sender_type == "user" else None,
                ))

            # SLA 记录
            resolved_at = created_at + timedelta(minutes=rng.randint(5, 180)) if status == "resolved" else None
            db.add(SlaRecord(
                ticket_id=t.id,
                sla_type="resolution",
                deadline=t.sla_deadline,
                is_timeout=(resolved_at and resolved_at > t.sla_deadline) if resolved_at else (now > t.sla_deadline),
                resolved_at=resolved_at,
            ))

            ticket_count += 1

        logger.info("bulk: created %d historical tickets with messages", ticket_count)

        # ── 5. 历史 Agent 会话 (匹配工单，给运维端数据) ────────────────
        session_count = 0
        for t in db.query(Ticket).filter(Ticket.created_by_agent.is_(True)).limit(150):
            s = AgentSession(
                user_id=t.user_id,
                ticket_id=t.id,
                current_intent=t.category + "_request" if t.category == "refund" else t.category,
                current_skill=t.category,
                current_state=t.status,
                final_status=t.status,
                task_status="done",
                model_name="qwen-plus",
                prompt_tokens=rng.randint(800, 3000),
                completion_tokens=rng.randint(200, 800),
                total_tokens=rng.randint(1000, 3800),
                estimated_cost=round(rng.uniform(0.002, 0.02), 6),
                cache_hit=rng.random() < 0.3,
                total_latency_ms=rng.randint(800, 5000),
                created_at=t.created_at,
                started_at=t.created_at + timedelta(seconds=rng.randint(1, 5)),
                finished_at=t.created_at + timedelta(seconds=rng.randint(3, 15)),
            )
            db.add(s)
            db.flush()

            # 1~3 个工具调用
            tools_used = []
            if t.category == "refund":
                tools_used = ["get_order_detail", "check_refund_policy", "create_refund_draft"]
            elif t.category == "logistics":
                tools_used = ["get_order_detail", "get_logistics_info"]
            elif t.category == "order_query":
                tools_used = ["get_order_detail", "get_order_items"]
            else:
                tools_used = ["search_knowledge"]

            for tool_name in tools_used[:rng.randint(1, len(tools_used))]:
                db.add(AgentToolCall(
                    session_id=s.id,
                    tool_name=tool_name,
                    input_json={"order_id": t.order_id} if t.order_id else {},
                    output_json={"status": "ok"},
                    success=rng.random() < 0.95,
                    latency_ms=rng.randint(50, 500),
                ))

            session_count += 1

        logger.info("bulk: created %d agent sessions with tool calls", session_count)

        # ── 6. 少量退款记录 (已完成的历史退款) ────────────────────────
        refund_count = 0
        delivered_orders = [o for o in orders if o.status == "delivered" and o.product_type == "normal"]
        for o in rng.sample(delivered_orders, min(40, len(delivered_orders))):
            reason = rng.choice(REFUND_REASONS)
            idem_key = str(uuid.uuid4())
            biz_key = _business_key(o.user_id, o.id, reason)
            req_hash = hashlib.sha256(f"{o.user_id}|{o.id}|{reason}|{o.total_amount}".encode()).hexdigest()[:16]
            status = rng.choices(
                ["draft", "pending_human", "approved", "rejected", "refunded"],
                weights=[0.1, 0.1, 0.15, 0.05, 0.6],
            )[0]
            high = float(o.total_amount) > 500
            db.add(RefundRequest(
                order_id=o.id,
                user_id=o.user_id,
                refund_reason=reason,
                status=status,
                amount=float(o.total_amount),
                risk_level="high" if high else "low",
                require_human_approval=high,
                idempotency_key=idem_key,
                business_key=biz_key,
                request_hash=req_hash,
            ))
            refund_count += 1

        logger.info("bulk: created %d refund records", refund_count)

        db.commit()
        logger.info(
            "=== bulk seed complete: %d users, %d orders, %d logistics, "
            "%d tickets, %d sessions, %d refunds ===",
            len(users), len(orders), logi_count, ticket_count, session_count, refund_count,
        )

        # 打印可用于对话测试的用户
        print("\n===== 批量数据已就绪 =====")
        print(f"用户数: {len(users)}  |  订单数: {len(orders)}  |  历史工单: {ticket_count}")
        print(f"\n可用于对话测试的用户 (前10):")
        print(f"{'user_id':>8}  {'username':<16}  {'level':<8}  订单数")
        print("-" * 52)
        for u in users[:10]:
            cnt = sum(1 for o in orders if o.user_id == u.id)
            print(f"{u.id:>8}  {u.username:<16}  {u.user_level:<8}  {cnt}")
        print(f"\n用法: POST /api/chat/message {{\"userId\": <user_id>, \"content\": \"我要退款\", \"orderId\": <order_id>}}")
        print("或不带 orderId，Agent 会主动列出该用户的订单供选择。\n")


if __name__ == "__main__":
    setup_logging()
    seed_bulk()
