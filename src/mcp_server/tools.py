"""The MCP tool catalog: one declarative `ToolSpec` per operation, each
backed by a small handler that turns MCP arguments into a Pillar Interface
call (docs/architecture.md: "translating a caller's request ... into a
Pillar Interface call against whichever pillar actually owns the answer").

Modeled directly on `little_versions.mcp`'s `ToolSpec`/schema-helper
pattern (the proven shape this ecosystem already uses for a hand-rolled,
dependency-free MCP surface) but generalized: little-versions' tools all
call one method on one API facade, so a tool only needed to carry a method
name. little-workshop's tools reach four different pillars over four
different Pillar Interfaces, so each `ToolSpec` carries its own handler
instead.

Per M7 (docs/milestones.md): "the proxy pattern established, not the final
tool surface" — this covers every mutating Job type and read endpoint
already built for Registry & Portfolio, Version Control & Release,
Deployment & Servers, and Agentic Development as of M2-M6, except the
internal-plumbing entry points (worktree provisioning, the self-redeploy/
rollback confirm-timeout watchdog and its detached workers) that exist for
other pillars to call, not for a human or agent session.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from common.paths import Paths
from mcp_server import client

JSONDict = dict[str, Any]


def _prop(json_type: str, description: str, **extra: Any) -> JSONDict:
    return {"type": json_type, "description": description, **extra}


def _schema(properties: JSONDict | None = None, required: list[str] | None = None) -> JSONDict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_PROJECT_ID = _prop("string", "The project's registered id/name in Registry & Portfolio.")
_FORCE = _prop("boolean", "Proceed even if the working tree is dirty. Default: false.")
_ACTION = _prop("string", "Resume ('continue') or cancel ('abort') a finish paused by a conflict.", enum=["continue", "abort"])
_ISSUE = _prop("integer", "Tracker issue number to link to this branch.")


@dataclasses.dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: JSONDict
    handler: Callable[[Paths, JSONDict], Any]

    def to_mcp(self) -> JSONDict:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


# ---------------------------------------------------------------------------
# Orchestration / Core
# ---------------------------------------------------------------------------

def _core_status(paths: Paths, args: JSONDict) -> Any:
    return client.core_status(paths)


def _job_list(paths: Paths, args: JSONDict) -> Any:
    return client.list_jobs(paths, status=args.get("status"))


def _job_status(paths: Paths, args: JSONDict) -> Any:
    return client.job_status(paths, args["job_id"])


# ---------------------------------------------------------------------------
# Registry & Portfolio
# ---------------------------------------------------------------------------

def _project_bootstrap(paths: Paths, args: JSONDict) -> Any:
    params = {
        "name": args["name"],
        "dest_dir": args["dest_dir"],
        "owner": args.get("owner", "dfluxcbf"),
        "description": args.get("description", ""),
        "create_github_repo": args.get("create_github_repo", False),
        "public": args.get("public", False),
        "remote_host": args.get("remote_host", "github.com"),
        "github_token": args.get("github_token"),
    }
    return client.create_job(
        paths, type="project.bootstrap", target_pillar="registry", project_id=args["name"],
        params=params, queue_key=None, depends_on=[], created_by="mcp",
    )


def _project_import(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="project.import", target_pillar="registry", project_id=args["name"],
        params={"name": args["name"], "path": args["path"]}, queue_key=None, depends_on=[], created_by="mcp",
    )


def _project_remove(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="project.remove", target_pillar="registry", project_id=args["name"],
        params={"name": args["name"]}, queue_key=args["name"], depends_on=[], created_by="mcp",
    )


def _project_list(paths: Paths, args: JSONDict) -> Any:
    return client.list_projects(paths, status=args.get("status"))


def _project_get(paths: Paths, args: JSONDict) -> Any:
    return client.get_project(paths, args["name"])


# ---------------------------------------------------------------------------
# Version Control & Release
# ---------------------------------------------------------------------------

def _version_job(paths: Paths, job_type: str, project_id: str, params: JSONDict) -> Any:
    return client.create_job(
        paths, type=job_type, target_pillar="version", project_id=project_id,
        params=params, queue_key=project_id, depends_on=[], created_by="mcp",
    )


def _version_init(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.init", args["project_id"], {
        "production_branch": args.get("production_branch"), "integration_branch": args.get("integration_branch"),
    })


def _version_topic_start(paths: Paths, args: JSONDict, prefix: str) -> Any:
    return _version_job(paths, f"version.{prefix}_start", args["project_id"], {
        "name": args["name"], "issue": args.get("issue"), "force": args.get("force", False),
    })


def _version_topic_finish(paths: Paths, args: JSONDict, prefix: str) -> Any:
    return _version_job(paths, f"version.{prefix}_finish", args["project_id"], {
        "force": args.get("force", False), "action": args.get("action"),
    })


def _version_feature_start(paths: Paths, args: JSONDict) -> Any:
    return _version_topic_start(paths, args, "feature")


def _version_feature_finish(paths: Paths, args: JSONDict) -> Any:
    return _version_topic_finish(paths, args, "feature")


def _version_improvement_start(paths: Paths, args: JSONDict) -> Any:
    return _version_topic_start(paths, args, "improvement")


def _version_improvement_finish(paths: Paths, args: JSONDict) -> Any:
    return _version_topic_finish(paths, args, "improvement")


def _version_hotfix_start(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.hotfix_start", args["project_id"], {
        "name": args["name"], "issue": args.get("issue"), "force": args.get("force", False),
    })


def _version_hotfix_finish(paths: Paths, args: JSONDict) -> Any:
    return _version_topic_finish(paths, args, "hotfix")


def _version_release_start(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.release_start", args["project_id"], {"force": args.get("force", False)})


def _version_release_candidate_cut(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.release_candidate_cut", args["project_id"], {
        "number": args.get("number"), "force": args.get("force", False),
    })


def _version_release_candidate_promote(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.release_candidate_promote", args["project_id"], {"rc_number": args["rc_number"]})


def _version_release_finish(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.release_finish", args["project_id"], {
        "force": args.get("force", False), "action": args.get("action"),
    })


def _version_support_start(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.support_start", args["project_id"], {
        "version": args["version"], "force": args.get("force", False),
    })


def _version_support_finish(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.support_finish", args["project_id"], {})


def _version_config_add_hook(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.config_add_hook", args["project_id"], {"hook_path": args["hook_path"]})


def _version_config_remove_hook(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.config_remove_hook", args["project_id"], {"hook_path": args["hook_path"]})


def _version_status(paths: Paths, args: JSONDict) -> Any:
    return client.version_status(paths, args["kind"], args["project_id"])


def _version_config(paths: Paths, args: JSONDict) -> Any:
    return client.version_config(paths, args["project_id"])


def _version_release_candidate_list(paths: Paths, args: JSONDict) -> Any:
    return client.version_release_candidates(paths, args["project_id"])


# ---------------------------------------------------------------------------
# Deployment & Servers
# ---------------------------------------------------------------------------

def _deployment_deploy(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="deployment.deploy", target_pillar="deployment", project_id=args["project_id"],
        params={"branch": args.get("branch", "dev")}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _deployment_stop(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="deployment.stop", target_pillar="deployment", project_id=args["project_id"],
        params={}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _deployment_confirm(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="deployment.confirm", target_pillar="deployment", project_id=args["project_id"],
        params={}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _deployment_seed_confirmation(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="deployment.seed_confirmation", target_pillar="deployment", project_id=args["project_id"],
        params={}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _deployment_redeploy_pillar(paths: Paths, args: JSONDict) -> Any:
    params = {"pillar": args["pillar"], "commit": args.get("commit")}
    if args.get("confirm_timeout_seconds") is not None:
        params["confirm_timeout_seconds"] = args["confirm_timeout_seconds"]
    return client.create_job(
        paths, type="deployment.redeploy_pillar", target_pillar="deployment", project_id=args["pillar"],
        params=params, queue_key=args["pillar"], depends_on=[], created_by="mcp",
    )


def _deployment_status(paths: Paths, args: JSONDict) -> Any:
    return client.deployment_status(paths, args["project_id"])


def _deployment_history(paths: Paths, args: JSONDict) -> Any:
    return client.deployment_history(paths, args["project_id"])


def _deployment_confirmation_status(paths: Paths, args: JSONDict) -> Any:
    return client.deployment_confirmation(paths, args["project_id"])


def _deployment_branches(paths: Paths, args: JSONDict) -> Any:
    return client.deployment_branches(paths, args["project_id"])


# ---------------------------------------------------------------------------
# Agentic Development
# ---------------------------------------------------------------------------

def _agent_run_task(paths: Paths, args: JSONDict) -> Any:
    params = {
        "title": args["title"],
        "intent": args.get("intent", "task"),
        "review_mode": args.get("review_mode", "manual"),
        "description": args.get("description", ""),
    }
    if args.get("branch_mode"):
        params["branching"] = {
            "mode": args["branch_mode"], "branch_type": args.get("branch_type"),
            "base": args.get("base"), "branch": args.get("branch"),
        }
    return client.create_job(
        paths, type="agent.run_task", target_pillar="agent", project_id=args["project_id"],
        params=params, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _agent_finish_task(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="agent.finish_task", target_pillar="agent", project_id=args["project_id"],
        params={"run_id": args["run_id"]}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _agent_skip_task(paths: Paths, args: JSONDict) -> Any:
    return client.create_job(
        paths, type="agent.skip_task", target_pillar="agent", project_id=args["project_id"],
        params={"run_id": args["run_id"]}, queue_key=args["project_id"], depends_on=[], created_by="mcp",
    )


def _agent_runs(paths: Paths, args: JSONDict) -> Any:
    return client.agent_runs(paths, args["project_id"], status=args.get("status"))


def _agent_run_status(paths: Paths, args: JSONDict) -> Any:
    return client.agent_run_status(paths, args["project_id"], args["run_id"])


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("workshop_core_status", "Return every pillar's enabled/active status, as Core sees it.", _schema(), _core_status),
    ToolSpec("workshop_job_list", "List Jobs known to Orchestration, optionally filtered by status.",
             _schema({"status": _prop("string", "queued|running|pending_confirmation|succeeded|failed|cancelled")}), _job_list),
    ToolSpec("workshop_job_status", "Get one Job's current status and result by id.",
             _schema({"job_id": _prop("string", "The Job id.")}, ["job_id"]), _job_status),

    ToolSpec("workshop_project_bootstrap", "Scaffold and register a brand-new project (the deterministic path: "
             "git init, lvx init, dev branch, optional GitHub repo).",
             _schema({
                 "name": _prop("string", "The new project's name."),
                 "dest_dir": _prop("string", "Directory the project should be scaffolded into."),
                 "owner": _prop("string", "GitHub owner/org for the new repo. Default: dfluxcbf."),
                 "description": _prop("string", "One-line project description."),
                 "create_github_repo": _prop("boolean", "Create and push a GitHub repo. Default: false."),
                 "public": _prop("boolean", "Make the GitHub repo public. Default: false."),
                 "remote_host": _prop("string", "Git remote host. Default: github.com."),
                 "github_token": _prop("string", "GitHub token, if create_github_repo is true."),
             }, ["name", "dest_dir"]), _project_bootstrap),
    ToolSpec("workshop_project_import", "Register an existing, already-checked-out repository — the other half of "
             "project.bootstrap (docs/milestones.md M8). Deliberately mechanical: it only registers what's there "
             "(reading the repo's own `origin` remote if one exists), it never scaffolds or checks policy "
             "conformance — inspect and fix the repository yourself first, the same judgment call a human session "
             "would make, then call this once it's ready. A normal workshop_agent_run_task afterward is how any "
             "further conformance work actually gets done, once the project is registered and resolvable.",
             _schema({
                 "name": _prop("string", "The project's name to register it under."),
                 "path": _prop("string", "Absolute path to the existing, already-checked-out git repository."),
             }, ["name", "path"]), _project_import),
    ToolSpec("workshop_project_remove", "Hard-delete a registered project: cascades to every pillar's own data for it.",
             _schema({"name": _prop("string", "The project's name.")}, ["name"]), _project_remove),
    ToolSpec("workshop_project_list", "List every registered project, with its overall status.",
             _schema({"status": _prop("string", "Filter by status, e.g. active.")}), _project_list),
    ToolSpec("workshop_project_get", "Get one registered project's full record by name.",
             _schema({"name": _prop("string", "The project's name.")}, ["name"]), _project_get),

    ToolSpec("workshop_version_init", "Initialize a repository for lvx-managed Gitflow.",
             _schema({"project_id": _PROJECT_ID, "production_branch": _prop("string", "main or master; auto-detected if omitted."),
                       "integration_branch": _prop("string", "develop or dev; auto-detected if omitted.")}, ["project_id"]), _version_init),
    ToolSpec("workshop_version_feature_start", "Create and check out feature/<name> from the integration branch.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Short feature name (no spaces)."), "issue": _ISSUE, "force": _FORCE},
                      ["project_id", "name"]), _version_feature_start),
    ToolSpec("workshop_version_feature_finish", "Finish the current feature branch: rebase, squash, merge, bump version, delete branch.",
             _schema({"project_id": _PROJECT_ID, "force": _FORCE, "action": _ACTION}, ["project_id"]), _version_feature_finish),
    ToolSpec("workshop_version_improvement_start", "Create and check out improvement/<name> from the integration branch.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Short improvement name (no spaces)."), "issue": _ISSUE, "force": _FORCE},
                      ["project_id", "name"]), _version_improvement_start),
    ToolSpec("workshop_version_improvement_finish", "Finish the current improvement branch, the same way a feature branch finishes.",
             _schema({"project_id": _PROJECT_ID, "force": _FORCE, "action": _ACTION}, ["project_id"]), _version_improvement_finish),
    ToolSpec("workshop_version_hotfix_start", "Create and check out hotfix/<name> from the production branch.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Short hotfix name (no spaces)."), "issue": _ISSUE, "force": _FORCE},
                      ["project_id", "name"]), _version_hotfix_start),
    ToolSpec("workshop_version_hotfix_finish", "Finish the current hotfix branch: merge to production, bump version, tag, back-merge.",
             _schema({"project_id": _PROJECT_ID, "force": _FORCE, "action": _ACTION}, ["project_id"]), _version_hotfix_finish),
    ToolSpec("workshop_version_release_start", "Create a release branch from the integration branch.",
             _schema({"project_id": _PROJECT_ID, "force": _FORCE}, ["project_id"]), _version_release_start),
    ToolSpec("workshop_version_release_candidate_cut", "Create the next (or an explicit) release-candidate tag.",
             _schema({"project_id": _PROJECT_ID, "number": _prop("integer", "Explicit RC number; default: auto-increment."), "force": _FORCE},
                      ["project_id"]), _version_release_candidate_cut),
    ToolSpec("workshop_version_release_candidate_list", "List all release-candidate tags for the current release branch, in order.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _version_release_candidate_list),
    ToolSpec("workshop_version_release_candidate_promote", "Mark a release-candidate tag as the target for the next release finish.",
             _schema({"project_id": _PROJECT_ID, "rc_number": _prop("integer", "The RC number to promote.")}, ["project_id", "rc_number"]),
             _version_release_candidate_promote),
    ToolSpec("workshop_version_release_finish", "Finish the current release branch: merge to production, tag, back-merge into develop.",
             _schema({"project_id": _PROJECT_ID, "force": _FORCE, "action": _ACTION}, ["project_id"]), _version_release_finish),
    ToolSpec("workshop_version_support_start", "Create support/<version> from the matching production release tag.",
             _schema({"project_id": _PROJECT_ID, "version": _prop("string", "Release version to branch support from, e.g. 1.2.0."), "force": _FORCE},
                      ["project_id", "version"]), _version_support_start),
    ToolSpec("workshop_version_support_finish", "Run the repository-defined policy for finishing a support branch.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _version_support_finish),
    ToolSpec("workshop_version_config_add_hook", "Register a .sh or .py version hook, run after every version bump.",
             _schema({"project_id": _PROJECT_ID, "hook_path": _prop("string", "Path to the hook script, relative to the repo root.")},
                      ["project_id", "hook_path"]), _version_config_add_hook),
    ToolSpec("workshop_version_config_remove_hook", "Remove a previously registered version hook.",
             _schema({"project_id": _PROJECT_ID, "hook_path": _prop("string", "Path of the hook to remove.")},
                      ["project_id", "hook_path"]), _version_config_remove_hook),
    ToolSpec("workshop_version_status", "Return metadata for the current feature/improvement/hotfix/release branch.",
             _schema({"project_id": _PROJECT_ID, "kind": _prop("string", "feature|improvement|hotfix|release", enum=["feature", "improvement", "hotfix", "release"])},
                      ["project_id", "kind"]), _version_status),
    ToolSpec("workshop_version_config", "Return the project's effective lvx configuration (.lvx/config.json).",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _version_config),

    ToolSpec("workshop_deployment_deploy", "Deploy a project's managed server from a branch, under the confirm-gated rollback guard.",
             _schema({"project_id": _PROJECT_ID, "branch": _prop("string", "Branch to deploy. Default: dev.")}, ["project_id"]), _deployment_deploy),
    ToolSpec("workshop_deployment_stop", "Stop a project's currently running managed server.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_stop),
    ToolSpec("workshop_deployment_confirm", "Confirm the most recent deploy, cancelling its rollback timeout.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_confirm),
    ToolSpec("workshop_deployment_seed_confirmation", "Seed a confirmation for a deploy that predates the confirm-gate mechanism.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_seed_confirmation),
    ToolSpec("workshop_deployment_redeploy_pillar", "Redeploy one of little-workshop's own pillars to a new commit (self-redeploy).",
             _schema({"pillar": _prop("string", "The pillar tag to redeploy, e.g. deployment."), "commit": _prop("string", "Commit/ref to deploy."),
                       "confirm_timeout_seconds": _prop("integer", "Override the default confirm-gate timeout.")}, ["pillar", "commit"]),
             _deployment_redeploy_pillar),
    ToolSpec("workshop_deployment_status", "Get a project's current deployment status.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_status),
    ToolSpec("workshop_deployment_history", "List a project's past deployments.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_history),
    ToolSpec("workshop_deployment_confirmation_status", "Get a project's pending/most-recent deploy confirmation state.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_confirmation_status),
    ToolSpec("workshop_deployment_branches", "List the branches Deployment & Servers knows about for a project.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _deployment_branches),

    ToolSpec("workshop_agent_run_task", "Run Claude Code on a project, on a real branch, under human review.",
             _schema({
                 "project_id": _PROJECT_ID,
                 "title": _prop("string", "Short title for the task."),
                 "description": _prop("string", "Full task description/instructions."),
                 "intent": _prop("string", "milestone|bug|docs|task", enum=["milestone", "bug", "docs", "task"]),
                 "review_mode": _prop("string", "manual|auto_approve", enum=["manual", "auto_approve"]),
                 "branch_mode": _prop("string", "new|existing|auto; omit to let branching stay unspecified.", enum=["new", "existing", "auto"]),
                 "branch_type": _prop("string", "Branch-type prefix to use when branch_mode is 'new', e.g. feature."),
                 "base": _prop("string", "Base branch/commit to branch from."),
                 "branch": _prop("string", "Explicit branch name (branch_mode 'existing') or override (branch_mode 'new')."),
             }, ["project_id", "title"]), _agent_run_task),
    ToolSpec("workshop_agent_finish_task", "Explicitly finish a run awaiting review: publish its branch/PR and report.",
             _schema({"project_id": _PROJECT_ID, "run_id": _prop("string", "The run's id.")}, ["project_id", "run_id"]), _agent_finish_task),
    ToolSpec("workshop_agent_skip_task", "Skip a run awaiting review without publishing it.",
             _schema({"project_id": _PROJECT_ID, "run_id": _prop("string", "The run's id.")}, ["project_id", "run_id"]), _agent_skip_task),
    ToolSpec("workshop_agent_runs", "List a project's agent runs, optionally filtered by status.",
             _schema({"project_id": _PROJECT_ID, "status": _prop("string", "running|awaiting_finish|finished|failed|skipped")},
                      ["project_id"]), _agent_runs),
    ToolSpec("workshop_agent_run_status", "Get one agent run's full status and result by id.",
             _schema({"project_id": _PROJECT_ID, "run_id": _prop("string", "The run's id.")}, ["project_id", "run_id"]), _agent_run_status),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}
