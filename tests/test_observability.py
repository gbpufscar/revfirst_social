from types import SimpleNamespace

from src.core import observability


def test_init_sentry_skips_when_dsn_missing(monkeypatch) -> None:
    observability.reset_observability_for_tests()

    called = {"count": 0}

    def fake_init(**kwargs):  # noqa: ARG001
        called["count"] += 1

    monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", True)
    monkeypatch.setattr(observability, "_call_sentry_init", fake_init)
    monkeypatch.setattr(
        observability,
        "get_settings",
        lambda: SimpleNamespace(
            sentry_dsn="",
            env="development",
            app_name="revfirst_social",
            app_version="0.1.0",
            sentry_traces_sample_rate=0.0,
        ),
    )

    assert observability.init_sentry() is False
    assert called["count"] == 0
    observability.reset_observability_for_tests()


def test_init_sentry_initializes_once(monkeypatch) -> None:
    observability.reset_observability_for_tests()

    calls = []

    def fake_init(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", True)
    monkeypatch.setattr(observability, "FastApiIntegration", lambda: object())
    monkeypatch.setattr(observability, "_call_sentry_init", fake_init)
    monkeypatch.setattr(
        observability,
        "get_settings",
        lambda: SimpleNamespace(
            sentry_dsn="https://abc@example.ingest.sentry.io/1",
            env="production",
            app_name="revfirst_social",
            app_version="0.1.0",
            sentry_traces_sample_rate=0.2,
        ),
    )

    assert observability.init_sentry() is True
    assert observability.init_sentry() is True
    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://abc@example.ingest.sentry.io/1"
    assert calls[0]["environment"] == "production"
    assert calls[0]["traces_sample_rate"] == 0.2
    observability.reset_observability_for_tests()


def test_init_sentry_skips_when_sdk_not_available(monkeypatch) -> None:
    observability.reset_observability_for_tests()
    monkeypatch.setattr(observability, "_SENTRY_AVAILABLE", False)
    monkeypatch.setattr(
        observability,
        "get_settings",
        lambda: SimpleNamespace(
            sentry_dsn="https://abc@example.ingest.sentry.io/1",
            env="production",
            app_name="revfirst_social",
            app_version="0.1.0",
            sentry_traces_sample_rate=0.1,
        ),
    )

    assert observability.init_sentry() is False
