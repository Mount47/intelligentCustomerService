"""LLM 熔断降级：三态转移 + 快速失败 + fallback 无缝降级 + 恢复。"""
import threading

import pytest

from app.llm.base import LLMResponse
from app.llm.circuit_breaker import CircuitBreaker, CircuitBreakerLLMClient, CircuitOpenError
from app.llm.errors import ContextWindowExceeded


class _Clock:
    """可手动推进的假时钟，做确定性时间测试（不 sleep）。"""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Primary:
    """可控主客户端：fail_until 次内抛异常，之后正常。计调用次数。"""
    model_name = "primary"

    def __init__(self, fail_until=10**9):
        self.calls = 0
        self.fail_until = fail_until

    def chat(self, *, system, messages, tools=None, stream=False):
        self.calls += 1
        if self.calls <= self.fail_until:
            raise RuntimeError("provider down")
        return LLMResponse(text="primary-ok")


class _Fallback:
    model_name = "fallback"

    def __init__(self):
        self.calls = 0

    def chat(self, *, system, messages, tools=None, stream=False):
        self.calls += 1
        return LLMResponse(text="fallback-ok")


# ---- 纯逻辑（CircuitBreaker）----

def test_opens_after_fail_max():
    cb = CircuitBreaker(fail_max=3, reset_timeout=10, clock=_Clock())
    cb.record_failure(); cb.record_failure()
    assert cb.allow() is True            # 还没到阈值
    cb.record_failure()
    assert cb.state == "open" and cb.allow() is False


def test_half_open_after_cooldown_then_recover():
    clk = _Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=clk)
    cb.record_failure()
    assert cb.allow() is False           # OPEN 冷却内
    clk.t = 10                           # 冷却到点
    assert cb.allow() is True and cb.state == "half_open"
    cb.record_success()
    assert cb.state == "closed"


def test_half_open_failure_reopens():
    clk = _Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=clk)
    cb.record_failure(); clk.t = 10; cb.allow()   # → half_open
    cb.record_failure()
    assert cb.state == "open"


def test_half_open_allows_exactly_one_probe_under_concurrency():
    clk = _Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=clk)
    cb.record_failure()
    clk.t = 10
    n = 32
    barrier = threading.Barrier(n)
    results = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        allowed = cb.allow()
        with results_lock:
            results.append(allowed)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(results) == 1
    assert cb.state == "half_open"
    cb.record_success()
    assert cb.state == "closed"


def test_late_success_from_closed_request_cannot_close_open_circuit():
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=_Clock())
    assert cb.allow() is True   # 请求 A 已放行
    assert cb.allow() is True   # 请求 B 也在 CLOSED 态放行
    cb.record_failure()         # B 先失败，熔断打开
    cb.record_success()         # A 后成功；这是旧结果，不能误关当前熔断
    assert cb.state == "open"
    assert cb.allow() is False


def test_stale_closed_success_cannot_impersonate_half_open_probe():
    clk = _Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=clk)
    stale = cb.acquire()
    failing = cb.acquire()
    cb.record_failure(failing)
    assert cb.state == "open"

    clk.t = 10
    probe = cb.acquire()
    assert probe is not None and probe.probe is True
    cb.record_success(stale)       # 熔断前旧请求迟到成功
    assert cb.state == "half_open"
    assert cb.allow() is False     # 唯一探针仍在途，没有提前放开流量

    cb.record_success(probe)
    assert cb.state == "closed"


def test_success_resets_failures():
    cb = CircuitBreaker(fail_max=3, clock=_Clock())
    cb.record_failure(); cb.record_failure()
    cb.record_success()
    assert cb.failures == 0 and cb.state == "closed"


# ---- 包装客户端（CircuitBreakerLLMClient）----

def _call(c):
    return c.chat(system="s", messages=[])


def test_no_fallback_fast_fails_and_stops_calling_primary():
    primary = _Primary()                 # 一直失败
    c = CircuitBreakerLLMClient(primary, fallback=None, fail_max=3, reset_timeout=100)
    for _ in range(5):
        with pytest.raises(CircuitOpenError):
            _call(c)
    assert primary.calls == 3            # 熔断后不再打主 provider（防雪崩）


def test_fallback_keeps_serving_during_outage():
    primary = _Primary()                 # 一直失败
    fb = _Fallback()
    c = CircuitBreakerLLMClient(primary, fallback=fb, fail_max=2, reset_timeout=100)
    outs = [_call(c).text for _ in range(5)]
    assert outs == ["fallback-ok"] * 5   # 始终有响应：依旧可用
    assert primary.calls == 2            # 熔断后连主都不试了，全走 fallback


def test_closed_passes_through_on_success():
    primary = _Primary(fail_until=0)     # 从不失败
    c = CircuitBreakerLLMClient(primary, fail_max=3)
    assert _call(c).text == "primary-ok"


def test_context_window_error_does_not_count_as_provider_failure():
    class TooLong:
        model_name = "too-long"
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            raise ContextWindowExceeded("too long")

    primary = TooLong()
    client = CircuitBreakerLLMClient(primary, fail_max=1)
    for _ in range(2):
        with pytest.raises(ContextWindowExceeded):
            _call(client)
    assert primary.calls == 2
    assert client._breaker.state == "closed"
    assert client._breaker.failures == 0


def test_context_error_on_half_open_probe_does_not_wedge_probe_slot():
    clk = _Clock()
    cb = CircuitBreaker(fail_max=1, reset_timeout=10, clock=clk)
    cb.record_failure()
    clk.t = 10
    probe = cb.acquire()
    assert probe is not None and probe.probe is True

    cb.discard(probe)
    assert cb.state == "open"
    replacement = cb.acquire()
    assert replacement is not None and replacement.probe is True
