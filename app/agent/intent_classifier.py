"""意图识别：关键词召回 + 极性判定 + LLM 兜底（ADR-7，结构化升级）。

设计（见 实际问题与解决.md #8 / 定位与亮点）：
- intent 统一用 `Intents`（str-Enum，不散落字符串）。
- **关键词只做候选召回**，不直接决定动作；退款族再经极性判定才定 intent。
- 否定 > 退款关键词（"我不想退款了"→cancel_refund）；疑问 ≠ refund_request（"能退款吗"→refund_inquiry）。
- refund_confirmation 仅在 `state == waiting_user_confirm` 才识别。
- `classify` 保留旧 (intent, from_rule) 元组接口（向后兼容）；`classify_intent` 产出结构化 IntentResult。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.agent.state_machine import States
from app.core.logging import get_logger

logger = get_logger(__name__)


class Intents(str, Enum):
    ORDER_QUERY = "order_query"
    LOGISTICS_QUERY = "logistics_query"
    LOGISTICS_EXCEPTION = "logistics_exception"
    REFUND_REQUEST = "refund_request"
    RETURN_REQUEST = "return_request"
    REFUND_INQUIRY = "refund_inquiry"            # 退款咨询（疑问，不执行）
    CANCEL_REFUND = "cancel_refund"              # 取消退款（否定）
    REFUND_CONFIRMATION = "refund_confirmation"  # 退款确认（仅等待确认态）
    INVOICE_REQUEST = "invoice_request"
    COUPON_ISSUE = "coupon_issue"
    PRODUCT_COMPLAINT = "product_complaint"
    HUMAN_HANDOFF = "human_handoff"
    GENERAL_POLICY_QUERY = "general_policy_query"
    OUT_OF_SCOPE = "out_of_scope"


class Polarity(str, Enum):
    POSITIVE = "positive"          # 肯定要做（我要退款）
    NEGATIVE = "negative"          # 否定/取消（我不想退款了）
    INTERROGATIVE = "interrogative"  # 疑问/咨询（能退款吗）
    NEUTRAL = "neutral"


class ActionType(str, Enum):
    QUERY = "query"        # 只读/答疑
    EXECUTE = "execute"    # 触发写操作（需确认）
    CANCEL = "cancel"      # 撤销
    CONFIRM = "confirm"    # 确认执行
    NONE = "none"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class IntentResult:
    intent: Intents
    polarity: Polarity = Polarity.NEUTRAL
    action_type: ActionType = ActionType.NONE
    confidence: Confidence = Confidence.MEDIUM
    requires_confirmation: bool = False
    reason: str = ""


# 规则关键词表（命中即候选，快、零成本、可评测）。顺序靠前者优先。
_KEYWORDS: list[tuple[Intents, tuple[str, ...]]] = [
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

ALL_INTENTS: tuple[Intents, ...] = tuple(Intents)

# 否定线索：必须是"否定退款"本身（绑定到"退"或显式取消），避免误伤"不想要了"这类退款理由
_NEGATION = ("不退", "不想退", "不要退", "不用退", "别退", "不退款", "取消退", "取消", "算了")
_INTERROGATIVE = ("吗", "嘛", "呢", "?", "？", "要不要", "能不能", "可不可以",
                  "是不是", "退不退", "是否", "能退", "可以退")
# A-不-A 疑问句式（如"要不要"）：内含的否定字样是疑问的一部分，不应判为否定
_DELIBERATION = ("要不要", "能不能", "可不可以", "是不是", "退不退", "用不用", "需不需要")
_CONFIRMATION = ("确认", "确定", "是的", "好的", "可以", "对", "嗯", "退吧", "帮我退", "就这样")

_REFUND_FAMILY = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST}


def _has(text: str, cues: tuple[str, ...]) -> bool:
    return any(c in text for c in cues)


class RuleIntentClassifier:
    """规则快路（候选召回）。命中返回 (intent, from_rule=True)；未命中返回兜底 from_rule=False。"""

    def classify(self, message: str) -> tuple[Intents, bool]:
        text = (message or "").lower()
        for intent, kws in _KEYWORDS:
            if any(kw in text for kw in kws):
                logger.info("intent(rule)=%s", intent.value)
                return intent, True
        logger.info("intent(rule-miss)")
        return Intents.GENERAL_POLICY_QUERY, False


class HybridIntentClassifier:
    """规则召回 + 极性判定 + LLM 兜底（ADR-7）。"""

    def __init__(self, llm=None, rule: RuleIntentClassifier | None = None) -> None:
        self.rule = rule or RuleIntentClassifier()
        self.llm = llm

    # ---- 向后兼容：旧 (intent, from_rule) 接口 ----
    def classify(self, message: str) -> tuple[Intents, bool]:
        intent, from_rule = self.rule.classify(message)
        if from_rule:
            return intent, True
        if self.llm is not None:
            picked = self._classify_llm(message)
            if picked:
                logger.info("intent(llm)=%s", picked.value)
                return picked, False
        return intent, False

    # ---- 新：结构化意图（关键词召回 → 极性 → 决策） ----
    def classify_intent(self, message: str, state: str | None = None) -> IntentResult:
        text = (message or "").lower().strip()
        is_q = _has(text, _INTERROGATIVE)
        # A-不-A 疑问（要不要/退不退…）里的"不要/不退"是疑问的一部分，不算否定
        is_neg = _has(text, _NEGATION) and not _has(text, _DELIBERATION)

        # 6. 退款确认：仅在等待确认态才识别
        if state == States.WAITING_USER_CONFIRM:
            if is_neg:
                return IntentResult(Intents.CANCEL_REFUND, Polarity.NEGATIVE, ActionType.CANCEL,
                                    Confidence.HIGH, False, "等待确认态下的否定→取消退款")
            if _has(text, _CONFIRMATION):
                return IntentResult(Intents.REFUND_CONFIRMATION, Polarity.POSITIVE, ActionType.CONFIRM,
                                    Confidence.HIGH, False, "等待确认态下的确认表达")

        candidate, from_rule = self.rule.classify(text)

        # 退款族：关键词只是候选，极性决定 intent（3/4/5）
        if from_rule and candidate in _REFUND_FAMILY:
            if is_neg:   # 4. 否定优先于退款关键词
                return IntentResult(Intents.CANCEL_REFUND, Polarity.NEGATIVE, ActionType.CANCEL,
                                    Confidence.MEDIUM, False, "退款关键词但含否定→取消退款")
            if is_q:     # 5. 疑问不识别为 refund_request
                return IntentResult(Intents.REFUND_INQUIRY, Polarity.INTERROGATIVE, ActionType.QUERY,
                                    Confidence.MEDIUM, False, "退款关键词但是疑问→退款咨询")
            return IntentResult(candidate, Polarity.POSITIVE, ActionType.EXECUTE,
                                Confidence.HIGH, True, "肯定的退款/退货请求，需确认后执行")

        if from_rule:    # 其他意图：候选直接采用，标注极性（写类工单暂不强制确认，Tier2）
            polarity = (Polarity.INTERROGATIVE if is_q
                        else Polarity.NEGATIVE if is_neg else Polarity.NEUTRAL)
            return IntentResult(candidate, polarity, ActionType.NONE,
                                Confidence.HIGH, False, "规则命中")

        # 召回未命中
        if _has(text, _CONFIRMATION):   # 无上下文的"确认" → 意图不明，需澄清（不算确认）
            return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                                Confidence.LOW, False, "无上下文的确认表达，意图不明需澄清")
        if self.llm is not None:
            picked = self._classify_llm(text)
            if picked:
                logger.info("intent(llm)=%s", picked.value)
                return IntentResult(picked, Polarity.NEUTRAL, ActionType.NONE,
                                    Confidence.MEDIUM, False, "LLM 兜底")
        # 召回未命中：按通用咨询兜底（MEDIUM，可答疑）；真正 LOW 只留给无上下文的"确认"等歧义
        return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                            Confidence.MEDIUM, False, "规则未命中，按通用咨询兜底")

    def _classify_llm(self, message: str) -> Intents | None:
        from app.llm.base import Msg
        system = (
            "你是售后意图分类器。从下列意图中选最匹配的一个，"
            "只输出英文标识本身，不要解释：\n" + ", ".join(i.value for i in ALL_INTENTS)
        )
        try:
            resp = self.llm.chat(system=system, messages=[Msg("user", message)])
        except Exception:  # noqa: BLE001 — 分类失败不阻断主流程
            return None
        text = (resp.text or "").strip().lower()
        for it in ALL_INTENTS:
            if it.value in text:
                return it
        return None
