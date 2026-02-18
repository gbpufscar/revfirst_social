from fastapi.testclient import TestClient

import src.api.main as api_main
from src.core.metrics import reset_metrics_for_tests


def test_metrics_endpoint_returns_prometheus_payload(monkeypatch) -> None:
    reset_metrics_for_tests()
    monkeypatch.setattr(api_main.settings, "metrics_enabled", True)
    monkeypatch.setattr(api_main, "test_db_connection", lambda: (True, None))
    monkeypatch.setattr(api_main, "test_redis_connection", lambda: (True, None))

    client = TestClient(api_main.app)
    version_response = client.get("/version")
    assert version_response.status_code == 200

    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith("text/plain")
    body = metrics_response.text
    assert "revfirst_build_info" in body
    assert 'revfirst_http_requests_total{method="GET",path="/version",status="200"}' in body
    assert "revfirst_http_request_duration_seconds_sum" in body


def test_metrics_endpoint_disabled_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings, "metrics_enabled", False)
    client = TestClient(api_main.app)
    response = client.get("/metrics")
    assert response.status_code == 404
