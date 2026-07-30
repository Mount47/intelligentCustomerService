from fastapi.testclient import TestClient

from app.main import app


def test_prometheus_endpoint_and_http_instrumentation():
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "supportflow_http_requests_total" in body
    assert 'route="/"' in body
