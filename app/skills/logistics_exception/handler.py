"""LogisticsExceptionSkill（声明式）。物流查询/异常：催件工单 / 签收未收到转人工。"""
from __future__ import annotations

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States
from app.core.config import get_settings
from app.services import logistics_service, order_service, ticket_service

_SYSTEM = (
    "你是物流助手。可调用只读工具查询物流状态与异常，向用户说明；"
    "不得承诺具体送达时间（除非物流工具返回），不得编造物流信息。"
)
_READ_TOOLS = ["get_logistics_status", "check_logistics_exception"]
_NOT_RECEIVED = ("没收到", "未收到", "没收", "未签收", "没到货")
_STATUS_CN = {
    "pending": "待揽收",
    "in_transit": "运输中",
    "delivered": "已签收",
    "exception": "物流异常",
}


def _logistics_snapshot(exc: dict) -> str:
    """异常和正常分支都先说明可核实的最近物流事实。"""
    status = _STATUS_CN.get(exc.get("status"), exc.get("status") or "更新中")
    location = exc.get("last_location") or "暂未更新"
    updated_at = exc.get("last_update_time")
    updated_text = updated_at.strftime("%Y-%m-%d %H:%M") if updated_at else "暂无更新时间"
    return (
        f"包裹状态为{status}，最近一次物流位置是{location}，"
        f"最后更新于{updated_text}。"
    )


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
            order = order_service.get_order(db, ctx.order_id)
            # 未发货（待付款/已付款）本就没有物流记录，是正常状态，不是异常
            if order is not None and order.status in ("pending_payment", "paid"):
                return Decision("您的订单已付款，正在备货中，暂未发货，发货后会同步物流信息。",
                                States.RESOLVED_BY_AGENT)
            if order is not None and order.status == "cancelled":
                return Decision("该订单已取消，不涉及物流。", States.RESOLVED_BY_AGENT)
            return Decision("暂未查询到该订单的物流信息，已为您转人工核实。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="logistics_not_found")

        msg = ctx.message or ""
        # 显示签收但用户未收到 → 转人工
        if exc.get("delivered") and any(k in msg for k in _NOT_RECEIVED):
            return Decision("物流显示已签收，但您反馈未收到，已为您升级人工核实。",
                            States.NEED_HUMAN, need_handoff=True,
                            handoff_reason="delivered_not_received")

        # 异常 / 48h 无更新 → 催件工单（会话级去重：同订单已有进行中催件单则不重复建）
        if exc.get("is_exception"):
            existing = ticket_service.get_open_ticket(db, ctx.user_id, ctx.order_id, "logistics")
            if existing is not None:
                return Decision(
                    f"{_logistics_snapshot(exc)}该订单的催件工单（#{existing.id}）已在处理中，"
                    "物流专员会尽快跟进，无需重复提交。",
                    States.RESOLVED_BY_AGENT)
            t = ticket_service.create_ticket(
                db, ctx.user_id, "logistics", priority="high", order_id=ctx.order_id)
            reason = exc.get("exception_reason") or "长时间无更新"
            return Decision(
                f"{_logistics_snapshot(exc)}检测到物流异常（{reason}），"
                f"已为您创建催件工单（#{t.id}），物流专员将尽快跟进。",
                States.RESOLVED_BY_AGENT)

        # 正常
        return Decision(_logistics_snapshot(exc), States.RESOLVED_BY_AGENT)
