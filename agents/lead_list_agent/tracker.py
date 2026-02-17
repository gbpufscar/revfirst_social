"""Track target leads and update interaction state."""

from __future__ import annotations


def upsert_lead(lead_map: dict[str, dict], handle: str, data: dict) -> dict[str, dict]:
    lead_map[handle] = {**lead_map.get(handle, {}), **data}
    return lead_map
