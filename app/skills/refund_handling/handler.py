"""RefundHandlingSkill（声明式，§5.2 / ADR-8）。

LLM 在 harness loop 中只读取信息；退款决策与写操作由 finalize 确定性执行（安全：
不可逆动作不交给 LLM）。高风险不自动退款，创建草稿 + 升级工单转人工。
"""
from __future__ import annotations

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States
from app.services import refund_service, ticket_service

_SYSTEM = (
    "你是电商退款助手。可调用只读工具核对订单与政策，向用户解释处理依据；"
    "不得承诺一定退款/赔偿，不得绕过订单校验，不得编造结果。最终退款由系统按政策处理。"
)
# LLM 仅可调只读工具（写操作由 finalize 确定性执行）
_READ_TOOLS = ["get_order_detail", "check_refund_policy", "search_policy_docs"]


class RefundHandlingSkill:
    name = "refund_handling"
    triggers = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST}

    def plan(self, ctx: AgentContext) -> SkillPlan:
        if ctx.order_id is None:
            return SkillPlan(system_prompt=_SYSTEM, allowed_tools=[],
                             required_info=["订单号"])
        return SkillPlan(system_prompt=_SYSTEM, allowed_tools=_READ_TOOLS, max_iterations=4)

    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision:
        if tool_ctx is None:
            return Decision("系统暂时无法处理，已为您转人工。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="no_tool_context")
        db = tool_ctx.db
        if ctx.order_id is None:
            return Decision("请提供需要退款的订单号。", States.INFO_REQUIRED,
                            required_info=["订单号"])

        # 会话级幂等（#8）：该订单已有进行中的退款 → 不重复建草稿
        # （挡住追问/换措辞/误判反复触发；三层幂等只防同请求重发，防不住这种）
        existing = refund_service.get_active_refund(db, ctx.user_id, ctx.order_id)
        if existing is not None:
            if existing.status == "pending_human":
                return Decision(
                    f"您该订单的退款正在人工审核中（单号 #{existing.id}），请耐心等待，无需重复提交。",
                    States.NEED_HUMAN, need_handoff=True, handoff_reason="refund_in_progress")
            return Decision(
                f"您该订单的退款申请（单号 #{existing.id}）已在处理中，我们会尽快跟进，无需重复提交。",
                States.RESOLVED_BY_AGENT)

        policy = refund_service.check_refund_policy(db, ctx.order_id, ctx.user_id, ctx.message)
        if not policy["eligible"]:
            reason = policy.get("reason")
            if reason in ("order_not_found", "not_owner"):
                return Decision("未能核实该订单归属，已为您转人工核实。", States.NEED_HUMAN,
                                need_handoff=True, handoff_reason=reason)
            return Decision("该订单当前状态不支持自动退款，已为您转人工处理。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="not_eligible")

        # 高风险：不自动退款，建草稿(pending_human) + 升级工单
        if policy["require_human_approval"]:
            refund_service.create_refund_draft(
                db, user_id=ctx.user_id, order_id=ctx.order_id, refund_reason=ctx.message)
            t = ticket_service.create_ticket(
                db, ctx.user_id, "refund", priority="high", order_id=ctx.order_id)
            ticket_service.update_status(db, t.id, States.NEED_HUMAN)
            reasons = "、".join(policy["reasons"]) or "需人工审核"
            return Decision(
                f"您的退款涉及人工审核（{reasons}），已提交审核工单（#{t.id}），专员将尽快跟进。",
                States.NEED_HUMAN, need_handoff=True, handoff_reason="refund_high_risk")

        # 低风险：自动创建退款草稿
        draft = refund_service.create_refund_draft(
            db, user_id=ctx.user_id, order_id=ctx.order_id, refund_reason=ctx.message)
        ref = f" 依据：{policy['policy_ref']}" if policy.get("policy_ref") else ""
        return Decision(
            f"已为您创建退款申请（金额 {policy['amount']} 元，单号 #{draft['refund_request_id']}），"
            f"我们将尽快处理。{ref}",
            States.RESOLVED_BY_AGENT)
