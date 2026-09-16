"""Policy & Compliance's Pillar Interface. The four-gate authoring
pipeline is two Job types — `policy.author` (Gates 1-3 plus script
drafting; never saves anything) and `policy.activate` (Gate 4: the only
path that ever writes a `policies` row, create or edit) — matching
`docs/pillars/policy_compliance.md`'s own "stays strictly proposals
until Gate 4" language. Enforcement (`policy.check_project`/
`policy.check_all`) and reads follow the same Job/GET split every other
pillar's Pillar Interface already uses.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.paths import Paths
from common.registry_client import ProjectResolutionError, resolve_project
from policy import agent_client, db, gates, registry_client, sandbox
from policy.agent_client import AgentClientError
from policy.models import CheckOutcome, CreatedBy, Policy, PolicyCheckResult, ScopeLevel
from policy.precedence import resolve_winning_policy
from policy.registry_client import RegistryReadError
from policy.sandbox import SandboxUnavailable

PILLAR_ITSELF = "policy"


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def _require(params: dict, *names: str) -> str | None:
    for name in names:
        if not params.get(name):
            return f"missing {name!r}"
    return None


def _parse_scope(params: dict) -> tuple[ScopeLevel, str | None, str | None]:
    try:
        scope_level = ScopeLevel(params["scope_level"])
    except (KeyError, ValueError):
        return None, None, "missing or invalid 'scope_level' (project|project_set|all_projects)"
    scope_target = params.get("scope_target")
    if scope_level != ScopeLevel.ALL_PROJECTS and not scope_target:
        return None, None, f"scope_level {scope_level.value!r} needs a 'scope_target'"
    if scope_level == ScopeLevel.ALL_PROJECTS:
        scope_target = None
    return scope_level, scope_target, None


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Policy & Compliance")
    lock = threading.Lock()

    def query_fn(prompt: str) -> str:
        return agent_client.query(paths, prompt)

    def project_sets_for_project(project_id: str) -> list[str]:
        try:
            return registry_client.project_sets_containing(paths, project_id)
        except RegistryReadError:
            # Degrade gracefully: a framework or all-projects policy must
            # still resolve even if Registry & Portfolio is unreachable
            # right now — only project-set-scoped precedence is unavailable.
            return []

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = len(db.list_policies(conn))
        return {"ok": True, "policy_count": count}

    # -- authoring pipeline: Gates 1-3 + script drafting (never saves) -------

    @app.post("/v1/jobs/policy.author")
    def job_author(request: JobRequest) -> dict:
        error = _require(request.params, "ears_text", "topic", "target_pillar", "created_by")
        if error:
            return {"outcome": "failed", "error": error}
        scope_level, scope_target, scope_error = _parse_scope(request.params)
        if scope_error:
            return {"outcome": "failed", "error": scope_error}
        target_pillar = request.params["target_pillar"]
        try:
            created_by = CreatedBy(request.params["created_by"])
        except ValueError:
            return {"outcome": "failed", "error": "invalid 'created_by' (user|framework)"}

        if target_pillar == PILLAR_ITSELF and created_by != CreatedBy.FRAMEWORK:
            return {"outcome": "failed", "error": "a user policy may never target Policy & Compliance itself"}

        exclude_id = request.params.get("policy_id")  # editing: exclude the policy's own prior version

        try:
            gate1 = gates.gate1_ears_check(query_fn, request.params["ears_text"])
            gate2 = gates.gate2_enforceability_check(query_fn, gate1.ears_text)
        except AgentClientError as exc:
            return {"outcome": "failed", "error": f"authoring pipeline could not reach Agentic Development: {exc}"}

        if not gate2.enforceable:
            return {"outcome": "failed", "error": f"Gate 2 rejected: not mechanically enforceable — {gate2.reason}"}

        topic = request.params["topic"]
        with lock:
            structural = db.find_by_scope(conn, topic=topic, scope_level=scope_level, scope_target=scope_target, exclude_id=exclude_id)
            if structural is not None:
                return {"outcome": "failed", "error": f"Gate 3a rejected: a policy already exists at this exact scope+topic (id={structural.id})"}
            ownership = db.find_topic_owner_conflict(conn, topic=topic, target_pillar=target_pillar, exclude_id=exclude_id)
            if ownership is not None:
                return {
                    "outcome": "failed",
                    "error": f"Gate 3a rejected: topic {topic!r} already targets {ownership.target_pillar!r} (policy id={ownership.id})",
                }
            existing = [p for p in db.list_policies(conn) if p.id != exclude_id]

        try:
            gate3b = gates.gate3b_conflict_check(query_fn, gate1.ears_text, existing)
        except AgentClientError as exc:
            return {"outcome": "failed", "error": f"authoring pipeline could not reach Agentic Development: {exc}"}
        if gate3b.conflicts:
            return {
                "outcome": "failed",
                "error": f"Gate 3b rejected: conflicts with existing topic {gate3b.conflicting_topic!r} — {gate3b.detail}",
            }

        project_bound = target_pillar != PILLAR_ITSELF
        try:
            test_script = gates.generate_test_script(query_fn, gate1.ears_text, project_bound=project_bound)
        except AgentClientError as exc:
            return {"outcome": "failed", "error": f"authoring pipeline could not reach Agentic Development: {exc}"}

        return {
            "outcome": "succeeded",
            "result": {
                "ears_text": gate1.ears_text, "ears_refined": gate1.refined, "test_script": test_script,
                "topic": topic, "target_pillar": target_pillar,
                "scope_level": scope_level.value, "scope_target": scope_target, "created_by": created_by.value,
            },
        }

    # -- Gate 4: the only path that ever saves a policy row -----------------

    @app.post("/v1/jobs/policy.activate")
    def job_activate(request: JobRequest) -> dict:
        error = _require(request.params, "ears_text", "test_script", "topic", "target_pillar", "created_by")
        if error:
            return {"outcome": "failed", "error": error}
        if not request.params.get("approved"):
            return {"outcome": "failed", "error": "Gate 4 requires explicit human approval ('approved': true) — nothing is saved otherwise"}

        scope_level, scope_target, scope_error = _parse_scope(request.params)
        if scope_error:
            return {"outcome": "failed", "error": scope_error}
        target_pillar = request.params["target_pillar"]
        try:
            created_by = CreatedBy(request.params["created_by"])
        except ValueError:
            return {"outcome": "failed", "error": "invalid 'created_by' (user|framework)"}

        if target_pillar == PILLAR_ITSELF and created_by != CreatedBy.FRAMEWORK:
            return {"outcome": "failed", "error": "a user policy may never target Policy & Compliance itself"}

        if created_by == CreatedBy.FRAMEWORK:
            scope_level, scope_target = ScopeLevel.ALL_PROJECTS, None

        policy_id = request.params.get("policy_id")
        topic = request.params["topic"]

        with lock:
            if policy_id is not None and db.get_policy(conn, policy_id) is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found — cannot edit"}

            structural = db.find_by_scope(conn, topic=topic, scope_level=scope_level, scope_target=scope_target, exclude_id=policy_id)
            if structural is not None:
                return {"outcome": "failed", "error": f"Gate 3a rejected: a policy already exists at this exact scope+topic (id={structural.id})"}
            ownership = db.find_topic_owner_conflict(conn, topic=topic, target_pillar=target_pillar, exclude_id=policy_id)
            if ownership is not None:
                return {
                    "outcome": "failed",
                    "error": f"Gate 3a rejected: topic {topic!r} already targets {ownership.target_pillar!r} (policy id={ownership.id})",
                }

            policy = Policy(
                id=policy_id or db.new_id(), topic=topic, target_pillar=target_pillar,
                ears_text=request.params["ears_text"], test_script=request.params["test_script"],
                scope_level=scope_level, scope_target=scope_target, created_by=created_by,
            )
            if policy_id is not None:
                db.replace_policy(conn, policy)
                policy = db.get_policy(conn, policy_id)
            else:
                db.insert_policy(conn, policy)
        return {"outcome": "succeeded", "result": policy.model_dump(mode="json")}

    @app.post("/v1/jobs/policy.remove")
    def job_remove(request: JobRequest) -> dict:
        policy_id = request.params.get("policy_id")
        if not policy_id:
            return {"outcome": "failed", "error": "missing 'policy_id'"}
        with lock:
            if db.get_policy(conn, policy_id) is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}
            db.delete_policy(conn, policy_id)
        return {"outcome": "succeeded", "result": {"policy_id": policy_id}}

    # -- enforcement ------------------------------------------------------------

    def _mounts_for_policy(policy: Policy, project_id: str) -> dict[Path, str]:
        if policy.scope_level == ScopeLevel.ALL_PROJECTS and policy.target_pillar == PILLAR_ITSELF:
            # framework-bound: little-workshop's own enumerated source/docs
            repo_root = Path(__file__).resolve().parents[2]
            return {repo_root / "src": "/framework/src", repo_root / "docs": "/framework/docs"}
        project_dir = Path(resolve_project(paths, project_id)["path"])
        project_files_dir = paths.project_dir(project_id)
        mounts = {project_dir: "/project"}
        if project_files_dir.exists():
            mounts[project_files_dir] = "/project-files"
        return mounts

    @app.post("/v1/jobs/policy.check_project")
    def job_check_project(request: JobRequest) -> dict:
        error = _require(request.params, "policy_id") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        policy_id = request.params["policy_id"]
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}

        try:
            mounts = _mounts_for_policy(policy, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        try:
            sandbox_result = sandbox.run_policy_check(policy.test_script, mounts)
        except SandboxUnavailable as exc:
            # No verdict at all — a framework-level fault, never
            # pending_confirmation: nothing was determined to be a
            # violation, only that the check itself is broken.
            return {"outcome": "failed", "error": f"policy check did not complete: {exc}"}

        result = PolicyCheckResult(
            id=db.new_id(), policy_id=policy_id, project_id=request.project_id,
            outcome=sandbox_result.outcome, detail=sandbox_result.detail, job_id=request.job_id,
        )
        with lock:
            db.insert_check_result(conn, result)
        # Always "succeeded" at the Job level — a determined pass/fail/
        # not_applicable verdict is success for the check itself; only an
        # unreachable dependency or the sandbox's own 30-minute ceiling is
        # a genuine Job failure (see the SandboxUnavailable branch above).
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/policy.check_all")
    def job_check_all(request: JobRequest) -> dict:
        policy_id = request.params.get("policy_id")
        if not policy_id:
            return {"outcome": "failed", "error": "missing 'policy_id'"}
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}

        try:
            if policy.scope_level == ScopeLevel.PROJECT:
                project_ids = [policy.scope_target]
            elif policy.scope_level == ScopeLevel.PROJECT_SET:
                project_ids = registry_client.project_set_members(paths, policy.scope_target)
            else:
                project_ids = registry_client.list_active_project_ids(paths)
        except RegistryReadError as exc:
            return {"outcome": "failed", "error": str(exc)}

        from common.pillar_socket import resolve_pillar_socket
        from common.uds_client import client_for_socket

        dispatched = []
        socket_path = resolve_pillar_socket(paths, "orchestration", timeout=5.0)
        if socket_path is not None:
            for project_id in project_ids:
                try:
                    with client_for_socket(socket_path, timeout=10.0) as client:
                        response = client.post("/v1/jobs", json={
                            "type": "policy.check_project", "target_pillar": PILLAR_ITSELF, "project_id": project_id,
                            "params": {"policy_id": policy_id}, "queue_key": None, "depends_on": [],
                            "created_by": "policy.check_all",
                        })
                    if response.status_code == 200:
                        dispatched.append(response.json()["id"])
                except Exception:  # noqa: BLE001 — best-effort fan-out, never let one bad socket abort the rest
                    continue
        return {"outcome": "succeeded", "result": {"dispatched_job_ids": dispatched, "project_ids": project_ids}}

    # -- reads ----------------------------------------------------------------

    @app.get("/v1/policies")
    def list_policies(topic: str | None = None) -> list[dict]:
        with lock:
            return [p.model_dump(mode="json") for p in db.list_policies(conn, topic=topic)]

    @app.get("/v1/policies/winning")
    def get_winning_policy(topic: str, project_id: str) -> dict:
        with lock:
            policy = resolve_winning_policy(
                conn, topic=topic, project_id=project_id, project_sets_for_project=project_sets_for_project,
            )
        return {"topic": topic, "project_id": project_id, "policy": policy.model_dump(mode="json") if policy else None}

    @app.get("/v1/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict:
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="policy not found")
        return policy.model_dump(mode="json")

    @app.get("/v1/policy_check_results")
    def list_check_results(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        with lock:
            return [r.model_dump(mode="json") for r in db.list_check_results(conn, policy_id=policy_id, project_id=project_id)]

    @app.get("/v1/policy_check_results/latest")
    def get_latest_check_result(policy_id: str, project_id: str) -> dict:
        with lock:
            result = db.latest_check_result(conn, policy_id=policy_id, project_id=project_id)
        return {"policy_id": policy_id, "project_id": project_id, "result": result.model_dump(mode="json") if result else None}

    return app
