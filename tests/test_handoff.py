"""TODO-1 转人工会话摘要：事实来自 DB(准)、叙述 LLM/模板、分档、失败降级。"""
from datetime import datetime

from app.db.models import Order, RefundRequest, Ticket, TicketMessage, User
from app.llm.base import LLMResponse, Usage
from app.services import handoff_service


def _seed(db, n_msgs=5, with_refund=False):
    u = User(username="h")
    db.add(u)
    db.flush()
    o = Order(user_id=u.id, order_no="HO-1", status="paid", total_amount=120,
              product_type="normal", paid_at=datetime.utcnow())
    db.add(o)
    db.flush()
    t = Ticket(user_id=u.id, category="chat", order_id=o.id)
    db.add(t)
    db.flush()
    for i in range(n_msgs):
        db.add(TicketMessage(ticket_id=t.id, user_id=(u.id if i % 2 == 0 else None),
                             sender_type=("user" if i % 2 == 0 else "agent"),
                             content=(f"我要退款 第{i}条" if i == 0 else f"消息{i}")))
    if with_refund:
        db.add(RefundRequest(order_id=o.id, user_id=u.id, refund_reason="x", amount=120,
                             risk_level="high", require_human_approval=True,
                             idempotency_key="k", business_key="b", request_hash="h",
                             status="pending_human"))
    db.flush()
    return u, o, t


def test_facts_come_from_db(db):
    u, o, t = _seed(db, n_msgs=6, with_refund=True)
    s = handoff_service.summarize_for_human(db, t.id)
    assert "HO-1" in s["order_info"] and "120" in s["order_info"]      # 订单事实来自 DB
    assert s["refund_info"] and "pending_human" in s["refund_info"]    # 退款事实来自 DB
    assert any("退款申请" in a for a in s["agent_actions"])
    assert s["handoff_reason"] == "高风险退款，需人工审核"             # pending_human → 高风险
    assert s["user_need"].startswith("我要退款")                       # 首条用户消息=诉求
    assert s["message_count"] == 6


def test_short_conversation_not_summarized(db):
    _, _, t = _seed(db, n_msgs=2)
    s = handoff_service.summarize_for_human(db, t.id)
    assert "简短" in s["conversation_brief"] and s["generated_by"] == "template"


def test_llm_narrative_used_when_provided(db):
    _, _, t = _seed(db, n_msgs=8)

    class FakeLLM:
        model_name = "qwen-test"
        def chat(self, *, system, messages, tools=None, stream=False):
            return LLMResponse(text="用户多轮询问退款，已建草稿，待人工核实。",
                               stop_reason="end_turn", usage=Usage())

    s = handoff_service.summarize_for_human(db, t.id, llm=FakeLLM())
    assert s["conversation_brief"].startswith("用户多轮")
    assert s["generated_by"] == "llm:qwen-test"


def test_llm_failure_falls_back_to_template(db):
    _, _, t = _seed(db, n_msgs=8)

    class BadLLM:
        model_name = "bad"
        def chat(self, **kw):
            raise RuntimeError("boom")

    s = handoff_service.summarize_for_human(db, t.id, llm=BadLLM())   # 不抛
    assert s["generated_by"] == "template"
    assert str(s["message_count"]) in s["conversation_brief"]


def test_missing_ticket_returns_none(db):
    assert handoff_service.summarize_for_human(db, 99999) is None
