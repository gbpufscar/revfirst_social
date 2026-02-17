"""Pipeline step: ingest open calls from X into local store."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import get_settings
from integrations.x.x_client import XClient
from orchestrator.storage import get_connection, init_db, record_pipeline_run, upsert_candidates
from pipelines.io_utils import build_data_path, write_jsonl


def _normalize_candidates(candidates: list[Any]) -> list[dict[str, str]]:
    now_iso = datetime.now(timezone.utc).isoformat()
    normalized: list[dict[str, str]] = []

    for idx, item in enumerate(candidates):
        if is_dataclass(item):
            raw = asdict(item)
        elif isinstance(item, dict):
            raw = item
        else:
            continue

        post_id = str(raw.get("post_id") or raw.get("id") or f"x-{idx}").strip()
        text = str(raw.get("text") or "").strip()
        if not post_id or not text:
            continue

        normalized.append(
            {
                "post_id": post_id,
                "author": str(raw.get("author") or "unknown").strip() or "unknown",
                "text": text,
                "created_at": str(raw.get("created_at") or now_iso),
            }
        )

    return normalized


def _fallback_candidates(limit: int) -> list[dict[str, str]]:
    now = datetime.now(timezone.utc)
    seed_texts = [
        ("founder_ops", "Need a reliable way to turn X conversations into qualified leads."),
        ("growth_builder", "How do you keep reply quality high without sounding generic?"),
        ("b2b_operator", "Looking for a practical process to prioritize social opportunities."),
        ("product_marketer", "Recommend a framework to score thread opportunities in real time."),
    ]

    rows: list[dict[str, str]] = []
    for idx, (author, text) in enumerate(seed_texts[:limit]):
        rows.append(
            {
                "post_id": f"seed-{idx + 1}",
                "author": author,
                "text": text,
                "created_at": (now - timedelta(minutes=idx * 3)).isoformat(),
            }
        )
    return rows


def run(limit: int = 50) -> dict:
    settings = get_settings()
    client = XClient()
    fetched = client.fetch_open_calls(limit=limit)

    normalized = _normalize_candidates(fetched)
    source = "x"
    if not normalized:
        normalized = _fallback_candidates(limit=limit)
        source = "seed"

    artifact_path = build_data_path(settings.data_dir, "candidates.jsonl")
    details: dict[str, Any] = {
        "ingested": 0,
        "source": source,
        "artifact": str(artifact_path),
    }

    conn = get_connection(settings.db_path)
    try:
        init_db(conn)
        ingested_count = upsert_candidates(conn, normalized, source=source)
        write_jsonl(artifact_path, normalized)

        details["ingested"] = ingested_count
        record_pipeline_run(conn, "ingest_open_calls", "ok", details)
    finally:
        conn.close()

    return {"status": "ok", "pipeline": "ingest_open_calls", **details}
