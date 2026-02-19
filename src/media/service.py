"""Media infrastructure services and image-agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.media.providers import ImageProviderError, get_image_provider
from src.storage.models import MediaAsset, MediaJob, WorkspaceEvent


CHANNEL_IMAGE_SPECS: Dict[str, Dict[str, int]] = {
    "instagram": {"width": 1080, "height": 1350},
    "x": {"width": 1600, "height": 900},
    "blog": {"width": 1200, "height": 630},
}


@dataclass(frozen=True)
class MediaGenerationResult:
    success: bool
    workspace_id: str
    channel: str
    status: str
    message: str
    job_id: Optional[str] = None
    asset_id: Optional[str] = None
    public_url: Optional[str] = None
    reused: bool = False


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mime_extension(mime_type: str) -> str:
    normalized = mime_type.strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    return mapping.get(normalized, ".bin")


def _media_storage_root() -> Path:
    settings = get_settings()
    configured = Path(settings.media_storage_path)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


def _public_media_url(asset_id: str) -> str:
    settings = get_settings()
    base = settings.app_public_base_url.strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/media/public/{asset_id}"


def _store_media_bytes(*, workspace_id: str, asset_id: str, mime_type: str, content: bytes) -> tuple[str, str, int]:
    storage_root = _media_storage_root() / workspace_id
    storage_root.mkdir(parents=True, exist_ok=True)
    extension = _mime_extension(mime_type)
    filename = f"{asset_id}{extension}"
    full_path = storage_root / filename
    full_path.write_bytes(content)

    digest = hashlib.sha256(content).hexdigest()
    relative = str((Path(workspace_id) / filename).as_posix())
    return relative, digest, len(content)


def _image_spec(channel: str) -> Dict[str, int]:
    normalized = channel.strip().lower()
    return CHANNEL_IMAGE_SPECS.get(normalized, {"width": 1200, "height": 630})


def build_image_prompt(*, channel: str, content_text: str, brand_context: Optional[str] = None) -> str:
    normalized_channel = channel.strip().lower() or "generic"
    cleaned_text = " ".join((content_text or "").strip().split())
    if len(cleaned_text) > 420:
        cleaned_text = cleaned_text[:420].rstrip() + "..."
    context = (brand_context or "builder-first, direct, anti-hype, no emojis").strip()
    return (
        f"Create a branded visual for {normalized_channel}. "
        f"Context: {cleaned_text}. Style constraints: {context}. "
        "Modern SaaS aesthetic, clean composition, high readability."
    )


def _event(session: Session, *, workspace_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    session.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            event_type=event_type,
            payload_json=_json_dumps(payload),
        )
    )


def _get_existing_success_asset(
    session: Session,
    *,
    workspace_id: str,
    idempotency_key: Optional[str],
) -> Optional[MediaAsset]:
    if not idempotency_key:
        return None
    job = session.scalar(
        select(MediaJob).where(
            MediaJob.workspace_id == workspace_id,
            MediaJob.idempotency_key == idempotency_key,
        )
    )
    if job is None or job.status != "succeeded" or not job.result_asset_id:
        return None
    return session.scalar(
        select(MediaAsset).where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.id == job.result_asset_id,
        )
    )


def generate_image_asset(
    session: Session,
    *,
    workspace_id: str,
    channel: str,
    content_text: str,
    source_kind: Optional[str],
    source_ref_id: Optional[str],
    idempotency_key: Optional[str] = None,
    prompt_override: Optional[str] = None,
    requested_by_user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> MediaGenerationResult:
    settings = get_settings()
    normalized_channel = channel.strip().lower()
    if not settings.image_generation_enabled:
        return MediaGenerationResult(
            success=False,
            workspace_id=workspace_id,
            channel=normalized_channel,
            status="disabled",
            message="image_generation_disabled",
        )

    existing = _get_existing_success_asset(
        session,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        return MediaGenerationResult(
            success=True,
            workspace_id=workspace_id,
            channel=normalized_channel,
            status="reused",
            message="media_asset_reused",
            job_id=None,
            asset_id=existing.id,
            public_url=existing.public_url,
            reused=True,
        )

    spec = _image_spec(normalized_channel)
    prompt = prompt_override or build_image_prompt(channel=normalized_channel, content_text=content_text)
    provider = get_image_provider()

    job = MediaJob(
        id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        status="queued",
        provider=provider.provider_name,
        channel=normalized_channel,
        prompt_text=prompt,
        source_kind=source_kind,
        source_ref_id=source_ref_id,
        requested_by_user_id=requested_by_user_id,
        idempotency_key=idempotency_key,
        payload_json=_json_dumps(metadata or {}),
    )
    session.add(job)
    session.commit()

    try:
        job.status = "running"
        job.started_at = _now_utc()
        job.updated_at = _now_utc()
        session.commit()

        generated = provider.generate_image(
            workspace_id=workspace_id,
            channel=normalized_channel,
            prompt=prompt,
            width=spec["width"],
            height=spec["height"],
        )

        public_url = (generated.image_url or "").strip()
        storage_backend = "external_url"
        storage_path = None
        sha256 = None
        size_bytes = None

        if generated.image_bytes is not None:
            public_url = _public_media_url(job.id)
            if not public_url:
                raise ImageProviderError("app_public_base_url_missing_for_binary_media")
            storage_backend = "filesystem"
            storage_path, sha256, size_bytes = _store_media_bytes(
                workspace_id=workspace_id,
                asset_id=job.id,
                mime_type=generated.mime_type,
                content=generated.image_bytes,
            )

        if not public_url:
            raise ImageProviderError("image_provider_missing_public_url")

        asset = MediaAsset(
            id=job.id,
            workspace_id=workspace_id,
            source_type="generated",
            provider=generated.provider,
            purpose=source_kind,
            channel=normalized_channel,
            mime_type=generated.mime_type,
            width=generated.width or spec["width"],
            height=generated.height or spec["height"],
            size_bytes=size_bytes,
            storage_backend=storage_backend,
            storage_path=storage_path,
            public_url=public_url,
            sha256=sha256,
            prompt_text=prompt,
            metadata_json=_json_dumps(
                {
                    "source_ref_id": source_ref_id,
                    "provider_payload": generated.payload,
                    **(metadata or {}),
                }
            ),
        )
        session.add(asset)

        job.status = "succeeded"
        job.result_asset_id = asset.id
        job.finished_at = _now_utc()
        job.updated_at = _now_utc()
        job.payload_json = _json_dumps(
            {
                "asset_id": asset.id,
                "public_url": asset.public_url,
                "provider": generated.provider,
            }
        )
        _event(
            session,
            workspace_id=workspace_id,
            event_type="media_job_succeeded",
            payload={"job_id": job.id, "asset_id": asset.id, "channel": normalized_channel},
        )
        session.commit()
        return MediaGenerationResult(
            success=True,
            workspace_id=workspace_id,
            channel=normalized_channel,
            status="succeeded",
            message="media_generated",
            job_id=job.id,
            asset_id=asset.id,
            public_url=asset.public_url,
            reused=False,
        )
    except Exception as exc:
        session.rollback()
        latest_job = session.scalar(
            select(MediaJob).where(
                MediaJob.workspace_id == workspace_id,
                MediaJob.id == job.id,
            )
        )
        if latest_job is not None:
            latest_job.status = "failed"
            latest_job.error_message = str(exc)[:255]
            latest_job.finished_at = _now_utc()
            latest_job.updated_at = _now_utc()
            _event(
                session,
                workspace_id=workspace_id,
                event_type="media_job_failed",
                payload={"job_id": latest_job.id, "channel": normalized_channel, "error": str(exc)},
            )
            session.commit()
        return MediaGenerationResult(
            success=False,
            workspace_id=workspace_id,
            channel=normalized_channel,
            status="failed",
            message=str(exc),
            job_id=job.id,
        )


def list_media_assets(session: Session, *, workspace_id: str, limit: int = 20) -> list[MediaAsset]:
    safe_limit = max(1, min(limit, 100))
    statement = (
        select(MediaAsset)
        .where(MediaAsset.workspace_id == workspace_id)
        .order_by(desc(MediaAsset.created_at))
        .limit(safe_limit)
    )
    return list(session.scalars(statement).all())


def get_media_asset(session: Session, *, asset_id: str, workspace_id: Optional[str] = None) -> Optional[MediaAsset]:
    statement = select(MediaAsset).where(MediaAsset.id == asset_id)
    if workspace_id:
        statement = statement.where(MediaAsset.workspace_id == workspace_id)
    return session.scalar(statement)


def resolve_media_file_path(asset: MediaAsset) -> Optional[Path]:
    if asset.storage_backend != "filesystem":
        return None
    if not asset.storage_path:
        return None
    return _media_storage_root() / asset.storage_path
