"""Health probing — docs/architecture.md's Components paragraph: 5-second
probe timeout, up to 10 retries, and each pillar performs its own
internal health analysis and returns a full report, not a bare ping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from common.uds_client import client_for_socket

# Delay between probe retries. Distinct from, and much shorter than, the
# 1-minute-apart crash-*relaunch* interval in docs/milestones.md M1 —
# that number governs how often Core tries to bring a dead process back;
# this one only governs how quickly it re-checks an HTTP call that may
# simply not have answered yet. Not specified by the source docs beyond
# "up to 10 retries" — an implementation-time default.
DEFAULT_RETRY_DELAY_SECONDS = 0.5


@dataclass
class HealthResult:
    ok: bool
    attempts: int
    report: dict[str, Any] | None = None
    error: str | None = None


def probe_health(
    socket_path: Path,
    *,
    timeout: float = 5.0,
    max_retries: int = 10,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
    sleep=time.sleep,
) -> HealthResult:
    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with client_for_socket(socket_path, timeout=timeout) as client:
                response = client.get("/v1/health")
            if response.status_code == 200:
                return HealthResult(ok=True, attempts=attempt, report=response.json())
            last_error = f"status {response.status_code}"
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            last_error = str(exc)

        if attempt < max_retries:
            sleep(retry_delay)

    return HealthResult(ok=False, attempts=max_retries, error=last_error)
