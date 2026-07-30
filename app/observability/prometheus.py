"""Prometheus 进程指标；业务全局指标继续由数据库聚合的 admin metrics 提供。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "supportflow_http_requests_total",
    "HTTP requests handled by the API process",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "supportflow_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

router = APIRouter(tags=["observability"])


async def prometheus_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = None
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
