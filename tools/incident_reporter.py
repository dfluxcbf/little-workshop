"""Vendorable exception -> incident reporter for a *child* project's own FastAPI
app (little-meals, little-home, ...) — copy this one file into the project's
own `src/<project>/incident_reporter.py`, matching this project's existing
"no shared Python import path between two projects' own trees" rule (the same
discipline `dashboard/client.py`, `cli/client.py` and `mcp_server/client.py`
already keep between little-workshop's own pillars, extended here across
repos: a child project vendors its own copy rather than depending on
little-workshop as a package).

Usage, in the child project's own `app.py`:

    from little_meals.incident_reporter import install_incident_reporting
    app = FastAPI(...)
    install_incident_reporting(app, project_id="little-meals")

An unhandled exception from any route handler is reported, best-effort and
silent, to little-workshop's Agentic Development pillar as an incident
awaiting the user's confirmation — nothing is ever auto-dispatched. Code that
raises an exception as part of its own normal, expected control flow should
wrap that block in `no_auto_incident()` so it's never reported as a bug.

Zero dependencies beyond `httpx`, which every project in this ecosystem
already carries for its own little-workshop client glue (`version_client.py`,
`requirements_client.py`, ...); if a project has neither, vendor
`_post_over_uds` with the stdlib `http.client`/`socket` instead.
"""

from __future__ import annotations

import contextvars
import os
import traceback
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

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


def _agent_socket_path() -> Path:
    """little-workshop's own well-known socket layout
    (`common/paths.py`'s `Paths.socket_path`) — overridable for a host where
    little-workshop runs somewhere other than `~/.local/state/little-workshop`."""
    override = os.environ.get("LITTLE_WORKSHOP_AGENT_SOCKET")
    if override:
        return Path(override)
    state_home = Path(os.environ.get("LITTLE_WORKSHOP_STATE_HOME", "~/.local/state/little-workshop")).expanduser()
    return state_home / "sockets" / "agent.sock"


def report_incident(*, project_id: str, context: str, exc: BaseException, component: str = "server") -> None:
    """Best-effort and silent: a crash reporter that could itself raise, block,
    or fail loudly would only make the situation it's reporting on worse —
    if little-workshop is unreachable, the exception is simply not reported."""
    if _SUPPRESSED.get():
        return
    socket_path = _agent_socket_path()
    if not socket_path.exists():
        return
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-TRACEBACK_LIMIT:]
    try:
        transport = httpx.HTTPTransport(uds=str(socket_path))
        with httpx.Client(transport=transport, base_url="http://agent", timeout=3.0) as client:
            client.post("/v1/incidents", json={
                "project_id": project_id, "pillar": component, "context": context,
                "exception_type": type(exc).__name__, "message": str(exc)[:500], "traceback": detail,
            })
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        pass


def install_incident_reporting(app: FastAPI, *, project_id: str) -> None:
    """Every route handler's unhandled exception is reported once, then
    re-raised unchanged — this only adds the report as a side effect; the
    app's own error response (a 500) is exactly what it would have been
    otherwise."""

    class _IncidentReportingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            try:
                return await call_next(request)
            except Exception as exc:
                report_incident(project_id=project_id, context=f"{request.method} {request.url.path}", exc=exc, component="server")
                raise

    app.add_middleware(_IncidentReportingMiddleware)
