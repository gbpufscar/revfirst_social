"""Basic async load test for RevFirst_Social health endpoint."""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Iterable

import httpx


async def _one_request(client: httpx.AsyncClient, url: str) -> tuple[int, float]:
    started_at = time.perf_counter()
    response = await client.get(url)
    duration = time.perf_counter() - started_at
    return response.status_code, duration


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = int((len(sorted_values) - 1) * percentile)
    return sorted_values[index]


async def run_load_test(
    *,
    url: str,
    total_requests: int,
    concurrency: int,
    timeout_seconds: float,
    verify_tls: bool,
) -> dict[str, float]:
    semaphore = asyncio.Semaphore(concurrency)
    statuses: list[int] = []
    durations: list[float] = []
    started_at = time.perf_counter()

    async with httpx.AsyncClient(timeout=timeout_seconds, verify=verify_tls) as client:
        async def worker() -> None:
            async with semaphore:
                status_code, duration = await _one_request(client, url)
                statuses.append(status_code)
                durations.append(duration)

        await asyncio.gather(*(worker() for _ in range(total_requests)))

    elapsed = time.perf_counter() - started_at
    success = sum(1 for status in statuses if status < 500)
    errors = len(statuses) - success

    return {
        "total_requests": float(total_requests),
        "success": float(success),
        "errors": float(errors),
        "success_rate": (success / total_requests) * 100 if total_requests > 0 else 0.0,
        "rps": (total_requests / elapsed) if elapsed > 0 else 0.0,
        "latency_avg_ms": (sum(durations) / len(durations)) * 1000 if durations else 0.0,
        "latency_p95_ms": _percentile(durations, 0.95) * 1000,
        "latency_p99_ms": _percentile(durations, 0.99) * 1000,
        "elapsed_seconds": elapsed,
    }


def _format_report(result: dict[str, float]) -> Iterable[str]:
    yield f"total_requests={int(result['total_requests'])}"
    yield f"success={int(result['success'])}"
    yield f"errors={int(result['errors'])}"
    yield f"success_rate={result['success_rate']:.2f}%"
    yield f"throughput_rps={result['rps']:.2f}"
    yield f"latency_avg_ms={result['latency_avg_ms']:.2f}"
    yield f"latency_p95_ms={result['latency_p95_ms']:.2f}"
    yield f"latency_p99_ms={result['latency_p99_ms']:.2f}"
    yield f"elapsed_seconds={result['elapsed_seconds']:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a basic RevFirst load test.")
    parser.add_argument("--url", default="http://localhost:18000/health")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate validation (useful for self-signed test domains).",
    )
    args = parser.parse_args()

    if args.requests <= 0:
        raise ValueError("--requests must be positive")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be positive")

    result = asyncio.run(
        run_load_test(
            url=args.url,
            total_requests=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            verify_tls=not args.insecure,
        )
    )
    for line in _format_report(result):
        print(line)


if __name__ == "__main__":
    main()
