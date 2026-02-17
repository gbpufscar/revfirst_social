from fastapi.testclient import TestClient

import src.api.main as api_main


def test_health_returns_ok_when_services_are_up(monkeypatch) -> None:
    monkeypatch.setattr(api_main, "test_db_connection", lambda: (True, None))
    monkeypatch.setattr(api_main, "test_redis_connection", lambda: (True, None))

    client = TestClient(api_main.app)
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["services"]["database"]["ok"] is True
    assert payload["services"]["redis"]["ok"] is True


def test_health_returns_503_when_any_dependency_fails(monkeypatch) -> None:
    monkeypatch.setattr(api_main, "test_db_connection", lambda: (False, "db unavailable"))
    monkeypatch.setattr(api_main, "test_redis_connection", lambda: (True, None))

    client = TestClient(api_main.app)
    response = client.get("/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["services"]["database"]["error"] == "db unavailable"


def test_version_endpoint() -> None:
    client = TestClient(api_main.app)
    response = client.get("/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "revfirst_social"
    assert payload["version"]
