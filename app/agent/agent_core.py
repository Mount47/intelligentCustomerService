"""AgentCore harness —— 统一执行管道（§5.4）。

ReAct loop 归这里统一跑（不散落各 Skill）；guardrails / 状态机闸门 / token 记账集中一处。
M1.5 骨架：无工具、stub LLM 一轮 end_turn；M3 接 ToolRegistry，M4/M5 接真实 LLM 与业务 Skill。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.agent.context import AgentContext, Decision, ToolCallRecord
from app.agent.guardrails import Guardrails
from app.agent.intent_classifier import HybridIntentClassifier
from app.agent.skill_router import SkillRouter
from app.agent.state_machine import StateMachine, States
from app.core.logging import get_logger
from app.llm.base import LLMClient, Msg, ToolSpec
from app.llm.stub import StubLLMClient

if TYPE_CHECKING:
    from app.tools.base import ToolContext

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

    def handle(self, ctx: AgentContext, tool_ctx: "ToolContext | None" = None) -> Decision:
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
            # 7 harness 统一跑 ReAct loop（真执行工具 + 回灌）
            self._run_loop(ctx, plan, tool_ctx)
            # 8 Skill 据结果产出决策（可经 tool_ctx 执行确定性写操作）
            decision = skill.finalize(ctx, tool_ctx)

        # 9 guardrails 回复后校验（绝不绕过）
        decision.reply = self.guardrails.postcheck(decision.reply)

        # 10 状态机集中转移
        ctx.state = self.sm.transition(ctx.state, decision.next_state, ticket_id=ctx.ticket_id)
        ctx.decision = decision
        return decision

    def _run_loop(self, ctx: AgentContext, plan, tool_ctx: "ToolContext | None") -> None:
        # 据 plan.allowed_tools 从 registry 构造工具规格（白名单约束 LLM 可调工具）
        specs: list[ToolSpec] | None = None
        if plan.allowed_tools and self.tools is not None:
            specs = self.tools.specs(plan.allowed_tools)
        allowed = set(plan.allowed_tools or [])
        messages = list(ctx.history) + [Msg(role="user", content=ctx.message)]

        for _ in range(max(1, plan.max_iterations)):
            resp = self.llm.chat(system=plan.system_prompt, messages=messages, tools=specs)
            ctx.token_acct.add(resp.usage, self.llm.model_name)

            if resp.stop_reason == "tool_use" and resp.tool_calls:
                # 回放 assistant 的工具调用，再逐个执行并回灌结果
                messages.append(Msg(role="assistant", content=resp.text,
                                    tool_calls=resp.tool_calls))
                for tc in resp.tool_calls:
                    result = self._exec_tool(ctx, tool_ctx, allowed, tc)
                    messages.append(Msg(role="tool",
                                        content=json.dumps(result, ensure_ascii=False),
                                        tool_call_id=tc.id))
                continue
            # end_turn / max_tokens / refusal → 收尾
            if resp.text:
                ctx.history.append(Msg(role="assistant", content=resp.text))
            break

    def _exec_tool(self, ctx: AgentContext, tool_ctx, allowed: set, tc) -> dict:
        """执行单个工具调用，返回 ToolResult，并把记录追加到 ctx.tool_call_records。"""
        # 白名单外的工具拒绝执行（防 LLM 越权调用）
        if tc.name not in allowed:
            result = {"ok": False, "data": None,
                      "error": {"code": "tool_not_allowed", "message": f"工具 {tc.name} 不在本技能白名单"}}
            ctx.tool_call_records.append(
                ToolCallRecord(tc.name, tc.arguments, result, False, 0, "not allowed"))
            return result
        # 危险动作闸门（高风险退款由 service 兜底为 pending_human；M5 Skill 再加人工确认）
        self.guardrails.precheck(tc.name, tc.arguments)
        if tool_ctx is None or self.tools is None:
            result = {"ok": False, "data": None,
                      "error": {"code": "no_tool_context", "message": "缺少工具执行上下文"}}
            ctx.tool_call_records.append(
                ToolCallRecord(tc.name, tc.arguments, result, False, 0, "no tool context"))
            return result
        result, record = self.tools.execute(tool_ctx, tc.name, tc.arguments)
        ctx.tool_call_records.append(record)
        return result


def build_default_agent(llm: LLMClient | None = None, tool_registry=None) -> AgentCore:
    """默认装配。骨架/测试用 stub LLM；M4/M5 换 registry 选真实 provider。

    tool_registry 默认装载全部业务工具（M3）；harness 据 SkillPlan.allowed_tools 取规格。
    """
    if tool_registry is None:
        from app.tools.registry import build_tool_registry
        tool_registry = build_tool_registry()
    llm = llm or StubLLMClient()
    # 注册业务 Skill（未认领的意图落兜底 GeneralSkill）
    from app.skills.logistics_exception.handler import LogisticsExceptionSkill
    from app.skills.refund_handling.handler import RefundHandlingSkill
    router = SkillRouter(skills=[RefundHandlingSkill(), LogisticsExceptionSkill()])
    return AgentCore(
        llm=llm,
        classifier=HybridIntentClassifier(llm=llm),  # 规则快路 + 同一 LLM 兜底
        router=router,
        state_machine=StateMachine(),
        guardrails=Guardrails(),
        tool_registry=tool_registry,
    )
