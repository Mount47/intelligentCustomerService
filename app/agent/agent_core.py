"""AgentCore harness —— 统一执行管道（§5.4）。

ReAct loop 归这里统一跑（不散落各 Skill）；guardrails / 状态机闸门 / token 记账集中一处。
M1.5 骨架：无工具、stub LLM 一轮 end_turn；M3 接 ToolRegistry，M4/M5 接真实 LLM 与业务 Skill。
"""
from __future__ import annotations

from app.agent.context import AgentContext, Decision
from app.agent.guardrails import Guardrails
from app.agent.intent_classifier import RuleIntentClassifier
from app.agent.skill_router import SkillRouter
from app.agent.state_machine import StateMachine, States
from app.core.logging import get_logger
from app.llm.base import LLMClient, Msg, ToolSpec
from app.llm.stub import StubLLMClient

logger = get_logger(__name__)


class AgentCore:
    def __init__(
        self,
        llm: LLMClient,
        classifier: RuleIntentClassifier,
        router: SkillRouter,
        state_machine: StateMachine,
        guardrails: Guardrails,
        tool_registry=None,   # M3 接入；骨架为 None
    ) -> None:
        self.llm = llm
        self.classifier = classifier
        self.router = router
        self.sm = state_machine
        self.guardrails = guardrails
        self.tools = tool_registry

    def handle(self, ctx: AgentContext) -> Decision:
        # 0 初始状态
        if ctx.state is None:
            ctx.state = States.CREATED

        # 4 意图识别（规则快路 + LLM 兜底）
        intent, _from_rule = self.classifier.classify(ctx.message)
        ctx.intent = intent
        ctx.state = self.sm.transition(ctx.state, States.INTENT_DETECTED, ticket_id=ctx.ticket_id)

        # 5 路由 Skill；6 取 plan
        skill = self.router.route(intent)
        ctx.skill = skill.name
        plan = skill.plan(ctx)

        # 6' 缺信息 → 短路 loop，索取信息
        if plan.required_info:
            decision = Decision(
                reply="为继续处理，请补充：" + "、".join(plan.required_info),
                next_state=States.INFO_REQUIRED,
                required_info=plan.required_info,
            )
        else:
            # 7 harness 统一跑 ReAct loop
            self._run_loop(ctx, plan)
            # 8 Skill 据结果产出决策
            decision = skill.finalize(ctx)

        # 9 guardrails 回复后校验（绝不绕过）
        decision.reply = self.guardrails.postcheck(decision.reply)

        # 10 状态机集中转移
        ctx.state = self.sm.transition(ctx.state, decision.next_state, ticket_id=ctx.ticket_id)
        ctx.decision = decision
        return decision

    def _run_loop(self, ctx: AgentContext, plan) -> None:
        # 据 plan.allowed_tools 从 registry 构造工具规格（白名单约束 LLM 可调工具）
        specs: list[ToolSpec] | None = None
        if plan.allowed_tools and self.tools is not None:
            specs = self.tools.specs(plan.allowed_tools)
        messages = list(ctx.history) + [Msg(role="user", content=ctx.message)]
        for _ in range(max(1, plan.max_iterations)):
            resp = self.llm.chat(system=plan.system_prompt, messages=messages, tools=specs)
            ctx.token_acct.add(resp.usage, self.llm.model_name)

            if resp.stop_reason == "tool_use" and resp.tool_calls:
                # 骨架：无注册工具，记录后即视为完成；M3 在此 precheck+执行+回灌
                for tc in resp.tool_calls:
                    self.guardrails.precheck(tc.name, tc.arguments)
                logger.info("loop: %d tool_calls (skeleton no-op)", len(resp.tool_calls))
                break
            # end_turn / max_tokens / refusal → 收尾
            ctx.history.append(Msg(role="assistant", content=resp.text))
            break


def build_default_agent(llm: LLMClient | None = None, tool_registry=None) -> AgentCore:
    """默认装配。骨架/测试用 stub LLM；M4/M5 换 registry 选真实 provider。

    tool_registry 默认装载全部业务工具（M3）；harness 据 SkillPlan.allowed_tools 取规格。
    """
    if tool_registry is None:
        from app.tools.registry import build_tool_registry
        tool_registry = build_tool_registry()
    return AgentCore(
        llm=llm or StubLLMClient(),
        classifier=RuleIntentClassifier(),
        router=SkillRouter(),
        state_machine=StateMachine(),
        guardrails=Guardrails(),
        tool_registry=tool_registry,
    )
