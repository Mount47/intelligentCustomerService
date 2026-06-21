"""LLM-as-Judge 第二层单测：桩裁判确定性 + 真实裁判 JSON 解析容错 + 盲评。"""
from app.eval.judge import (
    DIMENSIONS,
    LLMJudge,
    StubJudge,
    _parse_scores,
    build_judge,
    expected_summary,
)
from app.llm.base import LLMResponse, Usage


_GOOD = {"intent_ok": True, "skill_ok": True, "state_ok": True,
         "handoff_ok": True, "forbidden_hits": []}
_BAD = {"intent_ok": False, "skill_ok": False, "state_ok": False,
        "handoff_ok": False, "forbidden_hits": ["保证退款"]}


def test_stub_judge_good_beats_bad():
    j = StubJudge()
    good = j.score({"user_message": "x"}, "已为您创建退款申请", _GOOD)
    bad = j.score({"user_message": "x"}, "保证退款", _BAD)
    assert good["overall"] > bad["overall"]
    assert all(d in good for d in DIMENSIONS)
    assert good["parse_ok"] is True


def test_stub_judge_penalizes_forbidden():
    """说了禁语 → 合规分被压低。"""
    j = StubJudge()
    r = j.score({"user_message": "x"}, "我保证全额退款", _BAD)
    assert r["compliance"] <= 2


def test_stub_judge_deterministic():
    j = StubJudge()
    a = j.score({"user_message": "x"}, "ok", _GOOD)
    b = j.score({"user_message": "x"}, "ok", _GOOD)
    assert a == b   # 确定性，CI 可复现


def test_parse_scores_handles_fenced_json():
    text = '```json\n{"accuracy":5,"helpfulness":4,"compliance":5,"tone":4,"reason":"好"}\n```'
    s = _parse_scores(text)
    assert s["parse_ok"] is True
    assert s["accuracy"] == 5 and s["overall"] == 4.5


def test_parse_scores_clamps_and_survives_garbage():
    assert _parse_scores("模型今天罢工了")["parse_ok"] is False       # 非 JSON → 标记失败
    assert _parse_scores('{"accuracy":9,"helpfulness":0,"compliance":3,"tone":3,"reason":""}'
                         )["accuracy"] == 5  # 超界钳到 5


def test_expected_summary_is_reference_anchor():
    s = expected_summary({"notes": "高风险退款", "should_handoff": True, "adversarial": True})
    assert "高风险退款" in s and "转人工" in s and "对抗" in s


def test_judge_prompt_is_blind_to_model_identity():
    """盲评：裁判 prompt 不得出现被评模型的名字。"""
    captured = {}

    class SpyLLM:
        model_name = "qwen-plus"
        def chat(self, *, system, messages, tools=None, stream=False):
            captured["system"] = system
            captured["user"] = messages[0].content
            return LLMResponse(text='{"accuracy":5,"helpfulness":5,"compliance":5,"tone":5,"reason":"ok"}',
                               usage=Usage())

    LLMJudge(SpyLLM()).score({"user_message": "我要退款", "notes": "低风险"}, "已受理", _GOOD)
    blob = captured["system"] + captured["user"]
    assert "qwen" not in blob.lower()       # 生产者身份不得泄漏给裁判


def test_build_judge_default_is_stub():
    assert isinstance(build_judge(real=False), StubJudge)
