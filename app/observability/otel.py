"""可选 OpenTelemetry OTLP 导出。未配置 endpoint 时完全不初始化、不发网络请求。"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_configured = False


def configure_fastapi_otel(app, engine) -> bool:
    global _configured
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint or _configured:
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry import trace
    except ImportError:
        logger.warning("OTLP endpoint configured but observability extras are not installed")
        return False
    provider = TracerProvider(resource=Resource.create({
        "service.name": settings.otel_service_name,
        "deployment.environment": settings.app_env,
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
    )))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
    _configured = True
    logger.info("OpenTelemetry OTLP export enabled")
    return True


def configure_celery_otel() -> bool:
    global _configured
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint or _configured:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("Celery OTEL requested but observability extras are not installed")
        return False
    provider = TracerProvider(resource=Resource.create({
        "service.name": settings.otel_service_name.replace("-api", "-worker"),
        "deployment.environment": settings.app_env,
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
    )))
    trace.set_tracer_provider(provider)
    CeleryInstrumentor().instrument()
    _configured = True
    return True
