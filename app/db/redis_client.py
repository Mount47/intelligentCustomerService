"""Redis 连接与健康探针。连接异常有处理（§13）。

后续用途：短期防抖锁（SETNX，§9.2）、限流等。M1 只提供 client + 探针。
"""
from __future__ import annotations

import redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_settings = get_settings()

# decode_responses：返回 str 而非 bytes，省去手动解码
redis_client = redis.Redis.from_url(_settings.redis_url, decode_responses=True)


def check_redis() -> bool:
    """健康探针：Redis 是否可达。失败不抛，返回 False。"""
    try:
        return bool(redis_client.ping())
    except Exception as exc:  # noqa: BLE001 — 探针刻意吞异常
        logger.warning("Redis health check failed: %s", exc)
        return False
