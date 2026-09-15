"""The minimal reference pillar docs/milestones.md M1 calls for: "a
trivial health endpoint + one no-op Job type" — built to prove Core and
Orchestration end to end, not a real pillar's own implementation.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def build_app() -> FastAPI:
    app = FastAPI(title="reference pillar (M1 proof only)")

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True, "detail": "reference pillar always healthy"}

    @app.post("/v1/jobs/noop")
    def noop(request: JobRequest) -> dict:
        return {"outcome": "succeeded", "result": {"echoed_params": request.params}}

    @app.post("/v1/jobs/always_fail")
    def always_fail(request: JobRequest) -> dict:
        return {"outcome": "failed", "error": "reference pillar's deliberate failure job type"}

    return app
