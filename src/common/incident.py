"""Shared exception -> incident reporting, installed into every pillar's own
FastAPI app. An unhandled exception is reported to Agentic Development as an
incident awaiting the user's confirmation — never dispatched on its own, the
same "propose, a human decides" discipline every other automated write in
this project already follows (policy drafts, redeploy confirmations).

Reporting is deliberately NOT a Job through Orchestration: a crash reporter
must survive Orchestration itself being unreachable, slow, or mid-restart,
and must never delay or fail the request that triggered it. It's a direct,
best-effort call to Agentic Development's own Pillar Interface — a
documented, narrow exception to "every mutation is a Job," matching this
project's own precedent for a handful of other things that can't wait
(e.g. `version.stabilize`'s own direct push).

Code that expects a given exception and handles it as part of its own normal
control flow marks the block with `no_auto_incident()` so a known, deliberate
exception never becomes a bug report.
"""

from __future__ import annotations

import contextvars
import traceback
from contextlib import contextmanager

import httpx
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar("incident_suppressed", default=False)
TRACEBACK_LIMIT = 4000


@contextmanager
def no_auto_incident():
    """Wrap code that raises exceptions as part of its own normal handling —
    nothing inside this block is ever turned into an automatic bug task."""
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def report_incident(paths: Paths, *, project_id: str, pillar: str, context: str, exc: BaseException) -> None:
    """Best-effort and silent: a crash reporter that could itself raise, block,
    or fail loudly would only make the situation it's reporting on worse."""
    if _SUPPRESSED.get():
        return
    socket_path = resolve_pillar_socket(paths, "agent", timeout=2.0)
    if socket_path is None:
        return
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-TRACEBACK_LIMIT:]
    try:
        with client_for_socket(socket_path, timeout=3.0) as client:
            client.post("/v1/incidents", json={
                "project_id": project_id, "pillar": pillar, "context": context,
                "exception_type": type(exc).__name__, "message": str(exc)[:500], "traceback": detail,
            })
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        pass


def install_incident_reporting(app: FastAPI, paths: Paths, *, pillar: str, project_id: str = "little-workshop") -> None:
    """Every route handler's unhandled exception is reported once, then
    re-raised unchanged — this only adds the report as a side effect; the
    pillar's own error response (a 500, or a test's own exception
    propagation) is exactly what it would have been otherwise."""

    class _IncidentReportingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            try:
                return await call_next(request)
            except Exception as exc:
                report_incident(
                    paths, project_id=project_id, pillar=pillar,
                    context=f"{request.method} {request.url.path}", exc=exc,
                )
                raise

    app.add_middleware(_IncidentReportingMiddleware)
