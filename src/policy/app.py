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
from policy.models import CheckOutcome, CreatedBy, GatePoint, Policy, PolicyCheckResult, PolicyWaiver, ScopeLevel
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


def _parse_gate_points(raw: object) -> tuple[list[GatePoint], str | None]:
    """Which categories does this policy gate — never required (a
    policy that gates nothing is the default, matching every existing
    policy created before this existed); invalid category names are
    rejected outright, never silently dropped."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "'gate_points' must be a list"
    try:
        return [GatePoint(g) for g in raw], None
    except ValueError as exc:
        return [], f"invalid gate_point: {exc}"


def _validate_gate_points_against_target(gate_points: list[GatePoint], target_pillar: str) -> str | None:
    """`project_registration` can only ever be framework-bound
    (`target_pillar == "policy"`) — resolved 2026-09-17 (`docs/
    open_questions.md`'s `OQ-15`): the project it would gate doesn't
    exist in the registry yet at the moment any project-bound check
    could try to mount its checkout, a genuine chicken-and-egg problem
    a project-bound policy can never resolve. Every other gate point is
    free to be either."""
    if GatePoint.PROJECT_REGISTRATION in gate_points and target_pillar != PILLAR_ITSELF:
        return (
            "gate_points: 'project_registration' may only be used on a framework-bound policy "
            f"(target_pillar == {PILLAR_ITSELF!r}) — a project-bound policy has no project to check yet"
        )
    return None


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

        gate_points, gate_points_error = _parse_gate_points(request.params.get("gate_points"))
        if gate_points_error:
            return {"outcome": "failed", "error": gate_points_error}
        gate_points_target_error = _validate_gate_points_against_target(gate_points, target_pillar)
        if gate_points_target_error:
            return {"outcome": "failed", "error": gate_points_target_error}

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
                "gate_points": [g.value for g in gate_points],
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
            existing = db.get_policy(conn, policy_id) if policy_id is not None else None
            if policy_id is not None and existing is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found — cannot edit"}

            if "gate_points" in request.params:
                gate_points, gate_points_error = _parse_gate_points(request.params.get("gate_points"))
                if gate_points_error:
                    return {"outcome": "failed", "error": gate_points_error}
            else:
                # An EARS-text/script edit never silently strips an
                # already-configured gating behavior — only
                # policy.configure changes gate_points on its own.
                gate_points = existing.gate_points if existing is not None else []

            gate_points_target_error = _validate_gate_points_against_target(gate_points, target_pillar)
            if gate_points_target_error:
                return {"outcome": "failed", "error": gate_points_target_error}

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
                gate_points=gate_points,
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

    # -- usability: enable/disable and gating behavior, without re-authoring ----

    @app.post("/v1/jobs/policy.configure")
    def job_configure(request: JobRequest) -> dict:
        """Toggling `enabled` or choosing `gate_points` — deliberately
        never re-runs any gate: neither field is about the requirement's
        meaning, so nothing here touches `ears_text`/`test_script`/`topic`."""
        policy_id = request.params.get("policy_id")
        if not policy_id:
            return {"outcome": "failed", "error": "missing 'policy_id'"}
        with lock:
            policy = db.get_policy(conn, policy_id)
            if policy is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}

            enabled = request.params.get("enabled")
            gate_points = None
            if "gate_points" in request.params:
                gate_points, gate_points_error = _parse_gate_points(request.params.get("gate_points"))
                if gate_points_error:
                    return {"outcome": "failed", "error": gate_points_error}
                gate_points_target_error = _validate_gate_points_against_target(gate_points, policy.target_pillar)
                if gate_points_target_error:
                    return {"outcome": "failed", "error": gate_points_target_error}

            db.set_policy_configuration(conn, policy_id, enabled=enabled, gate_points=gate_points)
            updated = db.get_policy(conn, policy_id)
        return {"outcome": "succeeded", "result": updated.model_dump(mode="json")}

    @app.post("/v1/jobs/policy.waiver_grant")
    def job_waiver_grant(request: JobRequest) -> dict:
        """An explicit, auditable exception — never a silent bypass.
        `reason` is required: a waiver with no stated reason is exactly
        the kind of casual runtime toggle this pillar's whole design
        exists to avoid."""
        error = _require(request.params, "policy_id", "reason") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        policy_id = request.params["policy_id"]
        with lock:
            if db.get_policy(conn, policy_id) is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}
            if db.find_waiver(conn, policy_id=policy_id, project_id=request.project_id) is not None:
                return {"outcome": "failed", "error": f"project {request.project_id!r} already holds a waiver against policy {policy_id!r}"}
            waiver = PolicyWaiver(id=db.new_id(), policy_id=policy_id, project_id=request.project_id, reason=request.params["reason"])
            db.insert_waiver(conn, waiver)
        return {"outcome": "succeeded", "result": waiver.model_dump(mode="json")}

    @app.post("/v1/jobs/policy.waiver_revoke")
    def job_waiver_revoke(request: JobRequest) -> dict:
        waiver_id = request.params.get("waiver_id")
        if not waiver_id:
            return {"outcome": "failed", "error": "missing 'waiver_id'"}
        with lock:
            if db.get_waiver(conn, waiver_id) is None:
                return {"outcome": "failed", "error": f"waiver {waiver_id!r} not found"}
            db.delete_waiver(conn, waiver_id)
        return {"outcome": "succeeded", "result": {"waiver_id": waiver_id}}

    # -- enforcement ------------------------------------------------------------

    def _mounts_for_policy(policy: Policy, project_id: str) -> dict[Path, str | tuple[str, ...]]:
        if policy.scope_level == ScopeLevel.ALL_PROJECTS and policy.target_pillar == PILLAR_ITSELF:
            # framework-bound: little-workshop's own enumerated source/docs
            repo_root = Path(__file__).resolve().parents[2]
            return {repo_root / "src": "/framework/src", repo_root / "docs": "/framework/docs"}
        project_dir = Path(resolve_project(paths, project_id)["path"])
        project_files_dir = paths.project_dir(project_id)
        mounts = sandbox.git_dir_mount(project_dir)
        mounts[project_dir] = ("/project", *((mounts[project_dir],) if project_dir in mounts else ()))
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

    @app.get("/v1/policies/gating-for-job")
    def get_gating_for_job(project_id: str, categories: str) -> list[dict]:
        """Every currently-winning policy, for this project, whose own
        `gate_points` intersects the requested categories — Orchestration's
        own entry point for "which policies, if any, gate this Job."
        Disabled and waived policies are already excluded by
        `resolve_winning_policy` itself; a Job can now be gated by more
        than one policy at once (one per topic), never just the single
        fixed mapping the original design had."""
        requested = {GatePoint(c) for c in categories.split(",") if c}
        with lock:
            topics = {p.topic for p in db.list_policies(conn) if requested & set(p.gate_points)}
            winners = []
            for topic in sorted(topics):
                winner = resolve_winning_policy(
                    conn, topic=topic, project_id=project_id, project_sets_for_project=project_sets_for_project,
                )
                if winner is not None and requested & set(winner.gate_points):
                    winners.append(winner)
        return [w.model_dump(mode="json") for w in winners]

    @app.get("/v1/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict:
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="policy not found")
        return policy.model_dump(mode="json")

    @app.get("/v1/policy_waivers")
    def list_waivers(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        with lock:
            return [w.model_dump(mode="json") for w in db.list_waivers(conn, policy_id=policy_id, project_id=project_id)]

    @app.get("/v1/policy_waivers/{waiver_id}")
    def get_waiver(waiver_id: str) -> dict:
        with lock:
            waiver = db.get_waiver(conn, waiver_id)
        if waiver is None:
            raise HTTPException(status_code=404, detail="waiver not found")
        return waiver.model_dump(mode="json")

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
