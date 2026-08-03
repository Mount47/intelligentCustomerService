"""第一阶段多意图：同一 Agent 顺序执行多个 Skill，写操作仍受确认门保护。"""
from datetime import datetime, timedelta

from app.agent.agent_core import build_default_agent
from app.agent.context import AgentContext
from app.agent.intent_classifier import HybridIntentClassifier, Intents
from app.agent.state_machine import States
from app.agent.task_planner import TaskPlanner
from app.db.models import AgentSession, Logistics, OrderItem, RefundRequest, Ticket
from app.llm.base import LLMResponse, ToolCall, Usage
from app.tools.base import ToolContext

from .conftest import make_order


def _logistics(db, order_id, *, status="in_transit", location="苏州分拨中心"):
    db.add(Logistics(
        order_id=order_id,
        status=status,
        carrier="顺丰",
        tracking_no=f"MULTI-{order_id}",
        last_location=location,
        last_update_time=datetime.utcnow() - timedelta(hours=2),
        is_exception=False,
    ))
    db.flush()


def _run(db, user_id, order_id, message, *, llm=None, ticket=None, state=None):
    if ticket is None:
        ticket = Ticket(user_id=user_id, order_id=order_id, category="chat")
        db.add(ticket)
        db.flush()
    session = AgentSession(user_id=user_id, ticket_id=ticket.id)
    db.add(session)
    db.flush()
    ctx = AgentContext(
        session_id=session.id,
        ticket_id=ticket.id,
        user_id=user_id,
        order_id=order_id,
        message=message,
        state=state,
    )
    decision = build_default_agent(llm).handle(
        ctx, ToolContext(db=db, session_id=session.id)
    )
    ticket.status = ctx.state
    db.flush()
    return decision, ctx, ticket


def test_multi_intent_collection_accuracy_and_task_order():
    classifier = HybridIntentClassifier()
    cases = {
        "查一下订单状态，再看看物流到哪了": {
            Intents.ORDER_QUERY, Intents.LOGISTICS_QUERY,
        },
        "查一下物流，另外这单能退款吗？": {
            Intents.LOGISTICS_QUERY, Intents.REFUND_INQUIRY,
        },
        "查一下物流，我还要申请退款": {
            Intents.LOGISTICS_QUERY, Intents.REFUND_REQUEST,
        },
    }
    planner = TaskPlanner()
    for message, expected in cases.items():
        result = classifier.classify_multi_intent(message)
        assert {item.intent for item in result.intents} == expected
        plan = planner.plan(result, has_order=True)
        assert not plan.requires_clarification
        write_indexes = [i for i, task in enumerate(plan.tasks) if not task.read_only]
        if write_indexes:
            assert write_indexes == [len(plan.tasks) - 1]


def test_order_query_plus_logistics_query_merges_sections(db, user_order):
    user, order = user_order
    db.add(OrderItem(order_id=order.id, product_name="机械键盘", quantity=1, unit_price=100))
    order.status = "shipped"
    _logistics(db, order.id)

    decision, ctx, _ = _run(
        db, user.id, order.id, "查一下订单状态，再看看物流到哪了"
    )

    assert ctx.multi_intent_mode is True
    assert [outcome.intent for outcome in ctx.task_outcomes] == [
        Intents.LOGISTICS_QUERY, Intents.ORDER_QUERY,
    ]
    assert decision.next_state == States.RESOLVED_BY_AGENT
    assert "【订单信息】" in decision.reply and "【物流信息】" in decision.reply
    assert order.order_no in decision.reply and "苏州分拨中心" in decision.reply


def test_logistics_plus_refund_inquiry_is_read_only(db, user_order):
    user, order = user_order
    order.status = "shipped"
    _logistics(db, order.id)

    decision, ctx, ticket = _run(
        db, user.id, order.id, "查一下物流，另外这单能退款吗？"
    )

    assert {outcome.intent for outcome in ctx.task_outcomes} == {
        Intents.LOGISTICS_QUERY, Intents.REFUND_INQUIRY,
    }
    assert decision.next_state == States.RESOLVED_BY_AGENT
    assert "【物流信息】" in decision.reply and "【退款说明】" in decision.reply
    assert ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0


def test_logistics_plus_refund_request_reads_then_only_creates_pending(db, user_order):
    user, order = user_order
    order.status = "shipped"
    _logistics(db, order.id)

    decision, ctx, ticket = _run(
        db, user.id, order.id, "查一下物流，我还要申请退款"
    )

    assert [outcome.intent for outcome in ctx.task_outcomes] == [
        Intents.LOGISTICS_QUERY, Intents.REFUND_REQUEST,
    ]
    assert decision.next_state == States.WAITING_USER_CONFIRM
    assert "【物流信息】" in decision.reply and "【退款申请】" in decision.reply
    assert ticket.pending_action["type"] == "refund_request"
    assert db.query(RefundRequest).count() == 0


def test_logistics_plus_return_request_only_creates_pending(db, user_order):
    user, order = user_order
    order.status = "delivered"
    order.delivered_at = datetime.utcnow() - timedelta(days=2)
    _logistics(db, order.id, status="delivered", location="本人签收")

    decision, ctx, ticket = _run(
        db, user.id, order.id, "查一下物流，我还要申请退货"
    )

    assert [outcome.intent for outcome in ctx.task_outcomes] == [
        Intents.LOGISTICS_QUERY, Intents.RETURN_REQUEST,
    ]
    assert decision.next_state == States.WAITING_USER_CONFIRM
    assert ticket.pending_action["type"] == "return_request"
    assert db.query(RefundRequest).count() == 0


def test_negated_refund_only_queries_logistics(db, user_order):
    user, order = user_order
    order.status = "shipped"
    _logistics(db, order.id)

    decision, ctx, ticket = _run(
        db, user.id, order.id, "别退款，只查物流"
    )

    assert ctx.intent == Intents.LOGISTICS_QUERY.value
    assert decision.next_state == States.RESOLVED_BY_AGENT
    assert ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0


def test_complaint_plus_refund_prioritizes_human_without_refund(db, user_order):
    user, order = user_order

    decision, ctx, ticket = _run(
        db, user.id, order.id, "商品质量太差，我要投诉并退款"
    )

    assert decision.next_state == States.NEED_HUMAN
    assert len(ctx.task_outcomes) == 1
    assert ctx.task_outcomes[0].intent == Intents.PRODUCT_COMPLAINT
    assert ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0


def test_two_write_intents_require_clarification_without_side_effect(db, user_order):
    user, order = user_order

    decision, ctx, ticket = _run(
        db, user.id, order.id, "我要退款，同时还要申请退货"
    )

    assert decision.next_state == States.INFO_REQUIRED
    assert "只选择一个" in decision.reply
    assert ctx.task_outcomes == []
    assert ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0


def test_confirmation_with_new_request_stays_waiting(db, user_order):
    user, order = user_order
    order.status = "shipped"
    _logistics(db, order.id)
    first, _, ticket = _run(db, user.id, order.id, "我要退款")
    assert first.next_state == States.WAITING_USER_CONFIRM

    second, _, ticket = _run(
        db, user.id, order.id, "确认，顺便查一下物流",
        ticket=ticket,
        state=States.WAITING_USER_CONFIRM,
    )

    assert second.next_state == States.WAITING_USER_CONFIRM
    assert "不能同时夹带新诉求" in second.reply
    assert ticket.pending_action is not None
    assert db.query(RefundRequest).count() == 0


def test_multi_intent_high_risk_and_not_owner_never_write_refund(db, user_order):
    user, _ = user_order
    high = make_order(db, user.id, amount=999, status="shipped")
    _logistics(db, high.id)

    high_decision, _, high_ticket = _run(
        db, user.id, high.id, "查物流，然后申请退款"
    )
    assert high_decision.next_state == States.NEED_HUMAN
    assert high_ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0

    other = make_order(db, user.id + 999, amount=88, status="shipped")
    _logistics(db, other.id)
    denied, denied_ctx, denied_ticket = _run(
        db, user.id, other.id, "查订单和物流，然后申请退款"
    )
    assert denied.next_state == States.NEED_HUMAN
    assert len(denied_ctx.task_outcomes) == 1
    assert denied_ticket.pending_action is None
    assert db.query(RefundRequest).count() == 0


class _ReadToolLLM:
    model_name = "multi-tool-test"

    def __init__(self):
        self.called: set[str] = set()

    def chat(self, *, system, messages, tools=None, stream=False):
        names = {tool.name for tool in (tools or [])}
        preferred = (
            "get_order_detail" if "get_order_detail" in names
            else "get_logistics_status" if "get_logistics_status" in names
            else None
        )
        if preferred and preferred not in self.called:
            self.called.add(preferred)
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(f"call-{preferred}", preferred, {})],
                stop_reason="tool_use",
                usage=Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
            )
        return LLMResponse(text="已读取", usage=Usage(prompt_tokens=3, completion_tokens=1, total_tokens=4))


def test_multi_read_tasks_call_each_skill_tools(db, user_order):
    user, order = user_order
    order.status = "shipped"
    _logistics(db, order.id)
    llm = _ReadToolLLM()

    decision, ctx, _ = _run(
        db, user.id, order.id, "查订单状态和物流到哪", llm=llm
    )

    assert decision.next_state == States.RESOLVED_BY_AGENT
    assert [record.tool_name for record in ctx.tool_call_records] == [
        "get_logistics_status", "get_order_detail",
    ]
    assert all(record.success for record in ctx.tool_call_records)


def test_cross_order_multi_intent_is_rejected_by_planner():
    result = HybridIntentClassifier().classify_multi_intent(
        "查询订单 SO-A001 的物流，再给订单 SO-B002 退款"
    )
    assert result.requires_clarification is True
    assert result.order_references == ("SO-A001", "SO-B002")
