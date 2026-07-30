"""FastAPI 入口。仅装配（app factory + 路由 + 异常映射），业务逻辑不写这里（§21）。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import admin, auth, chat, health
from app.core.config import get_settings
from app.core.exceptions import SupportFlowError
from app.core.logging import get_logger, setup_logging
from app.observability.tracing import use_or_create_trace_id
from app.observability.prometheus import prometheus_middleware, router as prometheus_router


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger(__name__)

    app = FastAPI(title=settings.app_name, version=__version__)

    # CORS：本地前端联调（默认全放开，可用 CORS_ORIGINS 收紧）
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(prometheus_middleware)

    @app.middleware("http")
    async def trace_context(request: Request, call_next):
        trace_id = use_or_create_trace_id(request.headers.get("X-Trace-ID"))
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

    # 路由（health + chat 异步闭环 + admin 运维监测）
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(admin.router)
    app.include_router(prometheus_router)

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
    from app.db.session import engine
    from app.observability.otel import configure_fastapi_otel
    configure_fastapi_otel(app, engine)
    return app


app = create_app()
