import src.storage.db as db_module
import src.storage.redis_client as redis_module


class _DummyConnection:
    def execute(self, _statement):
        return 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False


class _DummyEngine:
    def connect(self):
        return _DummyConnection()


class _DummyRedis:
    def ping(self):
        return True


def test_db_connection_success(monkeypatch) -> None:
    monkeypatch.setattr(db_module, "get_engine", lambda: _DummyEngine())
    ok, error = db_module.test_connection()
    assert ok is True
    assert error is None


def test_redis_connection_success(monkeypatch) -> None:
    monkeypatch.setattr(redis_module, "get_client", lambda: _DummyRedis())
    ok, error = redis_module.test_connection()
    assert ok is True
    assert error is None
