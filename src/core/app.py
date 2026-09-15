"""Core's own small HTTP-over-Unix-socket interface, at
`Paths.core_socket_path`. Not a Pillar Interface (Core is not a
pillar), but the CLI needs *some* way to query Core's live registry, so
it gets the same transport every pillar uses, for the same reasons
(docs/architecture.md's Pillar Interface section).
"""

from __future__ import annotations

from fastapi import FastAPI

from core.supervisor import Supervisor


def build_app(supervisor: Supervisor) -> FastAPI:
    app = FastAPI(title="little-workshop Core")

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/v1/status")
    def status() -> dict:
        return {"pillars": supervisor.registry.as_status_dict()}

    @app.post("/v1/reconcile")
    def reconcile() -> dict:
        supervisor.reconcile_once()
        return {"pillars": supervisor.registry.as_status_dict()}

    return app
