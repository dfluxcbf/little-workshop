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
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.paths import Paths
from common.incident import install_incident_reporting
from common.pillar_socket import resolve_pillar_socket
from common.registry_client import ProjectResolutionError, resolve_project
from common.uds_client import client_for_socket
from policy import agent_client, db, gates, registry_client, sandbox
from policy.agent_client import AgentClientError
from policy.models import (
    CheckOutcome, CreatedBy, DraftStatus, GatePoint, Policy, PolicyCheckResult, PolicyDraft, PolicyProject, ScopeLevel,
)
from policy.precedence import gating_enabled_for, resolve_winning_policy
from policy.registry_client import RegistryReadError
from policy.sandbox import SandboxUnavailable

PILLAR_ITSELF = "policy"


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


class PendingCommit(BaseModel):
    """The commit a `commit-msg` hook is about to let through, as the hook
    itself sees it (its message, the paths it changes, its staged VERSION).
    Computed by the caller from its own index, which may be a temporary one
    git only exposes to the hook, so the sandbox never has to guess it."""
    message: str
    files: list[str] = []
    version: str | None = None


class EvaluateRequest(BaseModel):
    project_id: str
    gate_point: str
    commit: PendingCommit | None = None
    # The checkout to judge when it is not the registered one (a git worktree an agent or a
    # session commits in). Accepted only if it shares the registered checkout's repository.
    checkout: str | None = None


# A script judging a pending commit finds it here (the sandbox's `/policy/input`):
# `message`, `files` (one path per line) and, when the commit has one, `VERSION`.
COMMIT_INPUT_DIR = "/policy/input"
EVALUATE_WORKERS = 4


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


def _parse_project_entries(raw: object) -> tuple[list[PolicyProject], str | None]:
    """[{project_id, gating_enabled?}] (a bare string means gating on) -> selection,
    deduplicated by project. Anything else is rejected outright."""
    if not isinstance(raw, list):
        return [], "'projects' must be a list of {project_id, gating_enabled}"
    selection: dict[str, PolicyProject] = {}
    for entry in raw:
        if isinstance(entry, str):
            entry = {"project_id": entry}
        if not isinstance(entry, dict) or not entry.get("project_id") or not isinstance(entry["project_id"], str):
            return [], f"invalid project entry: {entry!r}"
        selection[entry["project_id"]] = PolicyProject(
            project_id=entry["project_id"], gating_enabled=bool(entry.get("gating_enabled", True)),
        )
    return list(selection.values()), None


def _validate_gate_points_against_target(gate_points: list[GatePoint], target_pillar: str) -> str | None:
    """`project_registration` can only ever be framework-bound
    (`target_pillar == "policy"`) — resolved 2026-09-17 (`docs/
    open_questions.md`'s `OQ-15`): the project it would gate doesn't
    exist in the registry yet at the moment any project-bound check
    could try to mount its checkout, a genuine chicken-and-egg problem
    a project-bound policy can never resolve. `commit` is the reverse: it
    judges a pending commit in a project checkout, so it is project-bound
    only. Every other gate point is free to be either."""
    if GatePoint.PROJECT_REGISTRATION in gate_points and target_pillar != PILLAR_ITSELF:
        return (
            "gate_points: 'project_registration' may only be used on a framework-bound policy "
            f"(target_pillar == {PILLAR_ITSELF!r}) — a project-bound policy has no project to check yet"
        )
    if GatePoint.COMMIT in gate_points and target_pillar == PILLAR_ITSELF:
        return (
            "gate_points: 'commit' judges the commit being made in a project's checkout, so only a "
            "project-bound policy may gate it"
        )
    return None


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Policy & Compliance")
    install_incident_reporting(app, paths, pillar="policy")
    lock = threading.Lock()

    def query_fn(prompt: str) -> str:
        return agent_client.query(paths, prompt)

    def _scope_project_ids(policy: Policy) -> list[str] | None:
        """The projects a policy's authoring scope covers right now, used
        only to seed its selection once. None when Registry & Portfolio
        cannot be reached (the policy stays unseeded and is retried)."""
        try:
            if policy.scope_level == ScopeLevel.PROJECT:
                return [policy.scope_target] if policy.scope_target else []
            if policy.scope_level == ScopeLevel.PROJECT_SET:
                return registry_client.project_set_members(paths, policy.scope_target)
            return registry_client.list_active_project_ids(paths)
        except RegistryReadError:
            return None

    def _seed_unseeded() -> None:
        """Every policy starts with its authoring scope as its project
        selection; from then on only `policy.set_projects` changes it."""
        with lock:
            pending = db.unseeded_policies(conn)
        for policy in pending:
            ids = _scope_project_ids(policy)
            if ids is None:
                continue
            with lock:
                db.set_policy_projects(conn, policy.id, [PolicyProject(project_id=pid) for pid in dict.fromkeys(ids)])

    _seed_unseeded()

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
            test_script = gates.generate_test_script(
                query_fn, gate1.ears_text, project_bound=project_bound,
                judges_commits=GatePoint.COMMIT in gate_points,
            )
        except AgentClientError as exc:
            return {"outcome": "failed", "error": f"authoring pipeline could not reach Agentic Development: {exc}"}

        result = {
            "ears_text": gate1.ears_text, "ears_refined": gate1.refined, "test_script": test_script,
            "topic": topic, "target_pillar": target_pillar,
            "scope_level": scope_level.value, "scope_target": scope_target, "created_by": created_by.value,
            "gate_points": [g.value for g in gate_points],
        }
        if request.params.get("save_draft"):
            # Kept for a human to review and approve in the Dashboard; still nothing enforced.
            draft, _ = _draft_from_params({**result, "source": request.params.get("source", "")})
            with lock:
                existing_draft = db.find_open_draft_by_topic(conn, topic)
                if existing_draft is not None:
                    draft.id = existing_draft.id
                    db.replace_draft_content(conn, draft)
                else:
                    db.insert_draft(conn, draft)
            result["draft_id"] = draft.id
        return {"outcome": "succeeded", "result": result}

    # -- Gate 4: the only path that ever saves a policy row -----------------

    def _save_policy(params: dict, *, projects: list[PolicyProject] | None = None) -> dict:
        """Gate 4's write: validate, then insert or edit a real policy row. The one
        place a `policies` row is ever written — `policy.activate` (CLI/MCP, given
        `approved: true`) and `policy.approve_draft` (the Dashboard) both come here.
        `projects` sets the selection explicitly; otherwise a new policy is seeded
        from its authoring scope."""
        scope_level, scope_target, scope_error = _parse_scope(params)
        if scope_error:
            return {"outcome": "failed", "error": scope_error}
        target_pillar = params["target_pillar"]
        try:
            created_by = CreatedBy(params["created_by"])
        except ValueError:
            return {"outcome": "failed", "error": "invalid 'created_by' (user|framework)"}

        if target_pillar == PILLAR_ITSELF and created_by != CreatedBy.FRAMEWORK:
            return {"outcome": "failed", "error": "a user policy may never target Policy & Compliance itself"}

        if created_by == CreatedBy.FRAMEWORK:
            scope_level, scope_target = ScopeLevel.ALL_PROJECTS, None

        policy_id = params.get("policy_id")
        topic = params["topic"]

        with lock:
            existing = db.get_policy(conn, policy_id) if policy_id is not None else None
            if policy_id is not None and existing is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found — cannot edit"}

            if "gate_points" in params:
                gate_points, gate_points_error = _parse_gate_points(params.get("gate_points"))
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
                ears_text=params["ears_text"], test_script=params["test_script"],
                scope_level=scope_level, scope_target=scope_target, created_by=created_by,
                gate_points=gate_points,
            )
            is_new = policy_id is None
            if policy_id is not None:
                db.replace_policy(conn, policy)
            else:
                db.insert_policy(conn, policy)
        if is_new and projects is not None:
            with lock:
                db.set_policy_projects(conn, policy.id, projects)
        elif is_new:
            _seed_unseeded()
        with lock:
            policy = db.get_policy(conn, policy.id)
        return {"outcome": "succeeded", "result": policy.model_dump(mode="json")}

    @app.post("/v1/jobs/policy.activate")
    def job_activate(request: JobRequest) -> dict:
        error = _require(request.params, "ears_text", "test_script", "topic", "target_pillar", "created_by")
        if error:
            return {"outcome": "failed", "error": error}
        if not request.params.get("approved"):
            return {"outcome": "failed", "error": "Gate 4 requires explicit human approval ('approved': true) — nothing is saved otherwise"}
        return _save_policy(request.params)

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

    # -- drafts: Gates 1-3 output awaiting a human's Gate 4 in the Dashboard ----

    def _draft_from_params(raw: dict) -> tuple[PolicyDraft | None, str | None]:
        missing = _require(raw, "topic", "target_pillar", "ears_text", "test_script")
        if missing:
            return None, missing
        try:
            scope_level = ScopeLevel(raw.get("scope_level", "all_projects"))
            created_by = CreatedBy(raw.get("created_by", "framework"))
        except ValueError as exc:
            return None, f"invalid draft: {exc}"
        gate_points, gate_points_error = _parse_gate_points(raw.get("gate_points"))
        if gate_points_error:
            return None, gate_points_error
        projects, projects_error = _parse_project_entries(raw.get("projects", []))
        if projects_error:
            return None, projects_error
        return PolicyDraft(
            id=db.new_id(), topic=raw["topic"], target_pillar=raw["target_pillar"], ears_text=raw["ears_text"],
            ears_refined=bool(raw.get("ears_refined", False)), test_script=raw["test_script"],
            scope_level=scope_level, scope_target=raw.get("scope_target"), created_by=created_by,
            gate_points=gate_points, projects=projects, source=str(raw.get("source", "")), notes=str(raw.get("notes", "")),
            concerns=bool(raw.get("concerns", False)),
        ), None

    @app.post("/v1/jobs/policy.draft_import")
    def job_draft_import(request: JobRequest) -> dict:
        """Bring drafts (Gates 1-3 output, e.g. `docs/pending_policy_drafts/*.json`) into
        the app for review. Idempotent: a topic that already has a pending draft is
        refreshed in place; a topic that already has a real policy is skipped."""
        raw = request.params.get("drafts")
        if not isinstance(raw, list) or not raw:
            return {"outcome": "failed", "error": "'drafts' must be a non-empty list"}
        created: list[str] = []
        updated: list[str] = []
        skipped: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                return {"outcome": "failed", "error": f"invalid draft entry: {entry!r}"}
            draft, error = _draft_from_params(entry)
            if error:
                return {"outcome": "failed", "error": f"{entry.get('topic', '?')}: {error}"}
            with lock:
                if any(p.topic == draft.topic for p in db.list_policies(conn)):
                    skipped.append({"topic": draft.topic, "reason": "a policy already exists for this topic"})
                    continue
                open_draft = db.find_open_draft_by_topic(conn, draft.topic)
                if open_draft is not None:
                    draft.id = open_draft.id
                    db.replace_draft_content(conn, draft)
                    updated.append(draft.topic)
                else:
                    db.insert_draft(conn, draft)
                    created.append(draft.topic)
        return {"outcome": "succeeded", "result": {"created": created, "updated": updated, "skipped": skipped}}

    @app.post("/v1/jobs/policy.approve_draft")
    def job_approve_draft(request: JobRequest) -> dict:
        """Gate 4 for a draft: the reviewer's explicit confirmation (`reviewed: true`)
        turns it into a real, active policy applying to the chosen projects. Driven by
        the Dashboard only — there is deliberately no CLI or MCP wrapper, so approval
        stays a human act inside the app."""
        draft_id = request.params.get("draft_id")
        if not draft_id:
            return {"outcome": "failed", "error": "missing 'draft_id'"}
        if request.params.get("reviewed") is not True:
            return {"outcome": "failed", "error": "approval needs the reviewer's explicit confirmation ('reviewed': true)"}
        selection, projects_error = _parse_project_entries(request.params.get("projects", []))
        if projects_error:
            return {"outcome": "failed", "error": projects_error}
        with lock:
            draft = db.get_draft(conn, draft_id)
        if draft is None:
            return {"outcome": "failed", "error": f"draft {draft_id!r} not found"}
        if draft.status != DraftStatus.PENDING:
            return {"outcome": "failed", "error": f"draft is {draft.status.value}, not pending"}
        if "gate_points" in request.params:
            gate_points, gate_points_error = _parse_gate_points(request.params.get("gate_points"))
            if gate_points_error:
                return {"outcome": "failed", "error": gate_points_error}
        else:
            gate_points = draft.gate_points
        outcome = _save_policy({
            "ears_text": draft.ears_text, "test_script": draft.test_script, "topic": draft.topic,
            "target_pillar": draft.target_pillar, "scope_level": draft.scope_level.value,
            "scope_target": draft.scope_target, "created_by": draft.created_by.value,
            "gate_points": [g.value for g in gate_points],
        }, projects=selection)
        if outcome.get("outcome") != "succeeded":
            return outcome
        with lock:
            db.set_draft_decision(conn, draft_id, status=DraftStatus.APPROVED, policy_id=outcome["result"]["id"])
        return {"outcome": "succeeded", "result": {**outcome["result"], "draft_id": draft_id}}

    @app.post("/v1/jobs/policy.reject_draft")
    def job_reject_draft(request: JobRequest) -> dict:
        draft_id = request.params.get("draft_id")
        if not draft_id:
            return {"outcome": "failed", "error": "missing 'draft_id'"}
        with lock:
            draft = db.get_draft(conn, draft_id)
            if draft is None:
                return {"outcome": "failed", "error": f"draft {draft_id!r} not found"}
            if draft.status != DraftStatus.PENDING:
                return {"outcome": "failed", "error": f"draft is {draft.status.value}, not pending"}
            db.set_draft_decision(conn, draft_id, status=DraftStatus.REJECTED, reason=str(request.params.get("reason", "")).strip())
            return {"outcome": "succeeded", "result": db.get_draft(conn, draft_id).model_dump(mode="json")}

    @app.post("/v1/jobs/policy.restore_draft")
    def job_restore_draft(request: JobRequest) -> dict:
        draft_id = request.params.get("draft_id")
        if not draft_id:
            return {"outcome": "failed", "error": "missing 'draft_id'"}
        with lock:
            draft = db.get_draft(conn, draft_id)
            if draft is None:
                return {"outcome": "failed", "error": f"draft {draft_id!r} not found"}
            if draft.status != DraftStatus.REJECTED:
                return {"outcome": "failed", "error": f"draft is {draft.status.value}, only a rejected draft can be restored"}
            db.set_draft_decision(conn, draft_id, status=DraftStatus.PENDING)
            return {"outcome": "succeeded", "result": db.get_draft(conn, draft_id).model_dump(mode="json")}

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

    @app.post("/v1/jobs/policy.set_projects")
    def job_set_projects(request: JobRequest) -> dict:
        """The projects this policy applies to, replacing the whole
        selection: each entry is `{"project_id": ..., "gating_enabled": bool}`
        (gating defaults on). A project left out is deselected. Where a
        failure would block is the policy's own `gate_points`, never chosen
        per project — only whether gating is on for that project."""
        policy_id = request.params.get("policy_id")
        raw = request.params.get("projects")
        if not policy_id:
            return {"outcome": "failed", "error": "missing 'policy_id'"}
        selection, projects_error = _parse_project_entries(raw)
        if projects_error:
            return {"outcome": "failed", "error": projects_error}
        with lock:
            if db.get_policy(conn, policy_id) is None:
                return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}
            db.set_policy_projects(conn, policy_id, selection)
            updated = db.get_policy(conn, policy_id)
        return {"outcome": "succeeded", "result": updated.model_dump(mode="json")}

    # -- enforcement ------------------------------------------------------------

    def _project_root_for_policy(policy: Policy, project_id: str) -> Path:
        if policy.scope_level == ScopeLevel.ALL_PROJECTS and policy.target_pillar == PILLAR_ITSELF:
            # framework-bound: little-workshop's own checkout
            return Path(__file__).resolve().parents[2]
        return Path(resolve_project(paths, project_id)["path"])

    def _mounts_for_policy(
        policy: Policy, project_id: str, checkout: Path | None = None,
    ) -> dict[Path, str | tuple[str, ...]]:
        if policy.scope_level == ScopeLevel.ALL_PROJECTS and policy.target_pillar == PILLAR_ITSELF:
            # framework-bound: little-workshop's own enumerated source/docs
            repo_root = _project_root_for_policy(policy, project_id)
            return {repo_root / "src": "/framework/src", repo_root / "docs": "/framework/docs"}
        project_dir = checkout or _project_root_for_policy(policy, project_id)
        project_files_dir = paths.project_dir(project_id)
        mounts = sandbox.git_dir_mount(project_dir)
        mounts[project_dir] = ("/project", *((mounts[project_dir],) if project_dir in mounts else ()))
        if project_files_dir.exists():
            mounts[project_files_dir] = "/project-files"
        return mounts

    def _git_common_dir(path: Path) -> Path | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 and proc.stdout.strip() else None

    def _checkout_of(project_id: str, checkout: str) -> Path:
        """`checkout` if it is a checkout of this project's own repository (the registered one,
        or a git worktree of it); a check must never read another project's files."""
        candidate = Path(checkout)
        if not candidate.is_absolute() or not candidate.is_dir():
            raise ProjectResolutionError(f"checkout {checkout!r} is not an existing absolute directory")
        registered = Path(resolve_project(paths, project_id)["path"])
        common = _git_common_dir(candidate)
        if common is None or common != _git_common_dir(registered):
            raise ProjectResolutionError(f"{checkout!r} is not a checkout of project {project_id!r}")
        return candidate.resolve()

    def _current_head_sha(repo_root: Path) -> str:
        """Cheap, host-side (no Docker) HEAD lookup — the state a
        committed-history-based policy check actually reads. Never raises:
        a repo that can't be read yet (mid-clone, detached, not a repo at
        all) just means "no fingerprint", which forces a real check."""
        try:
            proc = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def _current_check_state(policy: Policy, project_id: str) -> str:
        """Fingerprint of what policy.check_project would actually re-verify:
        the project's committed HEAD plus the policy's own last edit. Either
        one changing is a real change; neither changing means the previous
        result is still correct — see "once per change"
        (docs/pillars/policy_compliance.md)."""
        try:
            repo_root = _project_root_for_policy(policy, project_id)
        except ProjectResolutionError:
            return ""
        head_sha = _current_head_sha(repo_root)
        if not head_sha:
            return ""
        return f"{head_sha}:{policy.amended_at.isoformat()}"

    def _complete_orchestration_job(job_id: str, *, succeeded: bool, result: dict | None, error: str | None) -> None:
        """Best-effort: a real (non-skipped) sandbox check runs for up to
        30 minutes, so `job_check_project` dispatches it to a background
        thread and returns `accepted` immediately instead of blocking the
        request — the same async-Job completion path `agent.run_task`'s
        worker and M5's dispatched self-redeploy worker already use for
        the identical reason (a long-running operation that must not tie
        up the pillar's own request-handling capacity, and with it,
        `/v1/health`'s responsiveness to Core's own probe)."""
        socket_path = resolve_pillar_socket(paths, "orchestration", timeout=5.0)
        if socket_path is None:
            return
        try:
            with client_for_socket(socket_path, timeout=10.0) as client:
                client.post(f"/v1/jobs/{job_id}/complete", json={"succeeded": succeeded, "result": result, "error": error})
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            pass

    @app.post("/v1/jobs/policy.check_project")
    def job_check_project(request: JobRequest) -> dict:
        error = _require(request.params, "policy_id") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        policy_id = request.params["policy_id"]
        project_id = request.project_id
        force = bool(request.params.get("force"))
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}

        current_state = _current_check_state(policy, project_id)
        if current_state and not force:
            with lock:
                previous = db.latest_check_result(conn, policy_id=policy_id, project_id=project_id)
            if previous is not None and previous.checked_state == current_state:
                # Nothing has changed since the last verdict (same HEAD
                # commit, same policy edit) — reuse it instead of paying for
                # another Docker sandbox run. Recorded as a fresh row (its own
                # id/checked_at/job_id) so history and job linkage stay
                # accurate, but the sandbox itself never runs. Cheap and
                # synchronous — no thread needed, nothing here can block.
                result = PolicyCheckResult(
                    id=db.new_id(), policy_id=policy_id, project_id=project_id,
                    outcome=previous.outcome, detail=previous.detail, job_id=request.job_id,
                    checked_state=current_state,
                )
                with lock:
                    db.insert_check_result(conn, result)
                return {"outcome": "succeeded", "result": result.model_dump(mode="json"), "skipped": "unchanged"}

        try:
            mounts = _mounts_for_policy(policy, project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}

        def worker() -> None:
            try:
                sandbox_result = sandbox.run_policy_check(policy.test_script, mounts)
            except SandboxUnavailable as exc:
                # No verdict at all — a framework-level fault, never
                # pending_confirmation: nothing was determined to be a
                # violation, only that the check itself is broken.
                _complete_orchestration_job(
                    request.job_id, succeeded=False, result=None,
                    error=f"policy check did not complete: {exc}",
                )
                return

            result = PolicyCheckResult(
                id=db.new_id(), policy_id=policy_id, project_id=project_id,
                outcome=sandbox_result.outcome, detail=sandbox_result.detail, job_id=request.job_id,
                checked_state=current_state,
            )
            with lock:
                db.insert_check_result(conn, result)
            # Always "succeeded" at the Job level — a determined pass/fail/
            # not_applicable verdict is success for the check itself; only an
            # unreachable dependency or the sandbox's own 30-minute ceiling is
            # a genuine Job failure (see the SandboxUnavailable branch above).
            _complete_orchestration_job(request.job_id, succeeded=True, result=result.model_dump(mode="json"), error=None)

        threading.Thread(target=worker, daemon=True).start()
        return {"outcome": "accepted", "result": {"dispatched": True}}

    @app.post("/v1/jobs/policy.check_all")
    def job_check_all(request: JobRequest) -> dict:
        policy_id = request.params.get("policy_id")
        if not policy_id:
            return {"outcome": "failed", "error": "missing 'policy_id'"}
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            return {"outcome": "failed", "error": f"policy {policy_id!r} not found"}

        project_ids = [sel.project_id for sel in policy.projects]

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
            policy = resolve_winning_policy(conn, topic=topic, project_id=project_id)
        return {"topic": topic, "project_id": project_id, "policy": policy.model_dump(mode="json") if policy else None}

    def _gating_winners(project_id: str, requested: set[GatePoint]) -> list[tuple[Policy, bool]]:
        """Every currently-winning policy for this project whose own gate
        points intersect `requested`, with whether its gating is on here."""
        _seed_unseeded()
        with lock:
            topics = {p.topic for p in db.list_policies(conn) if requested & set(p.gate_points)}
            winners = []
            for topic in sorted(topics):
                winner = resolve_winning_policy(conn, topic=topic, project_id=project_id)
                if winner is not None and requested & set(winner.gate_points):
                    winners.append((winner, gating_enabled_for(winner, project_id)))
        return winners

    @app.get("/v1/policies/gating-for-job")
    def get_gating_for_job(project_id: str, categories: str) -> list[dict]:
        """Every currently-winning policy, for this project, whose own
        `gate_points` intersects the requested categories — Orchestration's
        own entry point for "which policies, if any, apply to this Job."
        Each carries `gating`: whether a failure may hold the Job. With
        gating off for this project the check is still returned so it runs
        (for analysis) — it just cannot block."""
        requested = {GatePoint(c) for c in categories.split(",") if c}
        return [{**winner.model_dump(mode="json"), "gating": gating}
                for winner, gating in _gating_winners(project_id, requested)]

    @app.post("/v1/policies/evaluate")
    def evaluate(request: EvaluateRequest) -> dict:
        """Run, now and in the sandbox, every policy that would gate
        `gate_point` for this project, and answer with each verdict and its
        reason. Nothing is recorded and nothing is held: this is the early
        warning, not the gate — the `commit-msg` hook's check of the commit
        being made (`commit`, with `commit` describing it), and a session's
        preflight before it asks for a gated action (`branch_finish`,
        `release`, ...). `blocking` lists the topics that fail with gating on,
        which is exactly what the real gate would hold."""
        try:
            gate_point = GatePoint(request.gate_point)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown gate point {request.gate_point!r}")
        if gate_point == GatePoint.COMMIT and request.commit is None:
            raise HTTPException(status_code=400, detail="gate point 'commit' needs the pending commit ('commit')")
        env: dict[str, str] = {}
        inputs: dict[str, str] = {}
        if request.commit is not None:
            env["POLICY_COMMIT_DIR"] = COMMIT_INPUT_DIR
            inputs["message"] = request.commit.message
            inputs["files"] = "\n".join(request.commit.files)
            if request.commit.version is not None:
                inputs["VERSION"] = request.commit.version

        try:
            checkout = _checkout_of(request.project_id, request.checkout) if request.checkout else None
        except ProjectResolutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        def run_one(winner: Policy, gating: bool) -> dict:
            entry = {"policy_id": winner.id, "topic": winner.topic, "ears_text": winner.ears_text, "gating": gating}
            try:
                mounts = _mounts_for_policy(winner, request.project_id, checkout)
                verdict = sandbox.run_policy_check(winner.test_script, mounts, env=env, inputs=inputs)
            except (ProjectResolutionError, SandboxUnavailable) as exc:
                return {**entry, "outcome": "error", "detail": str(exc)}
            return {**entry, "outcome": verdict.outcome.value, "detail": verdict.detail}

        winners = _gating_winners(request.project_id, {gate_point})
        with ThreadPoolExecutor(max_workers=EVALUATE_WORKERS) as pool:
            results = list(pool.map(lambda pair: run_one(*pair), winners))
        return {
            "project_id": request.project_id,
            "gate_point": gate_point.value,
            "results": results,
            "blocking": [r["topic"] for r in results if r["outcome"] == CheckOutcome.FAIL.value and r["gating"]],
            "errors": [r["topic"] for r in results if r["outcome"] == "error"],
        }

    @app.get("/v1/policies/{policy_id}")
    def get_policy(policy_id: str) -> dict:
        with lock:
            policy = db.get_policy(conn, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="policy not found")
        return policy.model_dump(mode="json")

    @app.get("/v1/policy_drafts")
    def list_policy_drafts(status: str | None = None) -> list[dict]:
        try:
            wanted = DraftStatus(status) if status else None
        except ValueError:
            raise HTTPException(status_code=400, detail="status must be pending|approved|rejected")
        with lock:
            return [d.model_dump(mode="json") for d in db.list_drafts(conn, status=wanted)]

    @app.get("/v1/policy_drafts/{draft_id}")
    def get_policy_draft(draft_id: str) -> dict:
        with lock:
            draft = db.get_draft(conn, draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="draft not found")
        return draft.model_dump(mode="json")

    @app.get("/v1/policy_projects")
    def list_policy_projects(policy_id: str | None = None, project_id: str | None = None) -> list[dict]:
        with lock:
            return db.list_policy_projects(conn, policy_id=policy_id, project_id=project_id)

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
