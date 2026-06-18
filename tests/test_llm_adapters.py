"""M4 LLM 多 provider 适配层（ADR-11）。

用假 client 离线验证：请求整形 + 响应归一化 + 工具调用映射 + registry 选择。
不调真实 API、不需装 anthropic/openai（SDK 延迟导入）。
"""
from types import SimpleNamespace as NS

import pytest

from app.core.config import Settings
from app.core.exceptions import LLMError
from app.llm.base import Msg, ToolCall, ToolSpec
from app.llm.claude_adapter import ClaudeAdapter, _to_anthropic_messages
from app.llm.openai_compat_adapter import OpenAICompatAdapter, _to_openai_messages
from app.llm.registry import build_llm_client

SPEC = ToolSpec("get_order_detail", "查订单",
                {"type": "object", "properties": {"order_id": {"type": "integer"}}})


# ---------- Claude ----------
class _FakeAnthropic:
    def __init__(self, resp):
        self._resp, self.captured = resp, None

    @property
    def messages(self):
        return self

    def create(self, **kw):
        self.captured = kw
        return self._resp


def _claude_resp():
    return NS(
        content=[NS(type="text", text="您好"),
                 NS(type="tool_use", id="tu1", name="get_order_detail", input={"order_id": 1})],
        stop_reason="tool_use",
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=2),
    )


def test_claude_parse_and_request():
    fake = _FakeAnthropic(_claude_resp())
    ad = ClaudeAdapter(model="claude-opus-4-8", client=fake)
    r = ad.chat(system="sys", messages=[Msg("user", "退款")], tools=[SPEC])
    assert r.text == "您好"
    assert r.stop_reason == "tool_use"
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "get_order_detail"
    assert r.tool_calls[0].arguments == {"order_id": 1}
    assert r.usage.total_tokens == 15 and r.usage.cache_read_tokens == 2
    # 请求整形：system、adaptive thinking、tools 映射
    assert fake.captured["system"] == "sys"
    assert fake.captured["thinking"] == {"type": "adaptive"}
    assert fake.captured["tools"][0]["input_schema"] == SPEC.input_schema


def test_claude_message_mapping_tool_roundtrip():
    msgs = [
        Msg("user", "退款"),
        Msg("assistant", "好的", tool_calls=[ToolCall("tu1", "get_order_detail", {"order_id": 1})]),
        Msg("tool", '{"ok":true}', tool_call_id="tu1"),
    ]
    out = _to_anthropic_messages(msgs)
    assert out[1]["content"][1]["type"] == "tool_use"
    assert out[2]["content"][0]["type"] == "tool_result"
    assert out[2]["content"][0]["tool_use_id"] == "tu1"


# ---------- OpenAI 兼容 ----------
class _FakeOpenAI:
    def __init__(self, resp):
        self._resp, self.captured = resp, None
        outer = self

        class _C:
            def create(self, **kw):
                outer.captured = kw
                return outer._resp
        self.chat = NS(completions=_C())


def _openai_resp():
    msg = NS(content="hi", tool_calls=[
        NS(id="c1", function=NS(name="get_order_detail", arguments='{"order_id":2}'))])
    return NS(choices=[NS(message=msg, finish_reason="tool_calls")],
              usage=NS(prompt_tokens=7, completion_tokens=3, total_tokens=10))


def test_openai_parse_and_request():
    fake = _FakeOpenAI(_openai_resp())
    ad = OpenAICompatAdapter(model="deepseek-chat", client=fake)
    r = ad.chat(system="sys", messages=[Msg("user", "退款")], tools=[SPEC])
    assert r.text == "hi"
    assert r.stop_reason == "tool_use"        # finish_reason tool_calls → tool_use
    assert r.tool_calls[0].arguments == {"order_id": 2}
    assert r.usage.total_tokens == 10
    # 请求整形：system 在首条、tools 映射为 function
    assert fake.captured["messages"][0] == {"role": "system", "content": "sys"}
    assert fake.captured["tools"][0]["type"] == "function"
    assert fake.captured["tools"][0]["function"]["name"] == "get_order_detail"


def test_openai_message_mapping_tool_roundtrip():
    msgs = [
        Msg("assistant", "好的", tool_calls=[ToolCall("c1", "get_order_detail", {"order_id": 2})]),
        Msg("tool", '{"ok":true}', tool_call_id="c1"),
    ]
    out = _to_openai_messages("", msgs)
    assert out[0]["tool_calls"][0]["function"]["name"] == "get_order_detail"
    assert out[1] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}


# ---------- registry ----------
def test_registry_picks_adapter_by_provider():
    assert type(build_llm_client(Settings(llm_provider="claude"))).__name__ == "ClaudeAdapter"
    assert type(build_llm_client(Settings(llm_provider="deepseek"))).__name__ == "OpenAICompatAdapter"
    assert type(build_llm_client(Settings(llm_provider="qwen"))).__name__ == "OpenAICompatAdapter"
    assert type(build_llm_client(Settings(llm_provider="gpt"))).__name__ == "OpenAICompatAdapter"
    assert type(build_llm_client(Settings(llm_provider="stub"))).__name__ == "StubLLMClient"
    with pytest.raises(LLMError):
        build_llm_client(Settings(llm_provider="nonsense"))
