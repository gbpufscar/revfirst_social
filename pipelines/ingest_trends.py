"""Pipeline step: ingest trends."""

from __future__ import annotations


def run() -> dict:
    return {"status": "ok", "pipeline": "ingest_trends"}
