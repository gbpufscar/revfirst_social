from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import src.api.main as api_main
from src.storage.db import Base, get_session, load_models
from src.workspaces.service import create_workspace_with_owner


def _build_sqlite_session_factory():
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def test_phase2_create_workspace_login_and_read() -> None:
    session_factory = _build_sqlite_session_factory()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)
        create_response = client.post(
            "/workspaces",
            json={
                "name": "acme",
                "owner_email": "owner@acme.io",
                "owner_password": "supersecret123",
            },
        )
        assert create_response.status_code == 201
        workspace_id = create_response.json()["workspace_id"]

        login_response = client.post(
            "/auth/login",
            json={
                "email": "owner@acme.io",
                "password": "supersecret123",
                "workspace_id": workspace_id,
            },
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        workspace_response = client.get(
            f"/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert workspace_response.status_code == 200
        payload = workspace_response.json()
        assert payload["id"] == workspace_id
        assert payload["my_role"] == "owner"
        assert payload["name"] == "acme"
    finally:
        api_main.app.dependency_overrides.clear()


def test_phase2_cross_workspace_access_blocked() -> None:
    session_factory = _build_sqlite_session_factory()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api_main.app.dependency_overrides[get_session] = override_get_session
    try:
        client = TestClient(api_main.app)

        ws1 = client.post(
            "/workspaces",
            json={
                "name": "workspace-alpha",
                "owner_email": "alpha@revfirst.io",
                "owner_password": "alpha-secret-123",
            },
        ).json()["workspace_id"]

        ws2 = client.post(
            "/workspaces",
            json={
                "name": "workspace-beta",
                "owner_email": "beta@revfirst.io",
                "owner_password": "beta-secret-123",
            },
        ).json()["workspace_id"]

        login_response = client.post(
            "/auth/login",
            json={
                "email": "alpha@revfirst.io",
                "password": "alpha-secret-123",
                "workspace_id": ws1,
            },
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        forbidden_response = client.get(
            f"/workspaces/{ws2}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden_response.status_code == 403
    finally:
        api_main.app.dependency_overrides.clear()


def test_phase2_existing_user_with_wrong_password_is_rejected() -> None:
    session_factory = _build_sqlite_session_factory()
    session = session_factory()
    try:
        create_workspace_with_owner(
            session,
            workspace_name="tenant-one",
            owner_email="owner@tenant.io",
            owner_password="correct-password-1",
        )

        with pytest.raises(HTTPException) as exc_info:
            create_workspace_with_owner(
                session,
                workspace_name="tenant-two",
                owner_email="owner@tenant.io",
                owner_password="wrong-password-2",
            )
        assert exc_info.value.status_code == 409
        assert "different credentials" in exc_info.value.detail
    finally:
        session.close()
