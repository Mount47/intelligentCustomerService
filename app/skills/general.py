"""GeneralSkill —— 骨架/兜底 Skill。

声明式：plan 给一个无工具的系统提示，finalize 返回 canned 回复 + 终态。
RefundHandlingSkill / LogisticsExceptionSkill 在 M5 落，复用同一 Skill 协议。
"""
from __future__ import annotations

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States


class GeneralSkill:
    name = "general"
    # 兜底：未被业务 Skill 认领的意图都落到这里
    triggers = {Intents.GENERAL_POLICY_QUERY, Intents.OUT_OF_SCOPE, Intents.ORDER_QUERY}

    def plan(self, ctx: AgentContext) -> SkillPlan:
        return SkillPlan(
            system_prompt=(
                "你是电商售后助手。只能依据工具结果或政策知识库回答，"
                "不得编造，不得承诺一定退款/赔偿/送达时间。"
            ),
            allowed_tools=["search_policy_docs"],   # 允许检索政策以引用来源
            max_iterations=2,
        )

    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision:
        # 明确要求人工 / 投诉 → 转人工（情绪/投诉优先转人工，不承诺赔偿）
        if ctx.intent in (Intents.HUMAN_HANDOFF, Intents.PRODUCT_COMPLAINT):
            return Decision(
                "非常理解您的诉求，已为您升级人工专员跟进，请稍候。",
                States.NEED_HUMAN, need_handoff=True, handoff_reason=ctx.intent)
        # 其余（政策问答 / 超范围 / 暂未支持的意图）→ 兜底回复，不编造
        reply = (ctx.history[-1].content if ctx.history else "") or \
            "您的问题已收到，我们会尽快为您处理；如需具体业务办理可提供订单号。"
        return Decision(reply=reply, next_state=States.RESOLVED_BY_AGENT)
