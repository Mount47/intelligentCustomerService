"""工单状态机（§7）。状态流转集中此处，非法转移抛异常，每次转移记日志。

高风险禁止直达 resolved_by_agent（由 Skill/guardrails 决定 next_state，状态机只校验合法性）。
"""
from __future__ import annotations

from app.core.exceptions import InvalidStateTransition
from app.core.logging import get_logger

logger = get_logger(__name__)


class States:
    # 正常态
    CREATED = "created"
    INTENT_DETECTED = "intent_detected"
    INFO_REQUIRED = "info_required"
    INFO_COLLECTED = "info_collected"
    TOOL_EXECUTING = "tool_executing"
    WAITING_USER_CONFIRM = "waiting_user_confirm"
    RESOLVED_BY_AGENT = "resolved_by_agent"
    NEED_HUMAN = "need_human"
    RESOLVED_BY_HUMAN = "resolved_by_human"
    REJECTED = "rejected"
    CLOSED = "closed"
    # 异常态
    TOOL_FAILED = "tool_failed"
    POLICY_CONFLICT = "policy_conflict"
    USER_ANGRY = "user_angry"
    REFUND_HIGH_RISK = "refund_high_risk"
    SLA_TIMEOUT = "sla_timeout"


# 合法转移表：from -> {允许的 to}
TRANSITIONS: dict[str, set[str]] = {
    States.CREATED: {States.INTENT_DETECTED},
    States.INTENT_DETECTED: {
        States.INFO_REQUIRED, States.TOOL_EXECUTING,
        States.NEED_HUMAN, States.RESOLVED_BY_AGENT,
        States.USER_ANGRY,
    },
    States.INFO_REQUIRED: {States.INFO_COLLECTED, States.NEED_HUMAN, States.CLOSED},
    States.INFO_COLLECTED: {States.TOOL_EXECUTING, States.NEED_HUMAN},
    States.TOOL_EXECUTING: {
        States.WAITING_USER_CONFIRM, States.RESOLVED_BY_AGENT,
        States.NEED_HUMAN, States.TOOL_FAILED, States.REFUND_HIGH_RISK,
    },
    States.WAITING_USER_CONFIRM: {
        States.TOOL_EXECUTING, States.RESOLVED_BY_AGENT,
        States.NEED_HUMAN, States.REJECTED,
    },
    States.RESOLVED_BY_AGENT: {States.CLOSED},
    States.NEED_HUMAN: {States.RESOLVED_BY_HUMAN, States.REJECTED, States.CLOSED},
    States.RESOLVED_BY_HUMAN: {States.CLOSED},
    States.REJECTED: {States.CLOSED},
    States.CLOSED: set(),  # 终态
    # 异常态出口
    States.TOOL_FAILED: {States.NEED_HUMAN, States.TOOL_EXECUTING},
    States.POLICY_CONFLICT: {States.NEED_HUMAN},
    States.USER_ANGRY: {States.NEED_HUMAN},
    States.REFUND_HIGH_RISK: {States.NEED_HUMAN, States.WAITING_USER_CONFIRM},
    States.SLA_TIMEOUT: {States.NEED_HUMAN},
}


class StateMachine:
    """集中管理转移。Skill 只声明 next_state，由此处校验+执行+记日志。"""

    def transition(self, current: str, target: str, *, ticket_id: int | None = None) -> str:
        allowed = TRANSITIONS.get(current)
        if allowed is None:
            raise InvalidStateTransition(f"unknown state: {current!r}")
        if target not in allowed:
            raise InvalidStateTransition(
                f"illegal transition {current!r} -> {target!r}"
            )
        logger.info("ticket=%s state %s -> %s", ticket_id, current, target)
        return target

    def can(self, current: str, target: str) -> bool:
        return target in TRANSITIONS.get(current, set())
