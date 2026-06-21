"""结构化意图识别：关键词召回 + 极性判定。覆盖否定/疑问/确认/无上下文。

要求（见对话）：① Intent Enum ② IntentResult 六字段 ③ 关键词只召回不直接触发
④ 否定 > 退款关键词 ⑤ 疑问 ≠ refund_request ⑥ refund_confirmation 仅在 waiting_user_confirm。
"""
from app.agent.intent_classifier import (
    ActionType,
    Confidence,
    HybridIntentClassifier,
    IntentResult,
    Intents,
    Polarity,
)
from app.agent.state_machine import States

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


def test_confirmation_only_in_waiting_state():
    # 确认，帮我退吧 + 等待确认态 → refund_confirmation
    r = clf.classify_intent("确认，帮我退吧", state=States.WAITING_USER_CONFIRM)
    assert r.intent == Intents.REFUND_CONFIRMATION
    assert r.action_type == ActionType.CONFIRM


def test_bare_confirmation_without_context_is_not_confirmation():
    # 没有上下文只说"确认" → 不能识别为 refund_confirmation，低置信需澄清
    r = clf.classify_intent("确认")
    assert r.intent != Intents.REFUND_CONFIRMATION
    assert r.confidence == Confidence.LOW


def test_uncertain_not_confirmation_in_waiting_state():
    # "不确定"含"确定"子串，但是迟疑/否定，绝不能当确认（截图实测 bug）
    r = clf.classify_intent("不确定", state=States.WAITING_USER_CONFIRM)
    assert r.intent != Intents.REFUND_CONFIRMATION
    # 同类伪确认
    for t in ("不可以", "不对", "不行"):
        assert clf.classify_intent(t, state=States.WAITING_USER_CONFIRM).intent != Intents.REFUND_CONFIRMATION


def test_intents_is_str_enum_backward_compatible():
    # str-Enum：与字符串比较/集合成员仍成立（不破坏既有代码）
    assert Intents.REFUND_REQUEST == "refund_request"
    assert "refund_request" in {Intents.REFUND_REQUEST}
