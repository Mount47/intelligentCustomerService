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
from dataclasses import dataclass
from threading import Lock

from app.core.exceptions import LLMError
from app.core.logging import get_logger
from app.llm.base import LLMClient, LLMResponse, Msg, ToolSpec

logger = get_logger(__name__)


class CircuitOpenError(LLMError):
    """熔断打开期间快速失败（无 fallback 时抛出）。"""


@dataclass(frozen=True)
class CircuitPermit:
    """一次主 provider 调用的许可；generation 防迟到结果污染新一代状态。"""

    generation: int
    probe: bool = False


class CircuitBreaker:
    """三态熔断的纯逻辑（不含 LLM，便于单测）。clock 可注入做确定性时间测试。"""

    def __init__(self, fail_max: int = 3, reset_timeout: float = 30.0, clock=time.monotonic):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._clock = clock
        self.failures = 0
        self.state = "closed"
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._generation = 0
        self._lock = Lock()

    def acquire(self) -> CircuitPermit | None:
        """获取调用许可。OPEN 冷却到点只发一个带代次的 HALF_OPEN 探针。"""
        with self._lock:
            if self.state == "closed":
                return CircuitPermit(self._generation)
            if self.state == "open":
                if self._clock() - self._opened_at >= self.reset_timeout:
                    # 状态切换与发放探针在同一临界区；其他线程只会看到 half_open 并拒绝。
                    self.state = "half_open"
                    self._probe_in_flight = True
                    return CircuitPermit(self._generation, probe=True)
                return None
            return None  # half_open 已有探针在途，其余请求快速降级

    def allow(self) -> bool:
        """兼容纯逻辑调用；生产包装器使用 acquire() 并回传 permit。"""
        return self.acquire() is not None

    def record_success(self, permit: CircuitPermit | None = None) -> None:
        with self._lock:
            if permit is not None:
                if permit.generation != self._generation:
                    return
                if permit.probe:
                    if self.state != "half_open" or not self._probe_in_flight:
                        return
                elif self.state != "closed":
                    return
            if self.state == "half_open":
                self.failures = 0
                self.state = "closed"
                self._probe_in_flight = False
                self._generation += 1  # 探针代结束，迟到结果全部失效
                logger.info("circuit breaker → closed (probe recovered)")
            elif self.state == "closed":
                self.failures = 0
            # OPEN 表示其他并发请求已触发熔断；忽略此前放行请求的迟到成功，不能误关熔断。

    def record_failure(self, permit: CircuitPermit | None = None) -> None:
        with self._lock:
            if permit is not None:
                if permit.generation != self._generation:
                    return
                if permit.probe:
                    if self.state != "half_open" or not self._probe_in_flight:
                        return
                elif self.state != "closed":
                    return
            if self.state == "open":
                # 忽略熔断前已放行请求的迟到失败，不延长当前冷却窗口。
                return
            self.failures += 1
            # HALF_OPEN 下唯一探针失败，或 CLOSED 下累计到阈值 → 打开
            if self.state == "half_open" or self.failures >= self.fail_max:
                logger.warning("circuit breaker → open (failures=%d)", self.failures)
                self.state = "open"
                self._opened_at = self._clock()
                self._probe_in_flight = False
                self._generation += 1  # 新熔断代，之前所有在途许可失效


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
        permit = self._breaker.acquire()
        if permit is None:                                  # OPEN/HALF_OPEN 非探针 → 不碰主 provider
            return self._degrade(system, messages, tools, stream)
        try:
            resp = self._primary.chat(system=system, messages=messages, tools=tools, stream=stream)
            self._breaker.record_success(permit)
            return resp
        except Exception as exc:  # noqa: BLE001 — 任何 provider 异常都计入熔断
            self._breaker.record_failure(permit)
            logger.warning("LLM primary call failed: %s", exc)
            return self._degrade(system, messages, tools, stream)
