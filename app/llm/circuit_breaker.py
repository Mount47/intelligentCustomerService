"""LLM provider 熔断降级（高并发可用性 §13）。

为什么要它：LLM 是整条链路里最慢、最易抖动的外部依赖，也是单点。provider 一旦故障，
若每个请求都去硬调 + 超时 + 重试，会把 worker 池全部堵死（雪崩）。熔断器在连续失败后
**快速失败 / 切降级**，不再把请求往死掉的 provider 上灌。

设计（标准三态熔断）：
- CLOSED（正常）：放行；连续失败达 fail_max → 跳到 OPEN。
- OPEN（熔断）：reset_timeout 内**直接快速失败/走 fallback**，不碰主 provider。
- HALF_OPEN（试探）：冷却后放一个试探请求；成功→CLOSED，失败→回 OPEN。

复用 `LLMClient` 抽象（ADR-7/11）：`CircuitBreakerLLMClient` 包住任意适配器，对外仍是 LLMClient，
agent loop 无感。可选 fallback（同 provider 换更稳/更便宜的模型）实现**无缝降级、依旧可用**。
"""
from __future__ import annotations

import time

from app.core.exceptions import LLMError
from app.core.logging import get_logger
from app.llm.base import LLMClient, LLMResponse, Msg, ToolSpec

logger = get_logger(__name__)


class CircuitOpenError(LLMError):
    """熔断打开期间快速失败（无 fallback 时抛出）。"""


class CircuitBreaker:
    """三态熔断的纯逻辑（不含 LLM，便于单测）。clock 可注入做确定性时间测试。"""

    def __init__(self, fail_max: int = 3, reset_timeout: float = 30.0, clock=time.monotonic):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._clock = clock
        self.failures = 0
        self.state = "closed"
        self._opened_at = 0.0

    def allow(self) -> bool:
        """能否尝试真实调用。OPEN 且冷却已过 → 转 HALF_OPEN 放一个试探。"""
        if self.state == "open":
            if self._clock() - self._opened_at >= self.reset_timeout:
                self.state = "half_open"
                return True
            return False
        return True   # closed / half_open

    def record_success(self) -> None:
        self.failures = 0
        if self.state != "closed":
            logger.info("circuit breaker → closed (recovered)")
        self.state = "closed"

    def record_failure(self) -> None:
        self.failures += 1
        # HALF_OPEN 下试探失败，或 CLOSED 下累计到阈值 → 打开
        if self.state == "half_open" or self.failures >= self.fail_max:
            if self.state != "open":
                logger.warning("circuit breaker → open (failures=%d)", self.failures)
            self.state = "open"
            self._opened_at = self._clock()


class CircuitBreakerLLMClient:
    """给主 LLMClient 套熔断。fallback 存在则降级到它，否则 OPEN 时抛 CircuitOpenError。"""

    def __init__(self, primary: LLMClient, fallback: LLMClient | None = None,
                 fail_max: int = 3, reset_timeout: float = 30.0):
        self._primary = primary
        self._fallback = fallback
        self._breaker = CircuitBreaker(fail_max=fail_max, reset_timeout=reset_timeout)
        self.model_name = getattr(primary, "model_name", "")

    def _degrade(self, system, messages, tools, stream) -> LLMResponse:
        if self._fallback is not None:
            logger.warning("LLM degraded to fallback=%s", getattr(self._fallback, "model_name", "?"))
            return self._fallback.chat(system=system, messages=messages, tools=tools, stream=stream)
        raise CircuitOpenError("LLM 服务暂不可用（熔断中）")

    def chat(self, *, system: str, messages: list[Msg],
             tools: list[ToolSpec] | None = None, stream: bool = False) -> LLMResponse:
        if not self._breaker.allow():                       # OPEN 冷却内 → 不碰主 provider
            return self._degrade(system, messages, tools, stream)
        try:
            resp = self._primary.chat(system=system, messages=messages, tools=tools, stream=stream)
            self._breaker.record_success()
            return resp
        except Exception as exc:  # noqa: BLE001 — 任何 provider 异常都计入熔断
            self._breaker.record_failure()
            logger.warning("LLM primary call failed: %s", exc)
            return self._degrade(system, messages, tools, stream)
