"""意图识别：关键词召回 + 极性判定 + LLM 兜底（ADR-7，结构化升级）。

设计（见 实际问题与解决.md #8 / 定位与亮点）：
- intent 统一用 `Intents`（str-Enum，不散落字符串）。
- **关键词只做候选召回**，不直接决定动作；退款族再经极性判定才定 intent。
- 否定 > 退款关键词（"我不想退款了"→cancel_refund）；疑问 ≠ refund_request（"能退款吗"→refund_inquiry）。
- refund_confirmation 仅在 `state == waiting_user_confirm` 才识别。
- `classify` 保留旧 (intent, from_rule) 元组接口（向后兼容）；`classify_intent` 产出结构化 IntentResult。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from app.agent.state_machine import States
from app.core.logging import get_logger
from app.services import logistics_service

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


@dataclass
class MultiIntentResult:
    """单轮结构化意图集合；第一阶段最多容纳三个同订单意图。"""
    intents: list[IntentResult]
    requires_clarification: bool = False
    reason: str = ""
    order_references: tuple[str, ...] = ()

    @property
    def is_multi(self) -> bool:
        return len(self.intents) > 1


# 规则关键词表（命中即候选，快、零成本、可评测）。顺序靠前者优先。
_KEYWORDS: list[tuple[Intents, tuple[str, ...]]] = [
    (Intents.HUMAN_HANDOFF, ("人工", "转人工", "客服", "投诉到底")),
    (Intents.PRODUCT_COMPLAINT, ("投诉", "质量问题", "坏了", "破损", "假货")),
    (Intents.REFUND_REQUEST, ("退款", "退钱", "退费", "申请退")),   # 退款精确（先于退货广召回）
    (Intents.RETURN_REQUEST, ("退货", "退回", "寄回")),             # 退货精确
    # 退-口语广召回：覆盖"退衣服/退这个/退我的订单"等口语说法，并抢在 order_query 前
    # （修退款召回：漏召回 + 被订单查询抢占。长尾仍靠后续语义层 LLM-defer）
    (Intents.RETURN_REQUEST, ("想退", "要退", "帮我退", "退掉", "退一下", "退一件",
                              "退这", "退那", "退我的", "退商品", "退东西", "退衣", "退鞋", "退订单")),
    (Intents.LOGISTICS_EXCEPTION, ("没收到", "未收到", "丢件", "物流异常", "不动了", "催")),
    (Intents.LOGISTICS_QUERY, (
        "物流", "快递", "到哪", "发货", "运单", "单号", "已经到了", "是不是到了", "到货", "签收",
    )),
    (Intents.INVOICE_REQUEST, ("发票", "开票", "税号", "抬头")),
    (Intents.COUPON_ISSUE, ("优惠券", "券", "满减", "折扣码")),
    (Intents.ORDER_QUERY, ("订单", "我的单", "买的", "买了什么", "买了啥", "买过什么")),
]

ALL_INTENTS: tuple[Intents, ...] = tuple(Intents)

# 否定线索：必须是"否定退款"本身（绑定到"退"或显式取消），避免误伤"不想要了"这类退款理由
_NEGATION = ("不退", "不想退", "不要退", "不用退", "别退", "不退款", "取消退", "取消", "算了")
_INTERROGATIVE = ("吗", "嘛", "呢", "?", "？", "要不要", "能不能", "可不可以",
                  "是不是", "退不退", "是否", "能退", "可以退", "怎么", "为什么",
                  "什么时候", "多久", "何时", "怎么办", "什么条件", "多久到账")
# A-不-A 疑问句式（如"要不要"）：内含的否定字样是疑问的一部分，不应判为否定
_DELIBERATION = ("要不要", "能不能", "可不可以", "是不是", "退不退", "用不用", "需不需要")
_CONFIRMATION = ("确认", "确定", "是的", "好的", "可以", "对", "嗯", "退吧", "帮我退", "就这样")
# "伪确认"：字面含确认字样但实为否定/迟疑（"不确定"含"确定"），不可当确认
_NEGATED_CONFIRM = ("不确定", "不可以", "不行", "不对", "不好", "不一定", "再想想", "没想好", "说不好")
# 明确超出售后范围的线索（P0 scope 硬闸：命中→固定拒答，不跑业务 LLM）
_OUT_OF_SCOPE_CUES = ("天气", "几点", "星期几", "笑话", "写诗", "唱歌", "股票", "新闻",
                      "证券", "翻译", "数学", "你是谁", "你叫", "陪我聊", "讲个", "故事", "怎么做菜")

# 非退款意图也可能被明确否定。命中这些短语时，移除对应候选，避免
# “别转人工，我只想查物流”被关键词优先级误路由到人工。
_NEGATED_INTENT_CUES: dict[Intents, tuple[str, ...]] = {
    Intents.HUMAN_HANDOFF: ("别转人工", "不转人工", "不要人工", "不用人工"),
    Intents.PRODUCT_COMPLAINT: ("不是投诉", "不投诉", "别投诉"),
    Intents.INVOICE_REQUEST: ("不开发票", "不用发票", "不要发票"),
}
_ESCALATION_CUES = ("曝光", "媒体", "消协", "12315", "投诉到底")
_ORDER_REFERENCE_RE = re.compile(
    r"(?:订单(?:号)?\s*[:：]?\s*)([A-Za-z0-9][A-Za-z0-9-]{2,39})",
    re.IGNORECASE,
)


def _is_confirm(text: str) -> bool:
    return _has(text, _CONFIRMATION) and not _has(text, _NEGATED_CONFIRM)

_REFUND_FAMILY = {Intents.REFUND_REQUEST, Intents.RETURN_REQUEST}
_RETURN_EXACT = ("退货", "退回", "寄回")


def _has(text: str, cues: tuple[str, ...]) -> bool:
    return any(c in text for c in cues)


# ── 确认态专用 parser（P0）：高危确认门只认强确认/强取消，其余一律保持等待（fail-safe）──
class ConfirmSignal(str, Enum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    UNCLEAR = "unclear"


_STRONG_CANCEL = ("取消", "不退", "不要退", "别退", "算了", "不用了", "不办了", "不想退")
_STRONG_CONFIRM = ("确认", "确定要", "是的", "对，退", "可以退", "退吧", "帮我退", "就这样", "好的")
_HESITATE = ("不确定", "不一定", "再想想", "没想好", "不好说", "说不好", "不可以", "不行", "不对")


def parse_confirmation(text: str) -> ConfirmSignal:
    """等待确认态下解析用户回复。否定/迟疑优先于确认，避免'不确定'被当确认。"""
    t = (text or "").strip().lower()
    if _has(t, _STRONG_CANCEL):
        return ConfirmSignal.CANCEL
    if _has(t, _HESITATE):          # 迟疑/伪确认 → 不执行，重新提示
        return ConfirmSignal.UNCLEAR
    if _has(t, _STRONG_CONFIRM):
        return ConfirmSignal.CONFIRM
    return ConfirmSignal.UNCLEAR


class RuleIntentClassifier:
    """规则快路（候选召回）。命中返回 (intent, from_rule=True)；未命中返回兜底 from_rule=False。"""

    def classify(self, message: str) -> tuple[Intents, bool]:
        candidates = self.recall(message)
        if candidates:
            logger.info("intent(rule)=%s", candidates[0].value)
            return candidates[0], True
        logger.info("intent(rule-miss)")
        return Intents.GENERAL_POLICY_QUERY, False

    def recall(self, message: str) -> list[Intents]:
        """召回全部候选并去重；决策层负责处理冲突，不再让表顺序静默裁决。"""
        text = (message or "").lower()
        candidates: list[Intents] = []
        for intent, kws in _KEYWORDS:
            if any(kw in text for kw in kws) and intent not in candidates:
                candidates.append(intent)
        # “门口和驿站都没有”等话术不含“物流/快递/没收到”，仍是签收未收到的高风险表达。
        if logistics_service.reports_not_received(text) \
                and Intents.LOGISTICS_EXCEPTION not in candidates:
            candidates.append(Intents.LOGISTICS_EXCEPTION)
        # 处理关键词表中有意设置的“广召回”重叠，而不是把它们误当真实多意图。
        # “我要退款”会同时包含广召回词“要退”；有精确退款且无“退货/寄回”时，以退款为准。
        if Intents.REFUND_REQUEST in candidates and Intents.RETURN_REQUEST in candidates \
                and not _has(text, _RETURN_EXACT):
            candidates.remove(Intents.RETURN_REQUEST)
        # “申请退货”会同时命中广义词“申请退”和精确词“退货”；没有明确“退款/退钱/退费”
        # 时应保留更具体的退货意图，避免错误进入多意图澄清。
        if Intents.REFUND_REQUEST in candidates and Intents.RETURN_REQUEST in candidates \
                and _has(text, _RETURN_EXACT) \
                and not _has(text, ("退款", "退钱", "退费")):
            candidates.remove(Intents.REFUND_REQUEST)
        # “退我的订单”中的“订单”是操作对象，不是独立订单查询。
        if any(i in candidates for i in _REFUND_FAMILY) and Intents.ORDER_QUERY in candidates:
            candidates.remove(Intents.ORDER_QUERY)
        # 物流异常是物流查询的更具体子类。
        if Intents.LOGISTICS_EXCEPTION in candidates and Intents.LOGISTICS_QUERY in candidates:
            candidates.remove(Intents.LOGISTICS_QUERY)
        return candidates


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

    def classify_multi_intent(self, message: str, history=None) -> MultiIntentResult:
        """召回一轮中的多个明确意图，不用模型把多个任务强行压成一个。"""
        text = (message or "").lower().strip()
        references = tuple(dict.fromkeys(_ORDER_REFERENCE_RE.findall(message or "")))
        if len(references) > 1:
            return MultiIntentResult(
                [], True, "一轮中引用了多个订单，第一阶段仅支持同一订单", references
            )

        # 超范围仍是最高优先级硬闸，不能与业务任务混跑。
        if _has(text, _OUT_OF_SCOPE_CUES):
            result = self.classify_intent(message, history=history)
            return MultiIntentResult([result], order_references=references)

        candidates = self.rule.recall(text)
        for candidate in list(candidates):
            if _has(text, _NEGATED_INTENT_CUES.get(candidate, ())):
                candidates.remove(candidate)

        is_q = _has(text, _INTERROGATIVE)
        is_neg = _has(text, _NEGATION) and not _has(text, _DELIBERATION)
        # “别退款，只查物流”是对写意图的排除，不是一个“取消退款+查物流”组合。
        if is_neg and any(intent not in _REFUND_FAMILY for intent in candidates):
            candidates = [intent for intent in candidates if intent not in _REFUND_FAMILY]

        if not candidates:
            result = self.classify_intent(message, history=history)
            return MultiIntentResult([result], order_references=references)

        results: list[IntentResult] = []
        for candidate in candidates:
            if candidate in _REFUND_FAMILY:
                if is_neg:
                    result = self._build_result(
                        Intents.CANCEL_REFUND, source="rule", is_q=False, is_neg=True,
                        reason="多意图中退款表达被否定",
                    )
                elif is_q:
                    result = self._build_result(
                        Intents.REFUND_INQUIRY, source="rule", is_q=True, is_neg=False,
                        reason="多意图中的退款疑问",
                    )
                else:
                    result = self._build_result(
                        candidate, source="rule", is_q=False, is_neg=False,
                        reason="多意图中的退款/退货申请",
                    )
            else:
                result = self._build_result(
                    candidate, source="rule", is_q=is_q, is_neg=False,
                    reason="多意图规则召回",
                )
            if all(existing.intent != result.intent for existing in results):
                results.append(result)

        if len(results) > 3:
            return MultiIntentResult(
                results, True, "单轮意图超过三个，请拆分后重试", references
            )
        return MultiIntentResult(results, order_references=references)

    # ---- 新：结构化意图（关键词召回 → 极性 → 决策；召回不确定时带历史 defer LLM） ----
    def classify_intent(self, message: str, history=None) -> IntentResult:
        text = (message or "").lower().strip()
        is_q = _has(text, _INTERROGATIVE)
        # A-不-A 疑问（要不要/退不退…）里的"不要/不退"是疑问的一部分，不算否定
        is_neg = _has(text, _NEGATION) and not _has(text, _DELIBERATION)
        # 注：等待确认态由 handle 用 parse_confirmation 专门处理，不走通用分类（P0）

        # scope 是接入业务 LLM 前的硬闸，必须先于宽泛关键词（如“券”）判断。
        if _has(text, _OUT_OF_SCOPE_CUES):
            return IntentResult(Intents.OUT_OF_SCOPE, Polarity.NEUTRAL, ActionType.NONE,
                                Confidence.HIGH, False, "明确超出售后服务范围")

        candidates = self.rule.recall(text)
        suppressed = []
        for candidate in list(candidates):
            if _has(text, _NEGATED_INTENT_CUES.get(candidate, ())):
                candidates.remove(candidate)
                suppressed.append(candidate)

        # 明确投诉升级/媒体威胁属于安全升级信号；即使同时提到退款，也先转投诉流程。
        if Intents.PRODUCT_COMPLAINT in candidates and _has(text, _ESCALATION_CUES):
            candidates = [Intents.PRODUCT_COMPLAINT]

        # 多候选不能靠关键词表顺序决定：有 LLM 则结合历史消歧，无 LLM 则安全澄清。
        if len(candidates) > 1:
            if self.llm is not None:
                picked = self._classify_llm(text, history, candidates=candidates)
                if picked:
                    return self._build_result(picked, source="llm", is_q=is_q, is_neg=is_neg,
                                              reason="多候选冲突，LLM 结合历史消歧")
            names = ", ".join(i.value for i in candidates)
            return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                                Confidence.LOW, False, f"规则召回多个冲突候选，需澄清: {names}")

        candidate = candidates[0] if candidates else Intents.GENERAL_POLICY_QUERY
        from_rule = bool(candidates)

        # 退款族：关键词只是候选，极性决定 intent（3/4/5）
        if from_rule and candidate in _REFUND_FAMILY:
            if is_neg:   # 4. 否定优先于退款关键词
                return self._build_result(Intents.CANCEL_REFUND, source="rule", is_q=False,
                                          is_neg=True, reason="退款关键词但含否定→取消退款")
            if is_q:     # 5. 疑问不识别为 refund_request
                return self._build_result(Intents.REFUND_INQUIRY, source="rule", is_q=True,
                                          is_neg=False, reason="退款关键词但是疑问→退款咨询")
            return self._build_result(candidate, source="rule", is_q=False, is_neg=False,
                                      reason="肯定的退款/退货请求，需确认后执行")

        if from_rule:    # 其他意图：候选直接采用，标注极性（写类工单暂不强制确认，Tier2）
            polarity = (Polarity.INTERROGATIVE if is_q
                        else Polarity.NEGATIVE if is_neg else Polarity.NEUTRAL)
            return IntentResult(candidate, polarity, ActionType.NONE,
                                Confidence.HIGH, False, "规则命中")

        # 召回未命中
        if _is_confirm(text):   # 无上下文的"确认" → 意图不明，需澄清（不算确认）
            return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                                Confidence.LOW, False, "无上下文的确认表达，意图不明需澄清")
        if suppressed:
            return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                                Confidence.LOW, False, "只命中被明确否定的意图，需澄清真实诉求")
        # 召回未命中 → 语义层兜底：带对话历史让 LLM 判（多轮上下文，步骤④起步）
        if self.llm is not None:
            picked = self._classify_llm(text, history)
            if picked:
                logger.info("intent(llm)=%s", picked.value)
                return self._build_result(picked, source="llm", is_q=is_q, is_neg=is_neg,
                                          reason="LLM 语义兜底（带历史）")
        # 无 LLM/判不出：按通用咨询兜底（MEDIUM，可答疑）；LOW 只留给无上下文的"确认"
        return IntentResult(Intents.GENERAL_POLICY_QUERY, Polarity.NEUTRAL, ActionType.NONE,
                            Confidence.MEDIUM, False, "规则未命中，按通用咨询兜底")

    def _build_result(self, intent: Intents, *, source: str, is_q: bool, is_neg: bool,
                      reason: str) -> IntentResult:
        """统一派生动作字段，避免同一 intent 因分类来源不同而契约不一致。"""
        confidence = Confidence.HIGH if source == "rule" else Confidence.MEDIUM
        if intent == Intents.CANCEL_REFUND:
            return IntentResult(intent, Polarity.NEGATIVE, ActionType.CANCEL,
                                confidence, False, reason)
        if intent == Intents.REFUND_INQUIRY:
            return IntentResult(intent, Polarity.INTERROGATIVE, ActionType.QUERY,
                                confidence, False, reason)
        if intent in _REFUND_FAMILY:
            # LLM 已明确选择 request/return_request 时，按请求语义进入确认协议；
            # 真正写操作仍由 pending_action + 专用确认 parser 把关。
            return IntentResult(intent, Polarity.POSITIVE, ActionType.EXECUTE,
                                confidence, True, reason)
        polarity = (Polarity.INTERROGATIVE if is_q
                    else Polarity.NEGATIVE if is_neg else Polarity.NEUTRAL)
        return IntentResult(intent, polarity, ActionType.NONE, confidence, False, reason)

    def _classify_llm(self, message: str, history=None,
                      candidates: list[Intents] | None = None) -> Intents | None:
        from app.llm.base import Msg
        from app.llm.errors import ContextWindowExceeded
        allowed = candidates or list(ALL_INTENTS)
        system = (
            "你是售后意图分类器。结合对话历史，判断用户【最后一句】最匹配下列哪个意图，"
            "严格输出 JSON，格式为 {\"intent\": \"英文标识\"}，不要解释：\n"
            + ", ".join(i.value for i in allowed)
        )
        messages = list(history or []) + [Msg("user", message)]
        try:
            resp = self.llm.chat(system=system, messages=messages)
        except ContextWindowExceeded:
            # backstop 已压缩重试过；继续吞掉会把可用性故障伪装成普通低置信分类。
            raise
        except Exception:  # noqa: BLE001 — 分类失败不阻断主流程
            return None
        text = (resp.text or "").strip().lower()
        # 兼容旧 provider/stub 的精确枚举输出；拒绝解释文本中的模糊子串命中。
        raw_intent = text
        if text.startswith("{"):
            try:
                payload = json.loads(text)
                raw_intent = payload.get("intent") if isinstance(payload, dict) else None
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(raw_intent, str):
            return None
        raw_intent = raw_intent.strip().lower()
        return next((it for it in allowed if raw_intent == it.value), None)
