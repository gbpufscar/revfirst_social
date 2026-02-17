"""Webhook helpers for Telegram integration."""

from __future__ import annotations

import json
from typing import Any


def decode_body(raw_body: bytes) -> dict[str, Any]:
    if not raw_body:
        return {}
    return json.loads(raw_body.decode("utf-8"))
