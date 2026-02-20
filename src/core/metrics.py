"""In-process metrics collector with Prometheus text exposition."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
import time
from typing import Dict, Tuple


_lock = Lock()
_started_at = time.time()

_http_requests_total: Dict[Tuple[str, str, str], int] = defaultdict(int)
_http_request_duration_sum: Dict[Tuple[str, str], float] = defaultdict(float)
_http_request_duration_count: Dict[Tuple[str, str], int] = defaultdict(int)
_rate_limit_block_total: Dict[str, int] = defaultdict(int)
_replies_generated_total: Dict[str, int] = defaultdict(int)
_replies_published_total: Dict[str, int] = defaultdict(int)
_reply_blocked_total: Dict[Tuple[str, str], int] = defaultdict(int)
_daily_post_published_total: Dict[str, int] = defaultdict(int)
_seed_used_total: Dict[str, int] = defaultdict(int)
_publish_errors_total: Dict[Tuple[str, str], int] = defaultdict(int)
_x_token_refresh_total: Dict[Tuple[str, str], int] = defaultdict(int)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_label(value: str, *, fallback: str = "unknown") -> str:
    normalized = (value or "").strip()
    return normalized or fallback


def record_http_request(*, method: str, path: str, status_code: int, duration_seconds: float) -> None:
    status = str(status_code)
    method_label = method.upper()
    path_label = path or "unknown"
    duration = max(duration_seconds, 0.0)

    with _lock:
        _http_requests_total[(method_label, path_label, status)] += 1
        _http_request_duration_sum[(method_label, path_label)] += duration
        _http_request_duration_count[(method_label, path_label)] += 1


def record_rate_limit_block(*, kind: str) -> None:
    with _lock:
        _rate_limit_block_total[_normalize_label(kind)] += 1


def record_replies_generated(*, workspace_id: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        _replies_generated_total[_normalize_label(workspace_id)] += int(count)


def record_replies_published(*, workspace_id: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        _replies_published_total[_normalize_label(workspace_id)] += int(count)


def record_reply_blocked(*, workspace_id: str, reason: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        key = (_normalize_label(workspace_id), _normalize_label(reason))
        _reply_blocked_total[key] += int(count)


def record_daily_post_published(*, workspace_id: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        _daily_post_published_total[_normalize_label(workspace_id)] += int(count)


def record_seed_used(*, workspace_id: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        _seed_used_total[_normalize_label(workspace_id)] += int(count)


def record_publish_error(*, workspace_id: str, channel: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        key = (_normalize_label(workspace_id), _normalize_label(channel))
        _publish_errors_total[key] += int(count)


def record_x_token_refresh(*, workspace_id: str, status: str, count: int = 1) -> None:
    if count <= 0:
        return
    with _lock:
        key = (_normalize_label(workspace_id), _normalize_label(status))
        _x_token_refresh_total[key] += int(count)


def render_prometheus_metrics(*, app_name: str, app_version: str, env: str) -> str:
    uptime = max(time.time() - _started_at, 0.0)

    with _lock:
        http_total = dict(_http_requests_total)
        duration_sum = dict(_http_request_duration_sum)
        duration_count = dict(_http_request_duration_count)
        rate_limit_total = dict(_rate_limit_block_total)
        replies_generated_total = dict(_replies_generated_total)
        replies_published_total = dict(_replies_published_total)
        reply_blocked_total = dict(_reply_blocked_total)
        daily_post_published_total = dict(_daily_post_published_total)
        seed_used_total = dict(_seed_used_total)
        publish_errors_total = dict(_publish_errors_total)
        x_token_refresh_total = dict(_x_token_refresh_total)

    lines = [
        "# HELP revfirst_build_info Build metadata.",
        "# TYPE revfirst_build_info gauge",
        (
            f'revfirst_build_info{{app_name="{_escape_label(app_name)}",'
            f'version="{_escape_label(app_version)}",env="{_escape_label(env)}"}} 1'
        ),
        "# HELP revfirst_process_uptime_seconds Process uptime in seconds.",
        "# TYPE revfirst_process_uptime_seconds gauge",
        f"revfirst_process_uptime_seconds {uptime:.6f}",
        "# HELP revfirst_http_requests_total Total HTTP requests.",
        "# TYPE revfirst_http_requests_total counter",
    ]

    for (method, path, status), value in sorted(http_total.items()):
        lines.append(
            (
                f'revfirst_http_requests_total{{method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}",status="{_escape_label(status)}"}} {value}'
            )
        )

    lines.extend(
        [
            "# HELP revfirst_http_request_duration_seconds Request duration summary.",
            "# TYPE revfirst_http_request_duration_seconds summary",
        ]
    )
    for (method, path), value in sorted(duration_sum.items()):
        lines.append(
            (
                f'revfirst_http_request_duration_seconds_sum{{method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}"}} {value:.6f}'
            )
        )
    for (method, path), value in sorted(duration_count.items()):
        lines.append(
            (
                f'revfirst_http_request_duration_seconds_count{{method="{_escape_label(method)}",'
                f'path="{_escape_label(path)}"}} {value}'
            )
        )

    lines.extend(
        [
            "# HELP revfirst_rate_limit_block_total Requests blocked by rate limiting.",
            "# TYPE revfirst_rate_limit_block_total counter",
        ]
    )
    for kind, value in sorted(rate_limit_total.items()):
        lines.append(f'revfirst_rate_limit_block_total{{kind="{_escape_label(kind)}"}} {value}')

    lines.extend(
        [
            "# HELP revfirst_replies_generated_total Total generated replies.",
            "# TYPE revfirst_replies_generated_total counter",
        ]
    )
    for workspace_id, value in sorted(replies_generated_total.items()):
        lines.append(
            f'revfirst_replies_generated_total{{workspace_id="{_escape_label(workspace_id)}"}} {value}'
        )

    lines.extend(
        [
            "# HELP revfirst_replies_published_total Total published replies.",
            "# TYPE revfirst_replies_published_total counter",
        ]
    )
    for workspace_id, value in sorted(replies_published_total.items()):
        lines.append(
            f'revfirst_replies_published_total{{workspace_id="{_escape_label(workspace_id)}"}} {value}'
        )

    lines.extend(
        [
            "# HELP revfirst_reply_blocked_total Total blocked replies.",
            "# TYPE revfirst_reply_blocked_total counter",
        ]
    )
    for (workspace_id, reason), value in sorted(reply_blocked_total.items()):
        lines.append(
            (
                f'revfirst_reply_blocked_total{{workspace_id="{_escape_label(workspace_id)}",'
                f'reason="{_escape_label(reason)}"}} {value}'
            )
        )

    lines.extend(
        [
            "# HELP revfirst_daily_post_published_total Total published daily posts.",
            "# TYPE revfirst_daily_post_published_total counter",
        ]
    )
    for workspace_id, value in sorted(daily_post_published_total.items()):
        lines.append(
            f'revfirst_daily_post_published_total{{workspace_id="{_escape_label(workspace_id)}"}} {value}'
        )

    lines.extend(
        [
            "# HELP revfirst_seed_used_total Total seeds used for generation.",
            "# TYPE revfirst_seed_used_total counter",
        ]
    )
    for workspace_id, value in sorted(seed_used_total.items()):
        lines.append(f'revfirst_seed_used_total{{workspace_id="{_escape_label(workspace_id)}"}} {value}')

    lines.extend(
        [
            "# HELP revfirst_publish_errors_total Total publish errors by channel.",
            "# TYPE revfirst_publish_errors_total counter",
        ]
    )
    for (workspace_id, channel), value in sorted(publish_errors_total.items()):
        lines.append(
            (
                f'revfirst_publish_errors_total{{workspace_id="{_escape_label(workspace_id)}",'
                f'channel="{_escape_label(channel)}"}} {value}'
            )
        )

    lines.extend(
        [
            "# HELP revfirst_x_token_refresh_total Total X token refresh outcomes.",
            "# TYPE revfirst_x_token_refresh_total counter",
        ]
    )
    for (workspace_id, status), value in sorted(x_token_refresh_total.items()):
        lines.append(
            (
                f'revfirst_x_token_refresh_total{{workspace_id="{_escape_label(workspace_id)}",'
                f'status="{_escape_label(status)}"}} {value}'
            )
        )

    lines.append("")
    return "\n".join(lines)


def reset_metrics_for_tests() -> None:
    global _started_at
    with _lock:
        _http_requests_total.clear()
        _http_request_duration_sum.clear()
        _http_request_duration_count.clear()
        _rate_limit_block_total.clear()
        _replies_generated_total.clear()
        _replies_published_total.clear()
        _reply_blocked_total.clear()
        _daily_post_published_total.clear()
        _seed_used_total.clear()
        _publish_errors_total.clear()
        _x_token_refresh_total.clear()
    _started_at = time.time()
