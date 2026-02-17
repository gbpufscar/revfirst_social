"""SQLite persistence helpers for RevFirst_Social pipelines."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str) -> sqlite3.Connection:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            post_id TEXT PRIMARY KEY,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'x',
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ranked_candidates (
            post_id TEXT PRIMARY KEY,
            intent_label TEXT NOT NULL,
            intent_confidence REAL NOT NULL,
            audience_fit REAL NOT NULL,
            urgency REAL NOT NULL,
            opportunity_score REAL NOT NULL,
            ranked_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES candidates(post_id)
        );

        CREATE TABLE IF NOT EXISTS proposed_replies (
            post_id TEXT PRIMARY KEY,
            reply_text TEXT NOT NULL,
            opportunity_score REAL NOT NULL,
            quality_score REAL NOT NULL,
            brand_ok INTEGER NOT NULL,
            cringe_ok INTEGER NOT NULL,
            issues_json TEXT NOT NULL,
            proposed_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES candidates(post_id)
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def upsert_candidates(conn: sqlite3.Connection, items: list[dict[str, Any]], source: str = "x") -> int:
    if not items:
        return 0

    now = utc_now_iso()
    rows: list[tuple[str, str, str, str, str, str]] = []
    for item in items:
        rows.append(
            (
                str(item.get("post_id", "")).strip(),
                str(item.get("author", "unknown")).strip() or "unknown",
                str(item.get("text", "")).strip(),
                str(item.get("created_at", now)),
                source,
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO candidates (post_id, author, text, created_at, source, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            author = excluded.author,
            text = excluded.text,
            created_at = excluded.created_at,
            source = excluded.source,
            ingested_at = excluded.ingested_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def fetch_candidates(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT post_id, author, text, created_at, source, ingested_at
        FROM candidates
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def upsert_ranked_candidates(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    if not items:
        return 0

    now = utc_now_iso()
    rows: list[tuple[str, str, float, float, float, float, str]] = []
    for item in items:
        rows.append(
            (
                str(item["post_id"]),
                str(item["intent_label"]),
                float(item["intent_confidence"]),
                float(item["audience_fit"]),
                float(item["urgency"]),
                float(item["opportunity_score"]),
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO ranked_candidates (
            post_id,
            intent_label,
            intent_confidence,
            audience_fit,
            urgency,
            opportunity_score,
            ranked_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            intent_label = excluded.intent_label,
            intent_confidence = excluded.intent_confidence,
            audience_fit = excluded.audience_fit,
            urgency = excluded.urgency,
            opportunity_score = excluded.opportunity_score,
            ranked_at = excluded.ranked_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def fetch_ranked_candidates(
    conn: sqlite3.Connection,
    min_score: float = 0.0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """
        SELECT
            rc.post_id,
            c.author,
            c.text,
            c.created_at,
            rc.intent_label,
            rc.intent_confidence,
            rc.audience_fit,
            rc.urgency,
            rc.opportunity_score,
            rc.ranked_at
        FROM ranked_candidates rc
        JOIN candidates c ON c.post_id = rc.post_id
        WHERE rc.opportunity_score >= ?
        ORDER BY rc.opportunity_score DESC, rc.ranked_at DESC
        LIMIT ?
        """,
        (min_score, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def upsert_proposed_replies(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    if not items:
        return 0

    now = utc_now_iso()
    rows: list[tuple[str, str, float, float, int, int, str, str]] = []
    for item in items:
        rows.append(
            (
                str(item["post_id"]),
                str(item["reply_text"]),
                float(item["opportunity_score"]),
                float(item["quality_score"]),
                int(bool(item["brand_ok"])),
                int(bool(item["cringe_ok"])),
                json.dumps(item.get("issues", []), ensure_ascii=True),
                now,
            )
        )

    conn.executemany(
        """
        INSERT INTO proposed_replies (
            post_id,
            reply_text,
            opportunity_score,
            quality_score,
            brand_ok,
            cringe_ok,
            issues_json,
            proposed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id) DO UPDATE SET
            reply_text = excluded.reply_text,
            opportunity_score = excluded.opportunity_score,
            quality_score = excluded.quality_score,
            brand_ok = excluded.brand_ok,
            cringe_ok = excluded.cringe_ok,
            issues_json = excluded.issues_json,
            proposed_at = excluded.proposed_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def record_pipeline_run(
    conn: sqlite3.Connection,
    pipeline: str,
    status: str,
    details: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO pipeline_runs (pipeline, status, details_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (pipeline, status, json.dumps(details, ensure_ascii=True), utc_now_iso()),
    )
    conn.commit()
