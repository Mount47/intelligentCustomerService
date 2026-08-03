"""RefundHandlingSkill（声明式，§5.2 / ADR-8 / Step2 确认流）。

四条退款路径闭环（按结构化意图分派）：
- refund_request / return_request：核对资格 → 低风险【进入待确认，不直接建草稿】/ 高风险直接转人工
- refund_confirmation：仅当 ticket.pending_action 是 refund_request 时，确定性建草稿（幂等）
- cancel_refund：撤待确认动作 或 撤已建草稿
- refund_inquiry：只读答疑，绝不写

读写分离：LLM 在 loop 中只读；退款的建/撤由 finalize 确定性执行。
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States
from app.db.models import Ticket
from app.core.exceptions import InvalidPendingAction
from app.services import pending_action_service, refund_service, ticket_service

_SYSTEM = (
    "你是电商退款助手。可调用只读工具核对订单与政策，向用户解释处理依据；"
    "不得承诺一定退款/赔偿，不得绕过订单校验，不得编造结果。最终退款由系统按政策处理。"
)
_READ_TOOLS = ["get_order_detail", "check_refund_policy", "search_policy_docs", "get_order_refund"]
_NEW_REFUND_INTENTS = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST}


class RefundHandlingSkill:
    name = "refund_handling"
    triggers = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST, Intents.REFUND_INQUIRY,
                Intents.CANCEL_REFUND, Intents.REFUND_CONFIRMATION}

    def _intent(self, ctx: AgentContext) -> Intents:
        return ctx.intent_result.intent if ctx.intent_result else Intents.REFUND_REQUEST

    def plan(self, ctx: AgentContext) -> SkillPlan:
        intent = self._intent(ctx)
        # 确认/取消：确定性执行，无需 LLM 读取
        if intent in (Intents.REFUND_CONFIRMATION, Intents.CANCEL_REFUND):
            return SkillPlan(system_prompt=_SYSTEM, allowed_tools=[], max_iterations=1)
        # 请求/退货缺单号 → 索取
        if intent in _NEW_REFUND_INTENTS and ctx.order_id is None:
            return SkillPlan(system_prompt=_SYSTEM, allowed_tools=[], required_info=["订单号"])
        # 请求/咨询：允许只读核对
        return SkillPlan(system_prompt=_SYSTEM, allowed_tools=_READ_TOOLS, max_iterations=4)

    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision:
        if tool_ctx is None:
            return Decision("系统暂时无法处理，已为您转人工。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="no_tool_context")
        db = tool_ctx.db
        ticket = db.get(Ticket, ctx.ticket_id) if ctx.ticket_id else None
        intent = self._intent(ctx)

        if intent == Intents.REFUND_INQUIRY:
            return self._inquiry(db, ctx)
        if intent == Intents.CANCEL_REFUND:
            return self._cancel(db, ctx, ticket)
        if intent == Intents.REFUND_CONFIRMATION:
            return self._confirm(db, ctx, ticket)
        return self._request(db, ctx, ticket)

    # ---- refund_request / return_request：核对 → 低风险进待确认 / 高风险转人工 ----
    def _request(self, db, ctx: AgentContext, ticket) -> Decision:
        if ctx.order_id is None:
            return Decision("请提供需要退款的订单号。", States.INFO_REQUIRED, required_info=["订单号"])
        policy = refund_service.check_refund_policy(db, ctx.order_id, ctx.user_id, ctx.message)
        if not policy["eligible"]:
            reason = policy.get("reason")
            if reason in ("order_not_found", "not_owner"):
                return Decision("未能核实该订单归属，已为您转人工核实。", States.NEED_HUMAN,
                                need_handoff=True, handoff_reason=reason)
            return Decision("该订单当前状态不支持自动退款，已为您转人工处理。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="not_eligible")
        # 会话级幂等：已有进行中退款不重复（#8）
        existing = refund_service.get_active_refund(db, ctx.user_id, ctx.order_id)
        if existing is not None:
            if existing.status == "pending_human":
                return Decision(f"您该订单的退款正在人工审核中（单号 #{existing.id}），请耐心等待，无需重复提交。",
                                States.NEED_HUMAN, need_handoff=True, handoff_reason="refund_in_progress")
            return Decision(f"您该订单的退款申请（单号 #{existing.id}）已在处理中，无需重复提交。",
                            States.RESOLVED_BY_AGENT)
        # 高风险：不经用户确认，直接建草稿(pending_human) + 升级转人工
        if policy["require_human_approval"]:
            refund_service.create_refund_draft(
                db, user_id=ctx.user_id, order_id=ctx.order_id,
                refund_reason=ctx.message, commit=False)
            t = ticket_service.create_ticket(
                db, ctx.user_id, "refund", priority="high", order_id=ctx.order_id)
            ticket_service.update_status(db, t.id, States.NEED_HUMAN)
            reasons = "、".join(policy["reasons"]) or "需人工审核"
            return Decision(f"您的退款涉及人工审核（{reasons}），已提交审核工单（#{t.id}），专员将尽快跟进。",
                            States.NEED_HUMAN, need_handoff=True, handoff_reason="refund_high_risk")
        # 低风险：进入待确认，绑定 pending_action，不直接建草稿
        action_type = self._intent(ctx)
        operation = "退货" if action_type == Intents.RETURN_REQUEST else "退款"
        if ticket is not None:
            ticket.pending_action = {
                "id": uuid4().hex,
                "type": action_type.value,
                "order_id": ctx.order_id,
                "amount": policy["amount"],
                "created_at": datetime.utcnow().isoformat(),
            }
            db.flush()
        return Decision(
            f"该订单可以申请{operation}，金额 {policy['amount']} 元。"
            f"请在下方确认是否提交。",
            States.WAITING_USER_CONFIRM,
        )

    # ---- refund_confirmation：仅当绑定了 pending refund action 才真正建草稿 ----
    def _confirm(self, db, ctx: AgentContext, ticket) -> Decision:
        try:
            validated = pending_action_service.validate_refund_pending_action(
                db, ticket, ctx.user_id, allow_submitted_confirm=True
            )
        except InvalidPendingAction as exc:
            pending_action_service.clear_pending_action(ticket)
            db.flush()
            return Decision(str(exc), States.RESOLVED_BY_AGENT)

        order_id = validated.order.id
        operation = "退货" if validated.action_type == Intents.RETURN_REQUEST.value else "退款"
        existing = refund_service.get_active_refund(db, ctx.user_id, order_id)
        if existing is not None:                     # 幂等：已建则不重复
            ticket.pending_action = None
            db.flush()
            return Decision(f"您该订单的退款申请（单号 #{existing.id}）已在处理中。", States.RESOLVED_BY_AGENT)
        draft = refund_service.create_refund_draft(
            db, user_id=ctx.user_id, order_id=order_id,
            refund_reason=f"用户确认{operation}", commit=False)
        ticket.pending_action = None
        db.flush()
        return Decision(
            f"已为您创建{operation}申请（金额 {draft['amount']} 元，"
            f"单号 #{draft['refund_request_id']}），我们将尽快处理。",
            States.RESOLVED_BY_AGENT,
        )

    # ---- cancel_refund：撤待确认动作 或 撤已建草稿 ----
    def _cancel(self, db, ctx: AgentContext, ticket) -> Decision:
        if ticket and ticket.pending_action and ticket.pending_action.get("type") in {
            Intents.REFUND_REQUEST.value, Intents.RETURN_REQUEST.value
        }:
            operation = (
                "退货"
                if ticket.pending_action.get("type") == Intents.RETURN_REQUEST.value
                else "退款"
            )
            ticket.pending_action = None
            db.flush()
            return Decision(f"已取消本次{operation}申请，未提交。", States.RESOLVED_BY_AGENT)
        if ctx.order_id is not None:
            cancelled = refund_service.cancel_active_refund(
                db, ctx.user_id, ctx.order_id, commit=False)
            if cancelled:
                return Decision(f"已为您撤销退款申请（单号 #{cancelled['refund_request_id']}）。",
                                States.RESOLVED_BY_AGENT)
        return Decision("您当前没有进行中的退款申请。", States.RESOLVED_BY_AGENT)

    # ---- refund_inquiry：只读答疑 ----
    def _inquiry(self, db, ctx: AgentContext) -> Decision:
        if ctx.order_id is not None:
            policy = refund_service.check_refund_policy(db, ctx.order_id, ctx.user_id, ctx.message)
            if policy.get("eligible") and not policy.get("require_human_approval"):
                ref = f" 依据：{policy['policy_ref']}" if policy.get("policy_ref") else ""
                return Decision(f"该订单符合退款条件（金额 {policy['amount']} 元）。"
                                f"如需办理请回复『我要退款』。{ref}", States.RESOLVED_BY_AGENT)
            return Decision("该订单退款可能涉及人工审核，您可发起申请后由专员跟进。", States.RESOLVED_BY_AGENT)
        return Decision("退款政策：未发货可全额退；普通商品签收 7 天内可申请；生鲜/定制商品及"
                        "单笔超 500 元需人工审核。需要办理请提供订单号。", States.RESOLVED_BY_AGENT)
