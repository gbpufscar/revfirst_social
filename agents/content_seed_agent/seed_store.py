"""In-memory seed store abstraction."""

from __future__ import annotations


class SeedStore:
    def __init__(self) -> None:
        self._items: list[dict] = []

    def add(self, item: dict) -> None:
        self._items.append(item)

    def list(self) -> list[dict]:
        return list(self._items)
