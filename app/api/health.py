"""健康检查：GET /health。

返回整体状态 + 各组件（DB/Redis）。组件不可达时整体 degraded 并返回 503，
便于 compose/编排做就绪探针；进程本身存活仍返回 JSON（不抛异常）。
"""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.core.config import get_settings
from app.db.redis_client import check_redis
from app.db.session import check_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(response: Response) -> dict:
    settings = get_settings()
    components = {
        "db": "up" if check_db() else "down",
        "redis": "up" if check_redis() else "down",
    }
    healthy = all(v == "up" for v in components.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if healthy else "degraded",
        "app": settings.app_name,
        "version": __version__,
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "components": components,
    }
