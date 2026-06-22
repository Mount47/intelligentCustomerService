"""结构化意图识别：关键词召回 + 极性判定。覆盖否定/疑问/确认/无上下文。

要求（见对话）：① Intent Enum ② IntentResult 六字段 ③ 关键词只召回不直接触发
④ 否定 > 退款关键词 ⑤ 疑问 ≠ refund_request ⑥ refund_confirmation 仅在 waiting_user_confirm。
"""
from app.agent.intent_classifier import (
    ActionType,
    Confidence,
    ConfirmSignal,
    HybridIntentClassifier,
    IntentResult,
    Intents,
    Polarity,
    parse_confirmation,
)

clf = HybridIntentClassifier()   # 无 llm：测确定性规则+极性路径


def test_intent_result_has_six_fields():
    r = clf.classify_intent("我要退款")
    for f in ("intent", "polarity", "action_type", "confidence",
              "requires_confirmation", "reason"):
        assert hasattr(r, f)
    assert isinstance(r, IntentResult)
    assert isinstance(r.intent, Intents)          # 用 Enum，不是裸字符串


def test_negation_beats_refund_keyword():
    # 我不想退款了 → 否定优先 → cancel_refund，绝不 refund_request
    r = clf.classify_intent("我不想退款了")
    assert r.intent == Intents.CANCEL_REFUND
    assert r.polarity == Polarity.NEGATIVE
    assert r.action_type == ActionType.CANCEL
    assert r.requires_confirmation is False


def test_deliberation_question_not_refund_request():
    # 我要不要退款 → 疑问 → refund_inquiry
    r = clf.classify_intent("我要不要退款")
    assert r.intent == Intents.REFUND_INQUIRY
    assert r.polarity == Polarity.INTERROGATIVE


def test_can_i_refund_is_inquiry_not_request():
    # 能退款吗 → 疑问 → refund_inquiry，不触发执行
    r = clf.classify_intent("能退款吗")
    assert r.intent == Intents.REFUND_INQUIRY
    assert r.action_type == ActionType.QUERY


def test_affirmative_refund_is_request_requires_confirm():
    # 我要退款 → 肯定 → refund_request，需确认（关键词不直接触发建草稿）
    r = clf.classify_intent("我要退款")
    assert r.intent == Intents.REFUND_REQUEST
    assert r.polarity == Polarity.POSITIVE
    assert r.action_type == ActionType.EXECUTE
    assert r.requires_confirmation is True


def test_parse_confirmation_signals():
    # 确认态专用 parser（P0）：强确认/强取消/迟疑
    assert parse_confirmation("确认，帮我退吧") == ConfirmSignal.CONFIRM
    assert parse_confirmation("好的") == ConfirmSignal.CONFIRM
    assert parse_confirmation("算了不退了") == ConfirmSignal.CANCEL
    assert parse_confirmation("取消") == ConfirmSignal.CANCEL


def test_out_of_scope_detected():
    # 明确超范围 → out_of_scope（供 handle 做 scope 硬闸）
    assert clf.classify_intent("今天天气怎么样").intent == Intents.OUT_OF_SCOPE
    assert clf.classify_intent("给我讲个笑话").intent == Intents.OUT_OF_SCOPE


def test_bare_confirmation_without_context_is_not_confirmation():
    # 没有上下文只说"确认" → 不能识别为 refund_confirmation，低置信需澄清
    r = clf.classify_intent("确认")
    assert r.intent != Intents.REFUND_CONFIRMATION
    assert r.confidence == Confidence.LOW


def test_uncertain_not_confirmation():
    # "不确定"含"确定"子串，但是迟疑 → parse_confirmation 判 UNCLEAR（fail-safe，不执行）
    for t in ("不确定", "不可以", "不对", "不行", "再想想"):
        assert parse_confirmation(t) == ConfirmSignal.UNCLEAR


def test_colloquial_refund_recall():
    """口语化退款/退货要召回到退款技能；'退X订单'不被订单查询抢走（修退款召回）。"""
    for m in ("我想退一件衣服", "我想退这件衣服", "我想退我的订单", "我买的衣服想退", "退一下订单"):
        r = clf.classify_intent(m)
        assert r.intent in (Intents.REFUND_REQUEST, Intents.RETURN_REQUEST), f"{m} -> {r.intent.value}"
    # 退款精确词仍优先判 refund，不被广召回抢成 return
    assert clf.classify_intent("我要退款").intent == Intents.REFUND_REQUEST
    # 纯查询不被误召回为退款
    assert clf.classify_intent("帮我查下我的订单").intent == Intents.ORDER_QUERY


def test_llm_defer_uses_history_on_recall_miss():
    """步骤④：规则召回不到 → 带对话历史 defer LLM 判意图（多轮语义）。"""
    from app.llm.base import LLMResponse, Msg, Usage

    captured = {}

    class FakeLLM:
        model_name = "fake"

        def chat(self, *, system, messages, tools=None, stream=False):
            captured["messages"] = messages
            return LLMResponse(text="return_request", stop_reason="end_turn", usage=Usage())

    clf2 = HybridIntentClassifier(llm=FakeLLM())
    hist = [Msg("user", "我买了件衣服"), Msg("assistant", "好的")]
    r = clf2.classify_intent("这个我用不上了想处理掉", history=hist)   # 无关键词→miss→defer
    assert r.intent == Intents.RETURN_REQUEST
    assert r.reason.startswith("LLM 语义兜底")
    # 历史被带上 + 当前消息在最后
    assert captured["messages"][0].content == "我买了件衣服"
    assert captured["messages"][-1].content.endswith("处理掉")


def test_intents_is_str_enum_backward_compatible():
    # str-Enum：与字符串比较/集合成员仍成立（不破坏既有代码）
    assert Intents.REFUND_REQUEST == "refund_request"
    assert "refund_request" in {Intents.REFUND_REQUEST}
