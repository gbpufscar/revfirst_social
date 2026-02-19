from __future__ import annotations

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.config import get_settings
from src.media.providers import reset_image_provider_cache
from src.media.service import generate_image_asset, list_media_assets
from src.storage.db import Base, load_models
from src.storage.models import MediaAsset, MediaJob, Workspace


def _build_session() -> Session:
    load_models()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return factory()


def test_generate_image_asset_uses_mock_provider_and_persists_records(monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    reset_image_provider_cache()

    session = _build_session()
    try:
        workspace = Workspace(
            id=str(uuid.uuid4()),
            name=f"media-workspace-{uuid.uuid4()}",
            plan="free",
            subscription_status="active",
        )
        session.add(workspace)
        session.commit()

        first = generate_image_asset(
            session,
            workspace_id=workspace.id,
            channel="instagram",
            content_text="Founder execution note for builders.",
            source_kind="daily_post_draft",
            source_ref_id="draft-1",
            idempotency_key="draft-1:instagram",
        )
        assert first.success is True
        assert first.status == "succeeded"
        assert first.public_url is not None
        assert first.public_url.startswith("https://picsum.photos/")
        assert first.asset_id is not None
        assert first.job_id is not None

        second = generate_image_asset(
            session,
            workspace_id=workspace.id,
            channel="instagram",
            content_text="Founder execution note for builders.",
            source_kind="daily_post_draft",
            source_ref_id="draft-1",
            idempotency_key="draft-1:instagram",
        )
        assert second.success is True
        assert second.status == "reused"
        assert second.reused is True
        assert second.asset_id == first.asset_id

        job_rows = list(
            session.scalars(
                select(MediaJob).where(MediaJob.workspace_id == workspace.id)
            ).all()
        )
        asset_rows = list(
            session.scalars(
                select(MediaAsset).where(MediaAsset.workspace_id == workspace.id)
            ).all()
        )
        assert len(job_rows) == 1
        assert len(asset_rows) == 1

        assets = list_media_assets(session, workspace_id=workspace.id, limit=10)
        assert len(assets) == 1
        assert assets[0].channel == "instagram"
    finally:
        session.close()
        get_settings.cache_clear()
        reset_image_provider_cache()
