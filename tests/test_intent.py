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


def test_intents_is_str_enum_backward_compatible():
    # str-Enum：与字符串比较/集合成员仍成立（不破坏既有代码）
    assert Intents.REFUND_REQUEST == "refund_request"
    assert "refund_request" in {Intents.REFUND_REQUEST}
