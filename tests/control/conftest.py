from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import yaml

import src.api.main as api_main
import src.control.telegram_bot as control_bot
from src.control.security import reset_admin_directory_cache
from src.core.config import get_settings
from src.integrations.x.x_client import get_x_client
from src.storage.db import Base, get_session, load_models
from src.storage.models import User
from src.storage.security import get_token_key


class FakeRedis:
    def __init__(self) -> None:
        self._store: Dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        del ex
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        return True

    def get(self, key: str):
        return self._store.get(key)

    def delete(self, key: str):
        return 1 if self._store.pop(key, None) is not None else 0

    def exists(self, key: str):
        return 1 if key in self._store else 0

    def keys(self, pattern: str):
        if "*" not in pattern:
            return [pattern] if pattern in self._store else []
        prefix = pattern.split("*", 1)[0]
        return [key for key in self._store if key.startswith(prefix)]

    def eval(self, script: str, numkeys: int, key: str, token: str):
        del script, numkeys
        if self._store.get(key) == token:
            self._store.pop(key, None)
            return 1
        return 0


class FakePublisherXClient:
    def __init__(self) -> None:
        self.counter = 0

    def create_tweet(self, *, access_token: str, text: str, in_reply_to_tweet_id: str | None = None):
        del access_token, text, in_reply_to_tweet_id
        self.counter += 1
        return {"data": {"id": f"tweet-{self.counter}"}}


@dataclass
class ControlTestContext:
    client: TestClient
    session_factory: sessionmaker
    workspace_id: str
    access_token: str
    owner_user_id: str
    owner_email: str
    owner_password: str
    fake_redis: FakeRedis
    fake_x: FakePublisherXClient
    admins_file_path: Path


def _build_sqlite_session_factory() -> sessionmaker:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _bootstrap_workspace(client: TestClient, *, workspace_name: str, owner_email: str, owner_password: str) -> tuple[str, str]:
    create_response = client.post(
        "/workspaces",
        json={
            "name": workspace_name,
            "owner_email": owner_email,
            "owner_password": owner_password,
        },
    )
    assert create_response.status_code == 201
    workspace_id = create_response.json()["workspace_id"]

    login_response = client.post(
        "/auth/login",
        json={
            "email": owner_email,
            "password": owner_password,
            "workspace_id": workspace_id,
        },
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return workspace_id, token


def _write_admins_file(
    path: Path,
    *,
    telegram_user_id: str,
    user_id: str,
    include_in_allowed: bool = True,
) -> None:
    allowed = [telegram_user_id] if include_in_allowed else []
    payload = {
        "allowed_telegram_ids": allowed,
        "admins": [
            {
                "telegram_user_id": telegram_user_id,
                "user_id": user_id,
                "allowed_roles": ["owner", "admin", "member"],
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def create_control_test_context(monkeypatch, tmp_path: Path, *, include_in_allowed: bool = True) -> ControlTestContext:
    admins_path = tmp_path / "telegram_admins.yaml"

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "Y4Cpe2s2aQvRIvF8y17kF8s0w58K7tY6xE8DAXmXGJQ=")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "phase12-secret")
    monkeypatch.setenv("TELEGRAM_ADMINS_FILE_PATH", str(admins_path))

    get_settings.cache_clear()
    get_token_key.cache_clear()
    reset_admin_directory_cache()

    session_factory = _build_sqlite_session_factory()
    fake_redis = FakeRedis()
    fake_x = FakePublisherXClient()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    api_main.app.dependency_overrides[get_x_client] = lambda: fake_x
    control_bot.get_redis_client = lambda: fake_redis  # type: ignore[assignment]

    client = TestClient(api_main.app)

    owner_email = f"owner-{uuid.uuid4()}@revfirst.io"
    owner_password = "owner-pass-123"
    workspace_id, token = _bootstrap_workspace(
        client,
        workspace_name=f"phase12-{uuid.uuid4()}",
        owner_email=owner_email,
        owner_password=owner_password,
    )

    with session_factory() as session:
        owner_user = session.scalar(select(User).where(User.email == owner_email))
        assert owner_user is not None
        owner_user_id = owner_user.id

    _write_admins_file(
        admins_path,
        telegram_user_id="90001",
        user_id=owner_user_id,
        include_in_allowed=include_in_allowed,
    )
    reset_admin_directory_cache()

    return ControlTestContext(
        client=client,
        session_factory=session_factory,
        workspace_id=workspace_id,
        access_token=token,
        owner_user_id=owner_user_id,
        owner_email=owner_email,
        owner_password=owner_password,
        fake_redis=fake_redis,
        fake_x=fake_x,
        admins_file_path=admins_path,
    )


def teardown_control_test_context() -> None:
    api_main.app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_token_key.cache_clear()
    reset_admin_directory_cache()
