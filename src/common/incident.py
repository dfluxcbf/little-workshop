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

Three kinds of incident: an `exception` (a request's or a background thread's),
a `crash` (a pillar process that died and was restarted) and `unexpected`
behaviour no exception names (a Job stuck running past its limit). The last two
are reported with `report_problem`, which needs no exception.
"""

from __future__ import annotations

import contextvars
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

_SUPPRESSED: contextvars.ContextVar[bool] = contextvars.ContextVar("incident_suppressed", default=False)
TRACEBACK_LIMIT = 4000
KIND_EXCEPTION, KIND_CRASH, KIND_UNEXPECTED = "exception", "crash", "unexpected"


@contextmanager
def no_auto_incident():
    """Wrap code that raises exceptions as part of its own normal handling —
    nothing inside this block is ever turned into an automatic bug task."""
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def _post(socket_path: Path | None, body: dict) -> None:
    if socket_path is None:
        return
    try:
        with client_for_socket(socket_path, timeout=3.0) as client:
            client.post("/v1/incidents", json=body)
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        pass


def report_incident(paths: Paths, *, project_id: str, pillar: str, context: str, exc: BaseException) -> None:
    """Best-effort and silent: a crash reporter that could itself raise, block,
    or fail loudly would only make the situation it's reporting on worse."""
    if _SUPPRESSED.get():
        return
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-TRACEBACK_LIMIT:]
    _post(resolve_pillar_socket(paths, "agent", timeout=2.0), {
        "project_id": project_id, "pillar": pillar, "context": context,
        "exception_type": type(exc).__name__, "message": str(exc)[:500], "traceback": detail,
    })


def report_problem(
    target: Paths | Path | None, *, pillar: str, context: str, kind: str, title: str, message: str, detail: str = "",
    project_id: str = "little-workshop",
) -> None:
    """An incident no exception names: `title` stands where an exception's type would, `detail`
    where its traceback would. `target` is the agent's socket, or the paths to look it up with
    (None: there is nowhere to report to). Best effort and silent, as `report_incident`."""
    socket_path = resolve_pillar_socket(target, "agent", timeout=2.0) if isinstance(target, Paths) else target
    _post(socket_path, {
        "project_id": project_id, "pillar": pillar, "context": context, "kind": kind,
        "exception_type": title, "message": message[:500], "traceback": detail[-TRACEBACK_LIMIT:],
    })


def install_thread_incident_reporting(paths: Paths, *, pillar: str, project_id: str = "little-workshop") -> None:
    """Report every exception that escapes a background thread of this process (a Job's worker, a
    scheduler loop), then hand it on to the hook that was there before. Installing again replaces
    nothing: the process has one hook. A thread that ends with SystemExit has not crashed."""
    previous = threading.excepthook
    if getattr(previous, "incident_pillar", None) is not None:
        return

    def hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            name = args.thread.name if args.thread is not None else "unknown"
            report_incident(paths, project_id=project_id, pillar=pillar, context=f"background thread {name}", exc=args.exc_value)
        previous(args)

    hook.incident_pillar = pillar  # type: ignore[attr-defined]
    threading.excepthook = hook


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
    install_thread_incident_reporting(paths, pillar=pillar, project_id=project_id)
