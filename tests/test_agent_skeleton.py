"""M1.5 走通骨架：证明 Agent 脊椎端到端（无真实 DB/工具/LLM）。

覆盖：意图(规则) → 路由 → plan → harness loop(stub LLM) → finalize → guardrails → 状态机转移。
"""
import pytest

from app.agent.agent_core import build_default_agent
from app.agent.context import AgentContext
from app.agent.intent_classifier import Intents, RuleIntentClassifier
from app.agent.state_machine import States, StateMachine
from app.core.exceptions import InvalidStateTransition


def _ctx(message: str) -> AgentContext:
    return AgentContext(session_id=1, ticket_id=1, user_id=1, message=message)


def test_pipeline_end_to_end():
    agent = build_default_agent()
    ctx = _ctx("你们的售后政策是怎样的？")
    decision = agent.handle(ctx)

    assert decision.reply                       # 有回复
    assert ctx.intent is not None               # 意图已识别
    assert ctx.skill == "general"               # 路由到兜底 Skill
    assert ctx.state == decision.next_state     # 状态已落到决策态
    assert ctx.state in (States.RESOLVED_BY_AGENT, States.INFO_REQUIRED, States.NEED_HUMAN)
    assert ctx.token_acct.total_tokens > 0      # token 记账链路通（stub usage 流入）


def test_intent_rule_fastpath():
    clf = RuleIntentClassifier()
    assert clf.classify("我要退款")[0] == Intents.REFUND_REQUEST
    assert clf.classify("我要退款")[1] is True          # 来自规则
    assert clf.classify("快递到哪了")[0] == Intents.LOGISTICS_QUERY
    assert clf.classify("转人工")[0] == Intents.HUMAN_HANDOFF
    assert clf.classify("天气怎么样")[1] is False        # 未命中规则，走兜底


def test_state_machine_legal_and_illegal():
    sm = StateMachine()
    assert sm.transition(States.CREATED, States.INTENT_DETECTED) == States.INTENT_DETECTED
    with pytest.raises(InvalidStateTransition):
        sm.transition(States.CREATED, States.CLOSED)        # 非法直跳
    with pytest.raises(InvalidStateTransition):
        sm.transition(States.CLOSED, States.INTENT_DETECTED)  # 终态不可出


def test_guardrails_blocks_forbidden_promise():
    from app.agent.guardrails import Guardrails
    g = Guardrails()
    assert g.scan_forbidden("我们一定退款给您")        # 命中禁语
    safe = g.postcheck("我们一定退款给您")
    assert "一定退款" not in safe                       # 被降级为安全话术
