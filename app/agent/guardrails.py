"""Guardrails（§11）。三层：工具 precheck / 回复 postcheck / 系统提示约束。

骨架阶段实现 postcheck 的禁语扫描骨架 + precheck 占位；真实拦截/降级策略 M5 充实。
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)

# 禁语：禁止承诺一定退款/赔偿/送达时间（除非工具返回）。评测在真实输出上量化拦截率。
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "一定退款", "保证退款", "肯定能退", "必须退",
    "一定赔偿", "保证赔偿", "全额赔",
    "保证送达", "一定到", "今天必到",
)

# 写操作/危险工具：harness precheck 须特别对待（权限+幂等+人工闸门）
DANGEROUS_TOOLS: frozenset[str] = frozenset(
    {"create_refund_draft", "handoff_to_human", "update_ticket_status"}
)


class Guardrails:
    def precheck(self, tool_name: str, args: dict) -> bool:
        """工具调用前校验。骨架默认放行；危险工具记日志（M5 接入权限/幂等/确认闸门）。"""
        if tool_name in DANGEROUS_TOOLS:
            logger.info("guardrail precheck: dangerous tool %s args=%s", tool_name, args)
        return True

    def scan_forbidden(self, reply: str) -> list[str]:
        """返回命中的禁语列表（评测用）。"""
        return [p for p in FORBIDDEN_PHRASES if p in (reply or "")]

    def postcheck(self, reply: str) -> str:
        """回复前最后一道。命中禁语则降级为安全话术并记日志（不让违规输出外泄）。"""
        hits = self.scan_forbidden(reply)
        if hits:
            logger.warning("guardrail postcheck blocked phrases: %s", hits)
            return "您的诉求我们已记录，是否退款/赔偿需依据订单情况与售后政策核实后确认，已为您升级人工跟进。"
        return reply
