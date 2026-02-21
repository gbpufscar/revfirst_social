from __future__ import annotations

from datetime import datetime, timezone

from src.editorial.windows import next_publish_window, parse_daily_publish_windows_utc


def test_next_publish_window_before_first_window() -> None:
    windows = parse_daily_publish_windows_utc("07:30,16:30,20:30")
    now = datetime(2026, 2, 21, 7, 0, tzinfo=timezone.utc)
    result = next_publish_window(now, windows_utc=windows)
    assert result.scheduled_for == datetime(2026, 2, 21, 7, 30, tzinfo=timezone.utc)
    assert result.window_key == "20260221-0730"


def test_next_publish_window_between_first_and_second() -> None:
    windows = parse_daily_publish_windows_utc("07:30,16:30,20:30")
    now = datetime(2026, 2, 21, 12, 0, tzinfo=timezone.utc)
    result = next_publish_window(now, windows_utc=windows)
    assert result.scheduled_for == datetime(2026, 2, 21, 16, 30, tzinfo=timezone.utc)
    assert result.window_key == "20260221-1630"


def test_next_publish_window_between_second_and_third() -> None:
    windows = parse_daily_publish_windows_utc("07:30,16:30,20:30")
    now = datetime(2026, 2, 21, 18, 0, tzinfo=timezone.utc)
    result = next_publish_window(now, windows_utc=windows)
    assert result.scheduled_for == datetime(2026, 2, 21, 20, 30, tzinfo=timezone.utc)
    assert result.window_key == "20260221-2030"


def test_next_publish_window_after_last_window() -> None:
    windows = parse_daily_publish_windows_utc("07:30,16:30,20:30")
    now = datetime(2026, 2, 21, 23, 59, tzinfo=timezone.utc)
    result = next_publish_window(now, windows_utc=windows)
    assert result.scheduled_for == datetime(2026, 2, 22, 7, 30, tzinfo=timezone.utc)
    assert result.window_key == "20260222-0730"


def test_next_publish_window_on_exact_window_rolls_forward() -> None:
    windows = parse_daily_publish_windows_utc("07:30,16:30,20:30")
    now = datetime(2026, 2, 21, 16, 30, tzinfo=timezone.utc)
    result = next_publish_window(now, windows_utc=windows)
    assert result.scheduled_for == datetime(2026, 2, 21, 20, 30, tzinfo=timezone.utc)
    assert result.window_key == "20260221-2030"

