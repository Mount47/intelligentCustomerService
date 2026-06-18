"""M5 业务 Skill：RefundHandling + LogisticsException 端到端关键分支。

用 build_default_agent（stub LLM，loop 不调工具）+ 真 sqlite + ToolContext，
验证 finalize 的确定性业务决策、状态流转、写操作落库、guardrails。
"""
from datetime import datetime, timedelta

from app.agent.agent_core import build_default_agent
from app.agent.context import AgentContext
from app.agent.state_machine import States
from app.db.models import AgentSession, Logistics, RefundRequest, Ticket
from app.tools.base import ToolContext

from .conftest import make_order


def _run(db, user_id, message, order_id=None):
    agent = build_default_agent()
    sess = AgentSession(user_id=user_id)
    db.add(sess)
    db.flush()
    ctx = AgentContext(session_id=sess.id, ticket_id=1, user_id=user_id,
                       message=message, order_id=order_id)
    decision = agent.handle(ctx, tool_ctx=ToolContext(db=db, session_id=sess.id))
    return decision, ctx


# ---------- 退款 ----------
def test_refund_low_risk_auto_draft(db, user_order):
    u, o = user_order   # 普通 paid 订单，金额 100
    decision, ctx = _run(db, u.id, "我要退款，不想要了", order_id=o.id)
    assert ctx.intent == "refund_request" and ctx.skill == "refund_handling"
    assert ctx.state == States.RESOLVED_BY_AGENT
    rr = db.query(RefundRequest).one()
    assert rr.status == "draft" and rr.require_human_approval is False
    assert "退款" in decision.reply


def test_refund_high_amount_to_human(db, user_order):
    u, _ = user_order
    big = make_order(db, u.id, amount=999)
    decision, ctx = _run(db, u.id, "这个太贵了我要退款", order_id=big.id)
    assert ctx.state == States.NEED_HUMAN and decision.need_handoff is True
    rr = db.query(RefundRequest).one()
    assert rr.status == "pending_human" and rr.require_human_approval is True
    # 升级工单已建并转人工
    t = db.query(Ticket).filter_by(category="refund").one()
    assert t.status == States.NEED_HUMAN and t.priority == "high"


def test_refund_missing_order_id_asks_info(db, user_order):
    u, _ = user_order
    decision, ctx = _run(db, u.id, "我要退款")   # 无 order_id
    assert ctx.state == States.INFO_REQUIRED
    assert decision.required_info
    assert db.query(RefundRequest).count() == 0


def test_refund_not_owner_to_human(db, user_order):
    u, o = user_order
    decision, ctx = _run(db, u.id + 999, "我要退款", order_id=o.id)  # 非本人
    assert ctx.state == States.NEED_HUMAN
    assert decision.handoff_reason == "not_owner"
    assert db.query(RefundRequest).count() == 0


# ---------- 物流 ----------
def _add_logistics(db, order_id, status, hours_ago, is_exception=False, reason=None):
    db.add(Logistics(order_id=order_id, status=status, carrier="顺丰",
                     last_update_time=datetime.utcnow() - timedelta(hours=hours_ago),
                     last_location="上海转运中心", is_exception=is_exception,
                     exception_reason=reason))
    db.flush()


def test_logistics_normal_status(db, user_order):
    u, _ = user_order
    o = make_order(db, u.id, status="shipped")
    _add_logistics(db, o.id, "in_transit", hours_ago=2)
    decision, ctx = _run(db, u.id, "我的快递到哪了", order_id=o.id)
    assert ctx.skill == "logistics_exception"
    assert ctx.state == States.RESOLVED_BY_AGENT
    assert "in_transit" in decision.reply or "上海" in decision.reply


def test_logistics_stale_creates_urge_ticket(db, user_order):
    u, _ = user_order
    o = make_order(db, u.id, status="shipped")
    _add_logistics(db, o.id, "in_transit", hours_ago=60)   # 超 48h
    decision, ctx = _run(db, u.id, "我的快递怎么还没动", order_id=o.id)
    assert ctx.state == States.RESOLVED_BY_AGENT
    assert db.query(Ticket).filter_by(category="logistics").count() == 1
    assert "催件" in decision.reply


def test_logistics_delivered_not_received_to_human(db, user_order):
    u, _ = user_order
    o = make_order(db, u.id, status="delivered", delivered_days_ago=1)
    _add_logistics(db, o.id, "delivered", hours_ago=20)
    decision, ctx = _run(db, u.id, "物流显示签收了但我没收到", order_id=o.id)
    assert ctx.intent == "logistics_exception"
    assert ctx.state == States.NEED_HUMAN
    assert decision.handoff_reason == "delivered_not_received"
