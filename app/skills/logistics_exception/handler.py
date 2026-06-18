"""LogisticsExceptionSkill（声明式）。物流查询/异常：催件工单 / 签收未收到转人工。"""
from __future__ import annotations

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States
from app.core.config import get_settings
from app.services import logistics_service, ticket_service

_SYSTEM = (
    "你是物流助手。可调用只读工具查询物流状态与异常，向用户说明；"
    "不得承诺具体送达时间（除非物流工具返回），不得编造物流信息。"
)
_READ_TOOLS = ["get_logistics_status", "check_logistics_exception"]
_NOT_RECEIVED = ("没收到", "未收到", "没收", "未签收", "没到货")


class LogisticsExceptionSkill:
    name = "logistics_exception"
    triggers = {Intents.LOGISTICS_QUERY, Intents.LOGISTICS_EXCEPTION}

    def plan(self, ctx: AgentContext) -> SkillPlan:
        if ctx.order_id is None:
            return SkillPlan(system_prompt=_SYSTEM, allowed_tools=[], required_info=["订单号"])
        return SkillPlan(system_prompt=_SYSTEM, allowed_tools=_READ_TOOLS, max_iterations=3)

    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision:
        if tool_ctx is None:
            return Decision("系统暂时无法处理，已为您转人工。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="no_tool_context")
        db = tool_ctx.db
        if ctx.order_id is None:
            return Decision("请提供需要查询的订单号。", States.INFO_REQUIRED,
                            required_info=["订单号"])

        stale_hours = get_settings().logistics_stale_hours
        exc = logistics_service.detect_exception(db, ctx.order_id, stale_hours)
        if not exc.get("found"):
            return Decision("暂未查询到该订单的物流信息，已为您转人工核实。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="logistics_not_found")

        msg = ctx.message or ""
        # 显示签收但用户未收到 → 转人工
        if exc.get("delivered") and any(k in msg for k in _NOT_RECEIVED):
            return Decision("物流显示已签收，但您反馈未收到，已为您升级人工核实。",
                            States.NEED_HUMAN, need_handoff=True,
                            handoff_reason="delivered_not_received")

        # 异常 / 48h 无更新 → 催件工单
        if exc.get("is_exception"):
            t = ticket_service.create_ticket(
                db, ctx.user_id, "logistics", priority="high", order_id=ctx.order_id)
            reason = exc.get("exception_reason") or "长时间无更新"
            return Decision(
                f"检测到物流异常（{reason}），已为您创建催件工单（#{t.id}），物流专员将尽快跟进。",
                States.RESOLVED_BY_AGENT)

        # 正常
        return Decision(
            f"您的包裹当前状态：{exc.get('status')}，最新位置：{exc.get('last_location') or '更新中'}。",
            States.RESOLVED_BY_AGENT)
