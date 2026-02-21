"""Deterministic publish window helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Sequence


DEFAULT_DAILY_PUBLISH_WINDOWS_UTC = ("07:30", "16:30", "20:30")


@dataclass(frozen=True)
class PublishWindow:
    scheduled_for: datetime
    window_key: str


def parse_daily_publish_windows_utc(raw: str | Sequence[str] | None) -> tuple[time, ...]:
    values: Iterable[str]
    if raw is None:
        values = DEFAULT_DAILY_PUBLISH_WINDOWS_UTC
    elif isinstance(raw, str):
        values = [token.strip() for token in raw.split(",")]
    else:
        values = [str(token).strip() for token in raw]

    parsed: list[time] = []
    for token in values:
        if not token:
            continue
        pieces = token.split(":")
        if len(pieces) != 2:
            raise ValueError(f"Invalid UTC window format: {token}")
        hour = int(pieces[0])
        minute = int(pieces[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError(f"Invalid UTC window value: {token}")
        parsed.append(time(hour=hour, minute=minute, tzinfo=timezone.utc))

    if not parsed:
        raise ValueError("At least one DAILY_PUBLISH_WINDOWS_UTC value is required.")

    unique = sorted(set((value.hour, value.minute) for value in parsed))
    return tuple(time(hour=hour, minute=minute, tzinfo=timezone.utc) for hour, minute in unique)


def publish_window_key(scheduled_for_utc: datetime) -> str:
    normalized = scheduled_for_utc.astimezone(timezone.utc)
    return normalized.strftime("%Y%m%d-%H%M")


def next_publish_window(
    now_utc: datetime,
    *,
    windows_utc: Sequence[time] | None = None,
) -> PublishWindow:
    now = now_utc if now_utc.tzinfo is not None else now_utc.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    windows = tuple(windows_utc or parse_daily_publish_windows_utc(None))

    for window in windows:
        candidate = datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=window.hour,
            minute=window.minute,
            tzinfo=timezone.utc,
        )
        if now < candidate:
            return PublishWindow(scheduled_for=candidate, window_key=publish_window_key(candidate))

    first = windows[0]
    next_day = now + timedelta(days=1)
    candidate = datetime(
        year=next_day.year,
        month=next_day.month,
        day=next_day.day,
        hour=first.hour,
        minute=first.minute,
        tzinfo=timezone.utc,
    )
    return PublishWindow(scheduled_for=candidate, window_key=publish_window_key(candidate))

