"""OrderQuerySkill —— 订单查询（只读专门 Skill）。

把高频的"我的订单到哪一步了/什么时候发货"从兜底 GeneralSkill 接管：查订单 + 物流概况，
结构化播报状态/金额/时间线。纯只读，无写操作、不需确认门。
（eval LLM-Judge 实测：order_query 落兜底时 helpfulness 垫底，故单独成 Skill。）
"""
from __future__ import annotations

from datetime import datetime

from app.agent.context import AgentContext, Decision, SkillPlan
from app.agent.intent_classifier import Intents
from app.agent.state_machine import States
from app.services import logistics_service, order_service

_SYSTEM = "你是订单查询助手。只如实播报订单与物流信息，不得编造金额/时间/状态。"

_STATUS_CN = {
    "pending_payment": "待付款", "paid": "已付款", "shipped": "已发货",
    "delivered": "已签收", "cancelled": "已取消",
}


def _d(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


class OrderQuerySkill:
    name = "order_query"
    triggers = {Intents.ORDER_QUERY}

    def plan(self, ctx: AgentContext) -> SkillPlan:
        if ctx.order_id is None:
            return SkillPlan(system_prompt=_SYSTEM, allowed_tools=[], required_info=["订单号"])
        return SkillPlan(system_prompt=_SYSTEM,
                         allowed_tools=["get_order_detail", "get_logistics_status"],
                         max_iterations=2)

    def finalize(self, ctx: AgentContext, tool_ctx=None) -> Decision:
        if tool_ctx is None:
            return Decision("系统暂时无法处理，已为您转人工。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="no_tool_context")
        db = tool_ctx.db
        if ctx.order_id is None:
            return Decision("请提供需要查询的订单号。", States.INFO_REQUIRED, required_info=["订单号"])

        order = order_service.get_order(db, ctx.order_id)
        if order is None:
            return Decision("未查询到该订单信息，请核对订单号。", States.RESOLVED_BY_AGENT)
        if order.user_id != ctx.user_id:        # 防越权查他人订单
            return Decision("未能核实该订单归属，已为您转人工核实。", States.NEED_HUMAN,
                            need_handoff=True, handoff_reason="not_owner")

        parts = [f"订单 {order.order_no}：状态 {_STATUS_CN.get(order.status, order.status)}",
                 f"金额 {float(order.total_amount)} 元"]
        times = [f"{label} {_d(t)}" for label, t in
                 (("付款", order.paid_at), ("发货", order.shipped_at), ("签收", order.delivered_at)) if t]
        if times:
            parts.append("、".join(times))
        logi = logistics_service.get_logistics(db, order.id)
        if logi:
            loc = logi.last_location or "更新中"
            carrier = (logi.carrier or "").strip()
            parts.append(f"物流：{carrier} 最新位置 {loc}".strip())
        return Decision("；".join(parts) + "。", States.RESOLVED_BY_AGENT)
