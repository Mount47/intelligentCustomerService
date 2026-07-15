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
from app.llm.errors import ContextWindowExceeded
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
        if isinstance(self._resp, BaseException):
            raise self._resp
        return self._resp


def _claude_resp():
    return NS(
        content=[NS(type="text", text="您好"),
                 NS(type="tool_use", id="tu1", name="get_order_detail", input={"order_id": 1})],
        stop_reason="tool_use",
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=2),
    )


def test_build_llm_client_model_override():
    """裁判换模型：同 provider/key/base_url 下，model 参数覆盖默认模型名。"""
    s = Settings(llm_provider="qwen", llm_model="qwen-plus",
                 openai_api_key="x", openai_base_url="http://x")
    assert build_llm_client(s).model_name == "qwen-plus"
    assert build_llm_client(s, model="qwen-max").model_name == "qwen-max"


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


def test_claude_normalizes_context_window_error():
    with pytest.raises(ContextWindowExceeded):
        ClaudeAdapter(
            model="claude", client=_FakeAnthropic(ValueError("prompt is too long: 200001 tokens")),
        ).chat(system="sys", messages=[Msg("user", "x")])


# ---------- OpenAI 兼容 ----------
class _FakeOpenAI:
    def __init__(self, resp):
        self._resp, self.captured = resp, None
        outer = self

        class _C:
            def create(self, **kw):
                outer.captured = kw
                if isinstance(outer._resp, BaseException):
                    raise outer._resp
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


def test_openai_normalizes_context_window_error_code():
    exc = ValueError("bad request")
    exc.code = "context_length_exceeded"
    with pytest.raises(ContextWindowExceeded):
        OpenAICompatAdapter(model="qwen", client=_FakeOpenAI(exc)).chat(
            system="sys", messages=[Msg("user", "x")],
        )


# ---------- registry ----------
def _adapter(provider):
    # 关熔断仍保留上下文 backstop；检查其内层 provider adapter 选型。
    return build_llm_client(Settings(llm_provider=provider, circuit_breaker_enabled=False))


def test_registry_picks_adapter_by_provider():
    assert type(_adapter("claude")._client).__name__ == "ClaudeAdapter"
    assert type(_adapter("deepseek")._client).__name__ == "OpenAICompatAdapter"
    assert type(_adapter("qwen")._client).__name__ == "OpenAICompatAdapter"
    assert type(_adapter("gpt")._client).__name__ == "OpenAICompatAdapter"
    assert type(_adapter("stub")._client).__name__ == "StubLLMClient"
    with pytest.raises(LLMError):
        build_llm_client(Settings(llm_provider="nonsense", circuit_breaker_enabled=False))


def test_registry_wraps_with_circuit_breaker_by_default():
    # 最外层是上下文 backstop，内层默认开启熔断；model_name 一路透传。
    c = build_llm_client(Settings(llm_provider="stub"))
    assert type(c).__name__ == "ContextBackstopLLMClient"
    assert type(c._client).__name__ == "CircuitBreakerLLMClient"
