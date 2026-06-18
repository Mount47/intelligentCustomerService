"""FastAPI 入口。仅装配（app factory + 路由 + 异常映射），业务逻辑不写这里（§21）。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api import chat, health
from app.core.config import get_settings
from app.core.exceptions import SupportFlowError
from app.core.logging import get_logger, setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)

    app = FastAPI(title=settings.app_name, version=__version__)

    # 路由（health + chat 异步闭环；tickets/admin 后续）
    app.include_router(health.router)
    app.include_router(chat.router)

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {"app": settings.app_name, "version": __version__, "docs": "/docs"}

    @app.exception_handler(SupportFlowError)
    async def supportflow_error_handler(_: Request, exc: SupportFlowError) -> JSONResponse:
        """自定义异常 → 统一结构化错误响应。"""
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    logger.info("SupportFlow app initialized (provider=%s, model=%s)",
                settings.llm_provider, settings.llm_model)
    return app


app = create_app()
