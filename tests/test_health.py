"""M1 烟雾测试：app 能装配、/ 与 /health 路由可达。

不依赖真实 DB/Redis：health 探针失败时返回 503 + degraded（仍是合法响应），
所以此测试在无外部依赖时也能跑（断言结构而非 up）。
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["app"]


def test_health_shape():
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert set(body["components"]) == {"db", "redis"}
    assert "llm_model" in body  # 模型名来自配置，可观测
