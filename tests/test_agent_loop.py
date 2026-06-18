"""M4·loop：harness 跑 ReAct loop 真执行工具 + 回灌；LLM 意图兜底。

用脚本化假 LLM（按序返回 tool_use → end_turn），验证工具被执行、结果回灌、记录落库、
token 跨轮累计、白名单外工具被拒。
"""
from app.agent.agent_core import AgentCore
from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.guardrails import Guardrails
from app.agent.intent_classifier import HybridIntentClassifier
from app.agent.skill_router import SkillRouter
from app.agent.state_machine import StateMachine, States
from app.db.models import AgentSession, AgentToolCall
from app.llm.base import LLMResponse, ToolCall, Usage
from app.tools.base import ToolContext
from app.tools.registry import build_tool_registry


class ScriptedLLM:
    model_name = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def chat(self, *, system, messages, tools=None, stream=False):
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return r


class _OrderSkill:
    name = "order"
    triggers = {"order_query"}

    def plan(self, ctx):
        return SkillPlan(system_prompt="查订单助手", allowed_tools=["get_order_detail"],
                         max_iterations=3)

    def finalize(self, ctx, tool_ctx=None):
        reply = ctx.history[-1].content if ctx.history else "已完成"
        return Decision(reply=reply, next_state=States.RESOLVED_BY_AGENT)


def _agent(llm):
    return AgentCore(
        llm=llm, classifier=HybridIntentClassifier(),
        router=SkillRouter(skills=[_OrderSkill()]),
        state_machine=StateMachine(), guardrails=Guardrails(),
        tool_registry=build_tool_registry(),
    )


def test_loop_executes_tool_and_feeds_back(db, user_order):
    u, o = user_order
    sess = AgentSession(user_id=u.id, current_state="tool_executing")
    db.add(sess)
    db.flush()

    llm = ScriptedLLM([
        # 第1轮：要求调用 get_order_detail
        LLMResponse(text="", tool_calls=[ToolCall("t1", "get_order_detail",
                    {"order_id": o.id, "user_id": u.id})],
                    stop_reason="tool_use", usage=Usage(5, 0, 0, 5)),
        # 第2轮：拿到结果后收尾
        LLMResponse(text="您的订单状态为 paid。", stop_reason="end_turn",
                    usage=Usage(8, 4, 0, 12)),
    ])
    agent = _agent(llm)
    ctx = AgentContext(session_id=sess.id, ticket_id=1, user_id=u.id,
                       message="查我的订单", order_id=o.id)
    decision = agent.handle(ctx, tool_ctx=ToolContext(db=db, session_id=sess.id))

    assert decision.reply == "您的订单状态为 paid。"
    assert ctx.state == States.RESOLVED_BY_AGENT
    assert len(ctx.tool_call_records) == 1
    assert ctx.tool_call_records[0].tool_name == "get_order_detail"
    assert ctx.tool_call_records[0].success is True
    assert ctx.token_acct.total_tokens == 17          # 跨两轮累计 5+12
    assert ctx.token_acct.model_name == "scripted"
    # 审计落库
    assert db.query(AgentToolCall).filter_by(session_id=sess.id).count() == 1


def test_loop_rejects_tool_not_in_whitelist(db, user_order):
    u, o = user_order
    sess = AgentSession(user_id=u.id)
    db.add(sess)
    db.flush()
    llm = ScriptedLLM([
        LLMResponse(text="", tool_calls=[ToolCall("t1", "create_refund_draft",
                    {"order_id": o.id, "user_id": u.id})],
                    stop_reason="tool_use", usage=Usage(5, 0, 0, 5)),
        LLMResponse(text="抱歉，我先帮您核实。", stop_reason="end_turn", usage=Usage(3, 2, 0, 5)),
    ])
    agent = _agent(llm)  # _OrderSkill 仅允许 get_order_detail
    ctx = AgentContext(session_id=sess.id, ticket_id=1, user_id=u.id,
                       message="查我的订单", order_id=o.id)
    agent.handle(ctx, tool_ctx=ToolContext(db=db, session_id=sess.id))

    rec = ctx.tool_call_records[0]
    assert rec.success is False
    assert rec.result["error"]["code"] == "tool_not_allowed"
    # 未执行 → 不应创建退款单
    from app.db.models import RefundRequest
    assert db.query(RefundRequest).count() == 0


def test_hybrid_intent_llm_fallback():
    class FakeLLM:
        model_name = "fake"

        def chat(self, *, system, messages, tools=None, stream=False):
            return LLMResponse(text="refund_request", stop_reason="end_turn", usage=Usage())

    clf = HybridIntentClassifier(llm=FakeLLM())
    # 规则未命中 → LLM 兜底
    intent, from_rule = clf.classify("我想申请处理一下那个事情")
    assert intent == "refund_request"
    assert from_rule is False
    # 规则命中 → 不走 LLM
    assert clf.classify("我要退款") == ("refund_request", True)
