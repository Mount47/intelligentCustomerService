"""入口限流：Redis 固定窗口计数（§9.2 高并发前置闸）。

为什么要它：异步架构里 POST 只入队就返回，突发流量会把队列灌爆、把下游 LLM 成本打穿。
限流是接入层的削峰前置闸——超额请求在入队前就被挡掉，保护后端与成本。

设计：
- 固定窗口 `INCR + EXPIRE`（window 内累计，超过 limit 拒绝）。简单、原子够用，不上滑窗的复杂度。
- **fail-open**：Redis 不可达时放行（同 ADR-4——Redis 只担非正确性职责，挂了不连坐主流程）。
- 限流是流控不是正确性闸：宁可偶尔放过，也不能因 Redis 抖动把正常请求误杀。
"""
from __future__ import annotations

import time

from app.core.logging import get_logger
from app.db.redis_client import redis_client

logger = get_logger(__name__)


def allow_request(bucket: str, limit: int, window_sec: int = 60, client=None) -> tuple[bool, int]:
    """固定窗口限流。返回 (是否放行, 当前窗口内计数)。

    limit<=0 视为不限流（功能开关）。Redis 异常 → fail-open 放行。
    """
    if limit <= 0:
        return True, 0
    cli = client if client is not None else redis_client
    window = int(time.time()) // window_sec          # 当前窗口编号
    key = f"ratelimit:{bucket}:{window}"
    try:
        count = cli.incr(key)
        if count == 1:                                # 本窗口首次 → 设过期，窗口滚动自动清零
            cli.expire(key, window_sec)
        return count <= limit, int(count)
    except Exception as exc:  # noqa: BLE001 — 限流不可用不该挡正常流量
        logger.warning("rate limit check failed (fail-open): %s", exc)
        return True, 0
