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


def _convo(db, user_id, order_id=None):
    """多轮会话器：建真实 Ticket，跨轮维护 ticket.status（模拟 runner 持久化）。"""
    agent = build_default_agent()
    t = Ticket(user_id=user_id, category="chat", order_id=order_id)
    db.add(t)
    db.flush()

    def send(message):
        sess = AgentSession(user_id=user_id, ticket_id=t.id)
        db.add(sess)
        db.flush()
        start = t.status if t.status == States.WAITING_USER_CONFIRM else None
        ctx = AgentContext(session_id=sess.id, ticket_id=t.id, user_id=user_id,
                           message=message, order_id=t.order_id, state=start)
        decision = agent.handle(ctx, tool_ctx=ToolContext(db=db, session_id=sess.id))
        t.status = ctx.state          # 模拟 runner 末尾 ticket.status = ctx.state
        db.flush()
        return decision, ctx

    return send, t


# ---------- 退款 ----------
def test_refund_low_risk_two_step_confirm(db, user_order):
    """低风险退款两步：先待确认（不建草稿）→ 确认后才建草稿（Step2）。"""
    u, o = user_order
    send, t = _convo(db, u.id, order_id=o.id)
    _, c1 = send("我要退款，不想要了")
    assert c1.intent == "refund_request" and c1.state == States.WAITING_USER_CONFIRM
    assert db.query(RefundRequest).count() == 0               # 待确认，未建草稿
    assert t.pending_action and t.pending_action["type"] == "refund_request"
    d2, c2 = send("确认，帮我退吧")
    assert c2.state == States.RESOLVED_BY_AGENT
    rr = db.query(RefundRequest).one()
    assert rr.status == "draft" and rr.require_human_approval is False
    assert t.pending_action is None                           # 已清
    assert "退款" in d2.reply


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


def test_refund_no_duplicate_after_confirm(db, user_order):
    """确认建草稿后，再次请求同订单 → 会话级幂等，不重复建（#8）。"""
    u, o = user_order
    send, _ = _convo(db, u.id, order_id=o.id)
    send("我要退款")
    send("确认")                                    # 建草稿 #1
    assert db.query(RefundRequest).count() == 1
    d, _ = send("我还是要退款")                      # 再请求 → 已在处理中
    assert db.query(RefundRequest).count() == 1
    assert "处理中" in d.reply


def test_cancel_pending_refund(db, user_order):
    """待确认阶段说'算了不退了' → 取消待确认动作，未提交。"""
    u, o = user_order
    send, t = _convo(db, u.id, order_id=o.id)
    send("我要退款")                                 # → 待确认
    d, _ = send("算了不退了")
    assert db.query(RefundRequest).count() == 0
    assert t.pending_action is None
    assert "取消" in d.reply


def test_cancel_built_draft(db, user_order):
    """已建草稿后'取消退款' → 撤销草稿(cancelled)。"""
    u, o = user_order
    send, _ = _convo(db, u.id, order_id=o.id)
    send("我要退款")
    send("确认")
    d, _ = send("取消退款")
    rr = db.query(RefundRequest).one()
    assert rr.status == "cancelled"
    assert "撤销" in d.reply


def test_bare_confirm_without_context_clarifies(db, user_order):
    """无待确认上下文时'确认' → 低置信澄清，不误执行。"""
    u, o = user_order
    send, _ = _convo(db, u.id, order_id=o.id)
    _, c = send("确认")
    assert c.state == States.INFO_REQUIRED
    assert db.query(RefundRequest).count() == 0


def test_out_of_scope_hard_reply(db, user_order):
    """超范围(天气)→scope 硬闸固定拒答，不进业务技能、不建任何东西。"""
    u, _ = user_order
    d, c = _run(db, u.id, "今天天气怎么样")
    assert c.intent == "out_of_scope"
    assert c.state == States.RESOLVED_BY_AGENT
    assert "售后" in d.reply and "范围" in d.reply


def test_uncertain_in_confirm_does_not_execute(db, user_order):
    """待确认时回复'不确定' → 不建草稿、保持等待（截图实测 bug）。"""
    u, o = user_order
    send, _ = _convo(db, u.id, order_id=o.id)
    send("我要退款")                         # → 待确认
    d, c = send("不确定")
    assert db.query(RefundRequest).count() == 0
    assert c.state == States.WAITING_USER_CONFIRM   # 重新提示，仍等待
    assert "确认" in d.reply or "取消" in d.reply


def test_refund_inquiry_no_write(db, user_order):
    """'能退款吗' → 咨询，只读答疑不建草稿。"""
    u, o = user_order
    send, _ = _convo(db, u.id, order_id=o.id)
    _, c = send("能退款吗")
    assert c.intent == "refund_inquiry"
    assert db.query(RefundRequest).count() == 0


def test_refund_negation_does_not_create_draft(db, user_order):
    """'我不想退款了' → cancel_refund，不再误建草稿（结构化意图，治 #8 误判）。"""
    u, o = user_order
    _, ctx = _run(db, u.id, "我不想退款了", order_id=o.id)
    assert ctx.intent == "cancel_refund"
    assert db.query(RefundRequest).count() == 0


def test_refund_question_does_not_create_draft(db, user_order):
    """'能退款吗' → refund_inquiry（疑问≠请求），不建草稿。"""
    u, o = user_order
    _, ctx = _run(db, u.id, "能退款吗", order_id=o.id)
    assert ctx.intent == "refund_inquiry"
    assert db.query(RefundRequest).count() == 0


def test_refund_inflight_pending_human_not_duplicated(db, user_order):
    """高风险已建 pending_human 草稿后，再触发 → 提示审核中，不重复建。"""
    u, _ = user_order
    big = make_order(db, u.id, amount=999)
    _run(db, u.id, "这个太贵了我要退款", order_id=big.id)
    d2, ctx2 = _run(db, u.id, "我要退款", order_id=big.id)
    assert db.query(RefundRequest).count() == 1
    assert ctx2.state == States.NEED_HUMAN and "审核中" in d2.reply


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


def test_logistics_urge_ticket_dedup(db, user_order):
    """同一异常订单反复问 → 催件工单只建一张（会话级去重，#8 对称）。"""
    u, _ = user_order
    o = make_order(db, u.id, status="shipped")
    _add_logistics(db, o.id, "in_transit", hours_ago=60)
    _run(db, u.id, "我的快递怎么还没动", order_id=o.id)
    d2, _ = _run(db, u.id, "我的快递到哪了", order_id=o.id)   # 再问一次
    assert db.query(Ticket).filter_by(category="logistics", order_id=o.id).count() == 1
    assert "处理中" in d2.reply


def test_logistics_delivered_not_received_to_human(db, user_order):
    u, _ = user_order
    o = make_order(db, u.id, status="delivered", delivered_days_ago=1)
    _add_logistics(db, o.id, "delivered", hours_ago=20)
    decision, ctx = _run(db, u.id, "物流显示签收了但我没收到", order_id=o.id)
    assert ctx.intent == "logistics_exception"
    assert ctx.state == States.NEED_HUMAN
    assert decision.handoff_reason == "delivered_not_received"
