"""Telegram seed ingestion pipeline helper."""

from __future__ import annotations


def extract_seed_text(update: dict) -> str:
    return (update.get("text") or "").strip()
