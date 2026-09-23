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
    # Tracks "slow"-type job ids currently accepted-but-not-done, so
    # /v1/jobs/{id}/stop has something real to interrupt — M9's own
    # end-to-end proof of a genuine `stop` call.
    running: set[str] = set()

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True, "detail": "reference pillar always healthy"}

    @app.post("/v1/jobs/noop")
    def noop(request: JobRequest) -> dict:
        return {"outcome": "succeeded", "result": {"echoed_params": request.params}}

    @app.post("/v1/jobs/always_fail")
    def always_fail(request: JobRequest) -> dict:
        return {"outcome": "failed", "error": "reference pillar's deliberate failure job type"}

    @app.post("/v1/jobs/slow")
    def slow(request: JobRequest) -> dict:
        # Accepted-but-not-done: stays RUNNING in Orchestration until a
        # test calls `POST /v1/jobs/{id}/complete` itself — the one job
        # type this reference pillar needs to let tests exercise the
        # `queue_key` mutex/depends_on machinery for real, the same
        # "accepted" async pattern M5/M6's self-redeploy and agent.run_task
        # actually use in the real pillars.
        running.add(request.job_id)
        return {"outcome": "accepted", "result": {"dispatched": True}}

    @app.post("/v1/jobs/{job_id}/stop")
    def stop(job_id: str) -> dict:
        if job_id in running:
            running.discard(job_id)
            return {"stopped": True}
        return {"stopped": False, "reason": "not a running 'slow' job"}

    return app
