"""上下文窗口超限：一次有效压缩重试，禁止相同输入盲重试。"""
import pytest

from app.llm.base import LLMResponse, Msg
from app.llm.context_backstop import ContextBackstopLLMClient, emergency_compact
from app.llm.errors import ContextWindowExceeded


class _TooLongThenOK:
    model_name = "scripted"

    def __init__(self, fail_count=1):
        self.fail_count = fail_count
        self.calls = []

    def chat(self, *, system, messages, tools=None, stream=False):
        self.calls.append(list(messages))
        if len(self.calls) <= self.fail_count:
            raise ContextWindowExceeded("too long")
        return LLMResponse(text="ok")


def test_emergency_compaction_keeps_current_tool_roundtrip_and_drops_old_history():
    messages = [
        Msg("user", "旧问题" * 100),
        Msg("assistant", "旧回答" * 100),
        Msg("user", "当前问题"),
        Msg("assistant", "", tool_calls=[]),
        Msg("tool", "结果" * 500, tool_call_id="t1"),
    ]
    compacted = emergency_compact(messages, char_budget=300)
    assert [m.role for m in compacted] == ["user", "assistant", "tool"]
    assert compacted[0].content == "当前问题"
    assert compacted[-1].tool_call_id == "t1"
    assert sum(len(m.content) for m in compacted) < sum(len(m.content) for m in messages)
    assert sum(len(m.content) for m in compacted) <= 300


def test_context_backstop_retries_once_with_shorter_input():
    inner = _TooLongThenOK()
    client = ContextBackstopLLMClient(inner, char_budget=200)
    response = client.chat(
        system="sys",
        messages=[Msg("user", "旧历史" * 200), Msg("assistant", "旧回答" * 200),
                  Msg("user", "当前问题")],
    )
    assert response.text == "ok"
    assert len(inner.calls) == 2
    assert len(inner.calls[1]) == 1
    assert inner.calls[1][0].content == "当前问题"


def test_context_backstop_second_overflow_is_not_retried_again():
    inner = _TooLongThenOK(fail_count=10)
    client = ContextBackstopLLMClient(inner, char_budget=200)
    with pytest.raises(ContextWindowExceeded):
        client.chat(
            system="sys",
            messages=[Msg("user", "旧历史" * 200), Msg("user", "当前问题")],
        )
    assert len(inner.calls) == 2


def test_context_backstop_does_not_retry_identical_short_input():
    inner = _TooLongThenOK(fail_count=10)
    client = ContextBackstopLLMClient(inner, char_budget=200)
    with pytest.raises(ContextWindowExceeded):
        client.chat(system="sys", messages=[Msg("user", "短输入")])
    assert len(inner.calls) == 1
