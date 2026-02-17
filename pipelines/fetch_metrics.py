"""Pipeline step: fetch metrics."""

from __future__ import annotations


def run() -> dict:
    return {"status": "ok", "pipeline": "fetch_metrics"}
