"""意图识别：规则快路 + LLM 兜底（ADR-7 已定）。

骨架阶段只实现规则快路；LLM 兜底先留挂钩（命中不了规则时返回 GENERAL_POLICY_QUERY 占位，
M4 接真实 LLMIntentClassifier）。
"""
from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


class Intents:
    ORDER_QUERY = "order_query"
    LOGISTICS_QUERY = "logistics_query"
    LOGISTICS_EXCEPTION = "logistics_exception"
    REFUND_REQUEST = "refund_request"
    RETURN_REQUEST = "return_request"
    INVOICE_REQUEST = "invoice_request"
    COUPON_ISSUE = "coupon_issue"
    PRODUCT_COMPLAINT = "product_complaint"
    HUMAN_HANDOFF = "human_handoff"
    GENERAL_POLICY_QUERY = "general_policy_query"
    OUT_OF_SCOPE = "out_of_scope"


# 规则关键词表（命中即定，快、零成本、可评测）。顺序靠前者优先。
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (Intents.HUMAN_HANDOFF, ("人工", "转人工", "客服", "投诉到底")),
    (Intents.PRODUCT_COMPLAINT, ("投诉", "质量问题", "坏了", "破损", "假货")),
    (Intents.RETURN_REQUEST, ("退货", "退回", "寄回")),
    (Intents.REFUND_REQUEST, ("退款", "退钱", "退费", "申请退")),
    (Intents.LOGISTICS_EXCEPTION, ("没收到", "未收到", "丢件", "物流异常", "不动了", "催")),
    (Intents.LOGISTICS_QUERY, ("物流", "快递", "到哪", "发货", "运单", "单号")),
    (Intents.INVOICE_REQUEST, ("发票", "开票", "税号", "抬头")),
    (Intents.COUPON_ISSUE, ("优惠券", "券", "满减", "折扣码")),
    (Intents.ORDER_QUERY, ("订单", "我的单", "买的")),
]


class RuleIntentClassifier:
    """规则快路。命中返回 (intent, from_rule=True)；未命中返回兜底意图 from_rule=False。"""

    def classify(self, message: str) -> tuple[str, bool]:
        text = (message or "").lower()
        for intent, kws in _KEYWORDS:
            if any(kw in text for kw in kws):
                logger.info("intent(rule)=%s", intent)
                return intent, True
        # TODO(M4): LLM 兜底。骨架先归 general_policy_query 占位。
        logger.info("intent(fallback)=%s", Intents.GENERAL_POLICY_QUERY)
        return Intents.GENERAL_POLICY_QUERY, False
