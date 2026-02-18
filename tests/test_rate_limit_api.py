from fastapi.testclient import TestClient

import src.api.main as api_main
from src.core.metrics import reset_metrics_for_tests
from src.core.rate_limit import RateLimitDecision


class _StaticLimiter:
    def __init__(self, decision: RateLimitDecision) -> None:
        self._decision = decision

    def check(self, *, ip: str) -> RateLimitDecision:  # noqa: ARG002
        return self._decision


def test_rate_limit_blocks_request_and_sets_headers(monkeypatch) -> None:
    reset_metrics_for_tests()
    monkeypatch.setattr(api_main.settings, "env", "production")
    monkeypatch.setattr(api_main.settings, "ip_rate_limit_enabled", True)
    monkeypatch.setattr(
        api_main,
        "get_ip_rate_limiter",
        lambda: _StaticLimiter(
            RateLimitDecision(
                allowed=False,
                limit=10,
                remaining=0,
                reset_seconds=30,
            )
        ),
    )

    client = TestClient(api_main.app)
    response = client.get("/version")

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"
    assert response.headers["x-rate-limit-limit"] == "10"
    assert response.headers["x-rate-limit-remaining"] == "0"
    assert response.headers["x-rate-limit-reset"] == "30"


def test_rate_limit_allows_request_and_sets_headers(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings, "env", "production")
    monkeypatch.setattr(api_main.settings, "ip_rate_limit_enabled", True)
    monkeypatch.setattr(
        api_main,
        "get_ip_rate_limiter",
        lambda: _StaticLimiter(
            RateLimitDecision(
                allowed=True,
                limit=10,
                remaining=9,
                reset_seconds=60,
            )
        ),
    )

    client = TestClient(api_main.app)
    response = client.get("/version")

    assert response.status_code == 200
    assert response.headers["x-rate-limit-limit"] == "10"
    assert response.headers["x-rate-limit-remaining"] == "9"
    assert response.headers["x-rate-limit-reset"] == "60"
