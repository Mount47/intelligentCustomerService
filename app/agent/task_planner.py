"""第一阶段多意图任务规划与结果合并。

它仍在同一个 AgentCore 内顺序执行现有 Skill，不创建子 Agent，也不生成复杂任务图。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.intent_classifier import ActionType, IntentResult, Intents, MultiIntentResult
from app.agent.state_machine import States

_PRIORITY_HANDOFF = {Intents.HUMAN_HANDOFF, Intents.PRODUCT_COMPLAINT}
_WRITE_INTENTS = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST, Intents.CANCEL_REFUND}
_READ_ONLY_INTENTS = {
    Intents.ORDER_QUERY,
    Intents.LOGISTICS_QUERY,
    Intents.LOGISTICS_EXCEPTION,
    Intents.REFUND_INQUIRY,
    Intents.GENERAL_POLICY_QUERY,
    Intents.INVOICE_REQUEST,
    Intents.COUPON_ISSUE,
}


@dataclass(frozen=True)
class PlannedTask:
    intent_result: IntentResult
    order: int
    read_only: bool


@dataclass
class TaskPlan:
    tasks: list[PlannedTask] = field(default_factory=list)
    requires_clarification: bool = False
    reason: str = ""


@dataclass
class TaskOutcome:
    intent: Intents
    skill: str
    reply: str
    state: str
    need_handoff: bool = False
    handoff_reason: str | None = None


class TaskPlanner:
    """只支持线性顺序：只读任务在前，最多一个待确认写任务在后。"""

    def plan(self, result: MultiIntentResult, *, has_order: bool) -> TaskPlan:
        if result.requires_clarification:
            return TaskPlan(requires_clarification=True, reason=result.reason)
        if not result.intents:
            return TaskPlan(requires_clarification=True, reason="没有识别到可执行任务")
        if len(result.order_references) > 1:
            return TaskPlan(requires_clarification=True, reason="请一次只选择一笔订单")

        priority = [item for item in result.intents if item.intent in _PRIORITY_HANDOFF]
        if priority:
            # 投诉/人工信号优先，其他写任务不执行。
            return TaskPlan([PlannedTask(priority[0], 0, False)])

        writes = [item for item in result.intents
                  if item.intent in _WRITE_INTENTS or item.action_type == ActionType.EXECUTE]
        if len(writes) > 1:
            return TaskPlan(
                requires_clarification=True,
                reason="同一轮包含多个退款或退货操作，请只选择一个",
            )
        if any(item.intent not in _READ_ONLY_INTENTS and item not in writes
               for item in result.intents):
            return TaskPlan(requires_clarification=True, reason="这些诉求暂不支持组合处理")
        if not has_order and len(result.intents) > 1:
            return TaskPlan(requires_clarification=True, reason="请先选择本轮共同关联的一笔订单")

        reads = [item for item in result.intents if item not in writes]
        ordered = reads + writes
        return TaskPlan([
            PlannedTask(item, index, item in reads)
            for index, item in enumerate(ordered)
        ])


class OutcomeCoordinator:
    """按任务分段合并确定性结果，不调用模型润色。"""

    _LABELS = {
        Intents.ORDER_QUERY: "订单信息",
        Intents.LOGISTICS_QUERY: "物流信息",
        Intents.LOGISTICS_EXCEPTION: "物流信息",
        Intents.REFUND_INQUIRY: "退款说明",
        Intents.REFUND_REQUEST: "退款申请",
        Intents.RETURN_REQUEST: "退货申请",
        Intents.PRODUCT_COMPLAINT: "人工处理",
        Intents.HUMAN_HANDOFF: "人工处理",
    }
    _STATE_PRIORITY = {
        States.RESOLVED_BY_AGENT: 0,
        States.INFO_REQUIRED: 1,
        States.WAITING_USER_CONFIRM: 2,
        States.NEED_HUMAN: 3,
    }

    def combine(self, outcomes: list[TaskOutcome]):
        from app.agent.context import Decision

        if not outcomes:
            return Decision("请拆分并明确您要处理的事项。", States.INFO_REQUIRED,
                            required_info=["明确诉求"])
        state = max(
            (outcome.state for outcome in outcomes),
            key=lambda value: self._STATE_PRIORITY.get(value, 0),
        )
        sections = [
            f"【{self._LABELS.get(outcome.intent, '处理结果')}】{outcome.reply}"
            for outcome in outcomes
        ]
        handoff = next((outcome for outcome in outcomes if outcome.need_handoff), None)
        return Decision(
            reply="\n".join(sections),
            next_state=state,
            need_handoff=handoff is not None,
            handoff_reason=handoff.handoff_reason if handoff else None,
        )
