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
_GATE_POINT_VALUES = [
    "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
]
_GATE_POINTS = _prop(
    "array", "Which action categories this policy gates on failure. Any subset, or omit/empty for none: "
    + ", ".join(_GATE_POINT_VALUES) + ".",
    items={"type": "string", "enum": _GATE_POINT_VALUES},
)


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


def _job_stop(paths: Paths, args: JSONDict) -> Any:
    return client.stop_job(paths, args["job_id"])


def _schedule_add(paths: Paths, args: JSONDict) -> Any:
    job_template = {
        "type": args["type"], "target_pillar": args["target_pillar"], "project_id": args.get("project_id"),
        "params": args.get("params", {}), "queue_key": args.get("queue_key"), "depends_on": args.get("depends_on", []),
    }
    if args.get("at"):
        schedule = {"kind": "once", "at": args["at"]}
    else:
        schedule = {
            "kind": "recurring", "days_of_week": args.get("days_of_week", []),
            "time_of_day": args.get("time_of_day"),
        }
    return client.create_scheduled_task(paths, name=args["name"], job_template=job_template, schedule=schedule)


def _schedule_list(paths: Paths, args: JSONDict) -> Any:
    return client.list_scheduled_tasks(paths)


def _schedule_status(paths: Paths, args: JSONDict) -> Any:
    return client.get_scheduled_task(paths, args["task_id"])


def _schedule_remove(paths: Paths, args: JSONDict) -> Any:
    return client.remove_scheduled_task(paths, args["task_id"])


def _schedule_trigger(paths: Paths, args: JSONDict) -> Any:
    return client.trigger_scheduled_task(paths, args["task_id"])


def _schedule_cancel(paths: Paths, args: JSONDict) -> Any:
    return client.cancel_scheduled_task(paths, args["task_id"])


def _schedule_postpone(paths: Paths, args: JSONDict) -> Any:
    return client.postpone_scheduled_task(paths, args["task_id"], args["at"])


def _schedule_queue_get(paths: Paths, args: JSONDict) -> Any:
    return client.get_queue_capacity(paths, args["queue_key"])


def _schedule_queue_set(paths: Paths, args: JSONDict) -> Any:
    return client.set_queue_capacity(paths, args["queue_key"], args["capacity"])


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
        "ensure_labels": args.get("ensure_labels", True),
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


def _version_issue_set(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.issue_set", args["project_id"], {
        "issue_number": args["issue_number"], "branch": args.get("branch"),
    })


def _version_issue_unset(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.issue_unset", args["project_id"], {"branch": args.get("branch")})


def _version_issue_list(paths: Paths, args: JSONDict) -> Any:
    return client.version_issues(paths, args["project_id"])


def _version_status(paths: Paths, args: JSONDict) -> Any:
    return client.version_status(paths, args["kind"], args["project_id"])


def _version_branch_audit(paths: Paths, args: JSONDict) -> Any:
    return _version_job(paths, "version.branch_audit", args["project_id"], {})


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
# Requirements & Quality
# ---------------------------------------------------------------------------

def _requirements_job(paths: Paths, job_type: str, project_id: str, params: JSONDict) -> Any:
    return client.create_job(
        paths, type=job_type, target_pillar="requirements", project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="mcp",
    )


def _requirements_suite_create(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.create_test_suite", args["project_id"], {
        "name": args["name"], "description": args.get("description", ""),
    })


def _requirements_suite_delete(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.delete_test_suite", args["project_id"], {"suite_id": args["suite_id"]})


def _requirements_suite_list(paths: Paths, args: JSONDict) -> Any:
    return client.requirements_list_test_suites(paths, args.get("project_id"))


def _requirements_use_case_create(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.create_use_case", args["project_id"], {
        "name": args["name"], "description": args.get("description", ""),
    })


def _requirements_milestone_create(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.create_milestone", args["project_id"], {
        "name": args["name"], "description": args.get("description", ""),
    })


def _requirements_create(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.create_requirement", args["project_id"], {
        "key": args["key"], "title": args["title"], "description": args.get("description", ""),
        "category": args.get("category", ""), "use_case_id": args.get("use_case_id"),
        "milestone_id": args.get("milestone_id"),
    })


def _requirements_update(paths: Paths, args: JSONDict) -> Any:
    fields = {"requirement_id": args["requirement_id"]}
    for name in ("title", "description", "category"):
        if name in args:
            fields[name] = args[name]
    return _requirements_job(paths, "requirements.update_requirement", args["project_id"], fields)


def _requirements_publish(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.publish_requirement", args["project_id"], {"requirement_id": args["requirement_id"]})


def _requirements_delete(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.delete_requirement", args["project_id"], {"requirement_id": args["requirement_id"]})


def _requirements_link_suite(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.link_requirement_to_suite", args["project_id"], {
        "requirement_id": args["requirement_id"], "suite_id": args["suite_id"],
    })


def _requirements_unlink_suite(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.unlink_requirement_from_suite", args["project_id"], {
        "requirement_id": args["requirement_id"], "suite_id": args["suite_id"],
    })


def _requirements_list(paths: Paths, args: JSONDict) -> Any:
    return client.requirements_list_requirements(paths, args.get("project_id"), args.get("status"))


def _requirements_get(paths: Paths, args: JSONDict) -> Any:
    return client.requirements_get_requirement(paths, args["requirement_id"])


def _requirements_result_record(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.record_test_result", args["project_id"], {
        "suite_id": args["suite_id"], "test_name": args["test_name"], "verdict": args["verdict"],
        "detail": args.get("detail", ""),
    })


def _requirements_import_junit(paths: Paths, args: JSONDict) -> Any:
    return _requirements_job(paths, "requirements.import_junit", args["project_id"], {
        "suite_id": args["suite_id"], "xml": args["xml"],
    })


def _requirements_run_tests(paths: Paths, args: JSONDict) -> Any:
    params = {"suite_id": args["suite_id"], "commitish": args.get("commitish", "HEAD")}
    if args.get("test_command"):
        params["test_command"] = args["test_command"]
    return _requirements_job(paths, "requirements.run_tests", args["project_id"], params)


def _requirements_report(paths: Paths, args: JSONDict) -> Any:
    return client.requirements_report(paths, args["project_id"])


# ---------------------------------------------------------------------------
# Build & Packages
# ---------------------------------------------------------------------------

def _build_job(paths: Paths, job_type: str, project_id: str, params: JSONDict) -> Any:
    return client.create_job(
        paths, type=job_type, target_pillar="build", project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="mcp",
    )


def _build_run(paths: Paths, args: JSONDict) -> Any:
    return _build_job(paths, "build.run", args["project_id"], {
        "commitish": args.get("commitish", "HEAD"), "command": args["command"],
        "from_fork": args.get("from_fork", False),
    })


def _build_run_list(paths: Paths, args: JSONDict) -> Any:
    return client.build_list_runs(paths, args.get("project_id"))


def _build_run_status(paths: Paths, args: JSONDict) -> Any:
    return client.build_get_run(paths, args["run_id"])


def _build_publish_pypi(paths: Paths, args: JSONDict) -> Any:
    return _build_job(paths, "build.publish_pypi", args["project_id"], {
        "build_run_id": args["build_run_id"], "name": args["name"], "version": args["version"],
        "filename": args["filename"], "content_base64": args["content_base64"],
    })


def _build_publish_vsix(paths: Paths, args: JSONDict) -> Any:
    return _build_job(paths, "build.publish_vsix", args["project_id"], {
        "build_run_id": args["build_run_id"], "name": args["name"], "version": args["version"],
        "filename": args["filename"], "content_base64": args["content_base64"],
    })


def _build_package_list(paths: Paths, args: JSONDict) -> Any:
    return client.build_list_packages(paths, args.get("project_id"), args.get("kind"))


def _build_package_delete(paths: Paths, args: JSONDict) -> Any:
    return _build_job(paths, "build.delete_package", args["project_id"], {"package_id": args["package_id"]})


# ---------------------------------------------------------------------------
# Policy & Compliance
# ---------------------------------------------------------------------------

def _policy_job(paths: Paths, job_type: str, project_id: str | None, params: JSONDict) -> Any:
    return client.create_job(
        paths, type=job_type, target_pillar="policy", project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="mcp",
    )


def _policy_author(paths: Paths, args: JSONDict) -> Any:
    params = {
        "ears_text": args["ears_text"], "topic": args["topic"], "target_pillar": args["target_pillar"],
        "scope_level": args["scope_level"], "scope_target": args.get("scope_target"),
        "created_by": args.get("created_by", "user"),
    }
    if args.get("policy_id"):
        params["policy_id"] = args["policy_id"]
    if "gate_points" in args:
        params["gate_points"] = args["gate_points"]
    return _policy_job(paths, "policy.author", None, params)


def _policy_activate(paths: Paths, args: JSONDict) -> Any:
    params = {
        "ears_text": args["ears_text"], "test_script": args["test_script"], "topic": args["topic"],
        "target_pillar": args["target_pillar"], "scope_level": args["scope_level"],
        "scope_target": args.get("scope_target"), "created_by": args.get("created_by", "user"),
        "approved": True,
    }
    if args.get("policy_id"):
        params["policy_id"] = args["policy_id"]
    if "gate_points" in args:
        params["gate_points"] = args["gate_points"]
    return _policy_job(paths, "policy.activate", None, params)


def _policy_remove(paths: Paths, args: JSONDict) -> Any:
    return _policy_job(paths, "policy.remove", None, {"policy_id": args["policy_id"]})


def _policy_configure(paths: Paths, args: JSONDict) -> Any:
    params: JSONDict = {"policy_id": args["policy_id"]}
    if "enabled" in args:
        params["enabled"] = args["enabled"]
    if "gate_points" in args:
        params["gate_points"] = args["gate_points"]
    return _policy_job(paths, "policy.configure", None, params)


def _policy_waiver_grant(paths: Paths, args: JSONDict) -> Any:
    return _policy_job(paths, "policy.waiver_grant", args["project_id"], {"policy_id": args["policy_id"], "reason": args["reason"]})


def _policy_waiver_revoke(paths: Paths, args: JSONDict) -> Any:
    return _policy_job(paths, "policy.waiver_revoke", None, {"waiver_id": args["waiver_id"]})


def _policy_waiver_list(paths: Paths, args: JSONDict) -> Any:
    return client.policy_list_waivers(paths, args.get("policy_id"), args.get("project_id"))


def _policy_gating_for_job(paths: Paths, args: JSONDict) -> Any:
    return client.policy_gating_for_job(paths, args["project_id"], args["categories"])


def _policy_list(paths: Paths, args: JSONDict) -> Any:
    return client.policy_list(paths, args.get("topic"))


def _policy_get(paths: Paths, args: JSONDict) -> Any:
    return client.policy_get(paths, args["policy_id"])


def _policy_winning(paths: Paths, args: JSONDict) -> Any:
    return client.policy_winning(paths, args["topic"], args["project_id"])


def _policy_check_project(paths: Paths, args: JSONDict) -> Any:
    return _policy_job(paths, "policy.check_project", args["project_id"], {"policy_id": args["policy_id"]})


def _policy_check_all(paths: Paths, args: JSONDict) -> Any:
    return _policy_job(paths, "policy.check_all", None, {"policy_id": args["policy_id"]})


def _policy_results(paths: Paths, args: JSONDict) -> Any:
    return client.policy_check_results(paths, args.get("policy_id"), args.get("project_id"))


def _policy_confirm(paths: Paths, args: JSONDict) -> Any:
    return client.policy_confirm_job(paths, args["job_id"])


def _policy_catalog_list(paths: Paths, args: JSONDict) -> Any:
    from policy.catalog import load_catalog

    return [
        {"topic": e.topic, "section": e.section, "section_title": e.section_title, "ears_text": e.ears_text, "source": e.source}
        for e in load_catalog()
    ]


def _policy_catalog_run(paths: Paths, args: JSONDict) -> Any:
    from policy.catalog import find_entry

    entry = find_entry(args["topic"])
    if entry is None:
        raise ValueError(f"no catalog entry named {args['topic']!r}")
    return _policy_job(paths, "policy.author", None, {
        "ears_text": entry.ears_text, "topic": entry.topic, "target_pillar": args["target_pillar"],
        "scope_level": "all_projects", "created_by": "framework",
    })


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


def _agent_ci_check(paths: Paths, args: JSONDict) -> Any:
    params = {
        "pr_number": args["pr_number"], "suite_id": args["suite_id"],
        "context": args.get("context", "little-workshop/ci"),
    }
    if args.get("test_command"):
        params["test_command"] = args["test_command"].split()
    return client.create_job(
        paths, type="agent.ci_check", target_pillar="agent", project_id=args["project_id"],
        params=params, queue_key=args["project_id"], depends_on=[], created_by="mcp", timeout=600.0,
    )


def _agent_ci_list(paths: Paths, args: JSONDict) -> Any:
    return client.agent_ci_checks(paths, args["project_id"])


def _agent_ci_status(paths: Paths, args: JSONDict) -> Any:
    return client.agent_latest_ci_check(paths, args["project_id"], branch=args.get("branch"))


# ---------------------------------------------------------------------------
# Tool catalog
# ---------------------------------------------------------------------------

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("workshop_core_status", "Return every pillar's enabled/active status, as Core sees it.", _schema(), _core_status),
    ToolSpec("workshop_job_list", "List Jobs known to Orchestration, optionally filtered by status.",
             _schema({"status": _prop("string", "queued|running|pending_confirmation|succeeded|failed|cancelled")}), _job_list),
    ToolSpec("workshop_job_status", "Get one Job's current status and result by id.",
             _schema({"job_id": _prop("string", "The Job id.")}, ["job_id"]), _job_status),
    ToolSpec("workshop_job_stop", "Interrupt a Job that is currently running. Most Job types have no safe "
             "mid-flight interruption point and report back that they are not stoppable, rather than being "
             "silently ignored or left to hang.",
             _schema({"job_id": _prop("string", "The Job id.")}, ["job_id"]), _job_stop),
    ToolSpec("workshop_schedule_add", "Add a scheduled task: a durable definition that creates a Job of any "
             "type this MCP server already exposes, on its own timing. Provide either `at` (a one-shot ISO "
             "datetime) or `days_of_week`+`time_of_day` (recurring, host local time) — not both.",
             _schema({
                 "name": _prop("string", "A human label for the schedule."),
                 "type": _prop("string", "The Job type to fire, e.g. version.release_start."),
                 "target_pillar": _prop("string", "The pillar that owns that Job type."),
                 "project_id": _prop("string", "The project this Job targets, if any."),
                 "params": _prop("object", "The Job's own params."),
                 "queue_key": _prop("string", "Serializes against other Jobs sharing this key."),
                 "depends_on": _prop("array", "Job ids this fired Job should depend on.", items={"type": "string"}),
                 "at": _prop("string", "One-shot ISO datetime, host local time."),
                 "days_of_week": _prop("array", "Recurring: 0=Monday..6=Sunday.", items={"type": "integer"}),
                 "time_of_day": _prop("string", "Recurring: \"HH:MM\", host local time."),
             }, ["name", "type", "target_pillar"]), _schedule_add),
    ToolSpec("workshop_schedule_list", "List every scheduled task.", _schema(), _schedule_list),
    ToolSpec("workshop_schedule_status", "Get one scheduled task by id, including any pending missed-run question.",
             _schema({"task_id": _prop("string", "The scheduled task's id.")}, ["task_id"]), _schedule_status),
    ToolSpec("workshop_schedule_remove", "Delete a scheduled task for good: cancels a pending firing or stops "
             "a running one first. Jobs it already created stay in Orchestration's history.",
             _schema({"task_id": _prop("string", "The scheduled task's id.")}, ["task_id"]), _schedule_remove),
    ToolSpec("workshop_schedule_trigger", "Fire a scheduled task's Job right now, out of band — its own timing "
             "is unaffected. Also resolves a pending missed-run question, if there was one.",
             _schema({"task_id": _prop("string", "The scheduled task's id.")}, ["task_id"]), _schedule_trigger),
    ToolSpec("workshop_schedule_cancel", "Dismiss a pending missed-run question without firing it, or — if "
             "there is none — skip the one upcoming occurrence.",
             _schema({"task_id": _prop("string", "The scheduled task's id.")}, ["task_id"]), _schedule_cancel),
    ToolSpec("workshop_schedule_postpone", "Retarget one pending firing to a different date/time. The "
             "recurring baseline itself is untouched.",
             _schema({
                 "task_id": _prop("string", "The scheduled task's id."),
                 "at": _prop("string", "The new ISO datetime, host local time."),
             }, ["task_id", "at"]), _schedule_postpone),
    ToolSpec("workshop_schedule_queue_get", "Get a queue_key's current concurrency capacity (default 1).",
             _schema({"queue_key": _prop("string", "The queue_key to check.")}, ["queue_key"]), _schedule_queue_get),
    ToolSpec("workshop_schedule_queue_set", "Set a queue_key's concurrency capacity — how many Jobs sharing "
             "that key may run at once.",
             _schema({
                 "queue_key": _prop("string", "The queue_key to configure."),
                 "capacity": _prop("integer", "Maximum concurrent Jobs for this key. At least 1."),
             }, ["queue_key", "capacity"]), _schedule_queue_set),

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
                 "ensure_labels": _prop("boolean", "Ensure the seven little-runner trigger/intent GitHub labels exist. Default: true."),
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
    ToolSpec("workshop_version_issue_set", "Link an issue number to a branch (default: the current branch).",
             _schema({
                 "project_id": _PROJECT_ID, "issue_number": _ISSUE,
                 "branch": _prop("string", "Branch to link (default: current branch)."),
             }, ["project_id", "issue_number"]), _version_issue_set),
    ToolSpec("workshop_version_issue_unset", "Remove the issue link from a branch (default: the current branch).",
             _schema({"project_id": _PROJECT_ID, "branch": _prop("string", "Branch to unlink (default: current branch).")},
                      ["project_id"]), _version_issue_unset),
    ToolSpec("workshop_version_issue_list", "List every branch with a linked issue.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _version_issue_list),
    ToolSpec("workshop_version_branch_audit", "Read-only branch-hygiene audit for one project (naming, stale-merged, "
             "abandoned, detached-HEAD, stray-worktree violations). Never merges, deletes, commits, or pushes anything.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _version_branch_audit),
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

    ToolSpec("workshop_requirements_suite_create", "Create a test suite for a project.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Suite name."),
                      "description": _prop("string", "Suite description.")}, ["project_id", "name"]), _requirements_suite_create),
    ToolSpec("workshop_requirements_suite_delete", "Delete a test suite (and its result history/requirement links).",
             _schema({"project_id": _PROJECT_ID, "suite_id": _prop("string", "The test suite's id.")},
                     ["project_id", "suite_id"]), _requirements_suite_delete),
    ToolSpec("workshop_requirements_suite_list", "List a project's test suites.",
             _schema({"project_id": _prop("string", "Filter by project. Omit to list every project's suites.")}),
             _requirements_suite_list),
    ToolSpec("workshop_requirements_use_case_create", "Create a use case — the M8 axis, independent of test suites.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Use case name."),
                      "description": _prop("string", "Use case description.")}, ["project_id", "name"]), _requirements_use_case_create),
    ToolSpec("workshop_requirements_milestone_create", "Create a milestone — the M8 axis, independent of test suites.",
             _schema({"project_id": _PROJECT_ID, "name": _prop("string", "Milestone name."),
                      "description": _prop("string", "Milestone description.")}, ["project_id", "name"]), _requirements_milestone_create),
    ToolSpec("workshop_requirements_create", "Create a draft requirement. Editable until published.",
             _schema({
                 "project_id": _PROJECT_ID, "key": _prop("string", "A human requirement key, e.g. REQ-000000001."),
                 "title": _prop("string", "Short title."), "description": _prop("string", "Full requirement text."),
                 "category": _prop("string", "Free-text category."),
                 "use_case_id": _prop("string", "Optional linked use case id."),
                 "milestone_id": _prop("string", "Optional linked milestone id."),
             }, ["project_id", "key", "title"]), _requirements_create),
    ToolSpec("workshop_requirements_update", "Edit a draft requirement's fields. Refused once published.",
             _schema({
                 "project_id": _PROJECT_ID, "requirement_id": _prop("string", "The requirement's id."),
                 "title": _prop("string", "New title."), "description": _prop("string", "New description."),
                 "category": _prop("string", "New category."),
             }, ["project_id", "requirement_id"]), _requirements_update),
    ToolSpec("workshop_requirements_publish", "Publish a requirement, locking it against further edits.",
             _schema({"project_id": _PROJECT_ID, "requirement_id": _prop("string", "The requirement's id.")},
                     ["project_id", "requirement_id"]), _requirements_publish),
    ToolSpec("workshop_requirements_delete", "Delete a requirement for good.",
             _schema({"project_id": _PROJECT_ID, "requirement_id": _prop("string", "The requirement's id.")},
                     ["project_id", "requirement_id"]), _requirements_delete),
    ToolSpec("workshop_requirements_link_suite", "Link a requirement to a test suite that covers it.",
             _schema({"project_id": _PROJECT_ID, "requirement_id": _prop("string", "The requirement's id."),
                      "suite_id": _prop("string", "The test suite's id.")},
                     ["project_id", "requirement_id", "suite_id"]), _requirements_link_suite),
    ToolSpec("workshop_requirements_unlink_suite", "Unlink a requirement from a test suite.",
             _schema({"project_id": _PROJECT_ID, "requirement_id": _prop("string", "The requirement's id."),
                      "suite_id": _prop("string", "The test suite's id.")},
                     ["project_id", "requirement_id", "suite_id"]), _requirements_unlink_suite),
    ToolSpec("workshop_requirements_list", "List requirements, optionally filtered by project and/or status.",
             _schema({"project_id": _prop("string", "Filter by project."),
                      "status": _prop("string", "draft|published")}), _requirements_list),
    ToolSpec("workshop_requirements_get", "Get one requirement by id, including the test suites linked to it.",
             _schema({"requirement_id": _prop("string", "The requirement's id.")}, ["requirement_id"]), _requirements_get),
    ToolSpec("workshop_requirements_result_record", "Record a single test result manually.",
             _schema({
                 "project_id": _PROJECT_ID, "suite_id": _prop("string", "The test suite this result belongs to."),
                 "test_name": _prop("string", "The test's name."),
                 "verdict": _prop("string", "passed|failed|skipped|error"),
                 "detail": _prop("string", "Optional failure/error detail."),
             }, ["project_id", "suite_id", "test_name", "verdict"]), _requirements_result_record),
    ToolSpec("workshop_requirements_import_junit", "Ingest an existing JUnit-XML report (E6) into a test suite's results.",
             _schema({"project_id": _PROJECT_ID, "suite_id": _prop("string", "The test suite to record results under."),
                      "xml": _prop("string", "The JUnit-XML report's own text content.")},
                     ["project_id", "suite_id", "xml"]), _requirements_import_junit),
    ToolSpec("workshop_requirements_run_tests", "Run a suite's tests sandboxed, in a fresh Deployment & Servers "
             "worktree of a given commit (E6's on-demand testing) — never the project's own working checkout.",
             _schema({
                 "project_id": _PROJECT_ID, "suite_id": _prop("string", "The test suite to record results under."),
                 "commitish": _prop("string", "Branch, tag, or commit to test. Default: HEAD."),
                 "test_command": _prop("array", "Override test command, e.g. [\"python3\",\"-m\",\"pytest\",\"tests/unit\"].",
                                        items={"type": "string"}),
             }, ["project_id", "suite_id"]), _requirements_run_tests),
    ToolSpec("workshop_requirements_report", "Definition-of-done report for a project: for each requirement, "
             "whether it is published, tested, passing, and therefore done.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _requirements_report),

    ToolSpec("workshop_build_run", "Run a build/test command sandboxed, in a fresh Deployment & Servers "
             "worktree of a given commit — never the project's own working checkout. Mark from_fork true for "
             "an untrusted PR: the fork-safety gate then refuses to let this run's result be published.",
             _schema({
                 "project_id": _PROJECT_ID, "commitish": _prop("string", "Branch, tag, or commit to build. Default: HEAD."),
                 "command": _prop("array", "The build/test command, e.g. [\"python3\",\"-m\",\"pytest\"].", items={"type": "string"}),
                 "from_fork": _prop("boolean", "Mark this run as untrusted. Default: false."),
             }, ["project_id", "command"]), _build_run),
    ToolSpec("workshop_build_run_list", "List build runs, optionally filtered by project.",
             _schema({"project_id": _prop("string", "Filter by project.")}), _build_run_list),
    ToolSpec("workshop_build_run_status", "Get one build run by id, including its exit code and log.",
             _schema({"run_id": _prop("string", "The build run's id.")}, ["run_id"]), _build_run_status),
    ToolSpec("workshop_build_publish_pypi", "Publish a wheel/sdist to the internal PEP 503 index. Refused "
             "(the fork-safety gate) unless build_run_id names a real, succeeded, non-fork build run.",
             _schema({
                 "project_id": _PROJECT_ID, "build_run_id": _prop("string", "A succeeded, non-fork build run's id."),
                 "name": _prop("string", "Package (project) name."), "version": _prop("string", "Package version."),
                 "filename": _prop("string", "The wheel/sdist filename."),
                 "content_base64": _prop("string", "The file's own bytes, base64-encoded."),
             }, ["project_id", "build_run_id", "name", "version", "filename", "content_base64"]), _build_publish_pypi),
    ToolSpec("workshop_build_publish_vsix", "Publish a .vsix VS Code extension. Refused (the fork-safety gate) "
             "unless build_run_id names a real, succeeded, non-fork build run.",
             _schema({
                 "project_id": _PROJECT_ID, "build_run_id": _prop("string", "A succeeded, non-fork build run's id."),
                 "name": _prop("string", "Extension name."), "version": _prop("string", "Extension version."),
                 "filename": _prop("string", "The .vsix filename."),
                 "content_base64": _prop("string", "The file's own bytes, base64-encoded."),
             }, ["project_id", "build_run_id", "name", "version", "filename", "content_base64"]), _build_publish_vsix),
    ToolSpec("workshop_build_package_list", "List published packages, optionally filtered by project and/or kind.",
             _schema({"project_id": _prop("string", "Filter by project."),
                      "kind": _prop("string", "pypi|vsix")}), _build_package_list),
    ToolSpec("workshop_build_package_delete", "Delete a published package for good.",
             _schema({"project_id": _PROJECT_ID, "package_id": _prop("string", "The package's id.")},
                     ["project_id", "package_id"]), _build_package_delete),

    ToolSpec("workshop_policy_author", "Gates 1-3 of the policy authoring pipeline plus test-script drafting — "
             "EARS-compliance, mechanical-enforceability, and non-conflict checks, then a drafted pytest script. "
             "Never saves anything; the result is a proposal for a human to review and pass to "
             "workshop_policy_activate.",
             _schema({
                 "ears_text": _prop("string", "The requirement, in plain language or EARS form already."),
                 "topic": _prop("string", "A short, stable topic name, e.g. server-manifest-shape."),
                 "target_pillar": _prop("string", "The one pillar this policy's subject governs."),
                 "scope_level": _prop("string", "project|project_set|all_projects"),
                 "scope_target": _prop("string", "A project id or project-set id. Omit for all_projects."),
                 "created_by": _prop("string", "user|framework. Default: user."),
                 "policy_id": _prop("string", "Editing an existing policy: exclude it from conflict checks."),
                 "gate_points": _GATE_POINTS,
             }, ["ears_text", "topic", "target_pillar", "scope_level"]), _policy_author),
    ToolSpec("workshop_policy_activate", "Gate 4: save a workshop_policy_author draft as a real, active policy — "
             "only after a human has reviewed both the EARS text and the generated test script. The only path "
             "that ever writes a policies row.",
             _schema({
                 "ears_text": _prop("string", "The (possibly human-edited) EARS text from the draft."),
                 "test_script": _prop("string", "The (possibly human-edited) test script from the draft."),
                 "topic": _prop("string", "The topic from the draft."),
                 "target_pillar": _prop("string", "The target_pillar from the draft."),
                 "scope_level": _prop("string", "project|project_set|all_projects"),
                 "scope_target": _prop("string", "A project id or project-set id. Omit for all_projects."),
                 "created_by": _prop("string", "user|framework. Default: user."),
                 "policy_id": _prop("string", "Editing an existing policy in place, rather than creating a new one."),
                 "gate_points": _GATE_POINTS,
             }, ["ears_text", "test_script", "topic", "target_pillar", "scope_level"]), _policy_activate),
    ToolSpec("workshop_policy_remove", "Delete a policy for good.",
             _schema({"policy_id": _prop("string", "The policy's id.")}, ["policy_id"]), _policy_remove),
    ToolSpec("workshop_policy_list", "List policies, optionally filtered by topic.",
             _schema({"topic": _prop("string", "Filter by topic.")}), _policy_list),
    ToolSpec("workshop_policy_get", "Get one policy by id.",
             _schema({"policy_id": _prop("string", "The policy's id.")}, ["policy_id"]), _policy_get),
    ToolSpec("workshop_policy_winning", "Show which policy currently wins precedence on a topic, for a project "
             "(framework > project-specific > project-set > all-projects).",
             _schema({"topic": _prop("string", "The topic to resolve."), "project_id": _PROJECT_ID},
                     ["topic", "project_id"]), _policy_winning),
    ToolSpec("workshop_policy_check_project", "Check one policy against one project, sandboxed.",
             _schema({"policy_id": _prop("string", "The policy's id."), "project_id": _PROJECT_ID},
                     ["policy_id", "project_id"]), _policy_check_project),
    ToolSpec("workshop_policy_check_all", "Check one policy against every project it applies to, fanned out as "
             "real Jobs.",
             _schema({"policy_id": _prop("string", "The policy's id.")}, ["policy_id"]), _policy_check_all),
    ToolSpec("workshop_policy_results", "List policy check results, optionally filtered by policy and/or project.",
             _schema({"policy_id": _prop("string", "Filter by policy."),
                      "project_id": _prop("string", "Filter by project.")}), _policy_results),
    ToolSpec("workshop_policy_confirm", "Confirm a policy-gated Job that's pending_confirmation, dispatching it "
             "despite the policy failure — the 10-minute deliberate-bypass window.",
             _schema({"job_id": _prop("string", "The gated Job's id.")}, ["job_id"]), _policy_confirm),
    ToolSpec("workshop_policy_configure", "Enable/disable a policy, or change which action categories it gates "
             "on failure — never re-runs any gate, since neither changes the requirement's own meaning.",
             _schema({
                 "policy_id": _prop("string", "The policy's id."),
                 "enabled": _prop("boolean", "Turn the policy on or off. Omit to leave unchanged."),
                 "gate_points": _GATE_POINTS,
             }, ["policy_id"]), _policy_configure),
    ToolSpec("workshop_policy_waiver_grant", "Grant one project an explicit, auditable exception to one policy "
             "— never a silent bypass, and never a change to the policy itself for any other project.",
             _schema({
                 "policy_id": _prop("string", "The policy's id."), "project_id": _PROJECT_ID,
                 "reason": _prop("string", "Why this project can't comply — required, never blank."),
             }, ["policy_id", "project_id", "reason"]), _policy_waiver_grant),
    ToolSpec("workshop_policy_waiver_revoke", "Remove a waiver, restoring normal precedence for that project.",
             _schema({"waiver_id": _prop("string", "The waiver's id.")}, ["waiver_id"]), _policy_waiver_revoke),
    ToolSpec("workshop_policy_waiver_list", "List waivers, optionally filtered by policy and/or project.",
             _schema({"policy_id": _prop("string", "Filter by policy."),
                      "project_id": _prop("string", "Filter by project.")}), _policy_waiver_list),
    ToolSpec("workshop_policy_gating_for_job", "Which currently-winning policies gate a project for a given set "
             "of action categories — what Orchestration itself consults before dispatching a Job.",
             _schema({"project_id": _PROJECT_ID, "categories": _GATE_POINTS}, ["project_id", "categories"]),
             _policy_gating_for_job),
    ToolSpec("workshop_policy_catalog_list", "List every candidate framework policy in "
             "docs/pillars/policy_catalog.md (M13) — none is active until authored and activated.",
             _schema(), _policy_catalog_list),
    ToolSpec("workshop_policy_catalog_run", "Gates 1-3 for one catalog entry, framework tier, all-projects scope — "
             "the result is a draft for workshop_policy_activate, same as workshop_policy_author.",
             _schema({
                 "topic": _prop("string", "The catalog entry's own topic."),
                 "target_pillar": _prop("string", "The one pillar this policy's subject governs."),
             }, ["topic", "target_pillar"]), _policy_catalog_run),

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
    ToolSpec("workshop_agent_ci_check", "Test a pull request's real head branch in an isolated worktree and post "
             "the result as a GitHub commit status.",
             _schema({
                 "project_id": _PROJECT_ID,
                 "pr_number": _prop("integer", "The pull request number."),
                 "suite_id": _prop("string", "The test suite id to run."),
                 "context": _prop("string", "Commit status context name (default: little-workshop/ci)."),
                 "test_command": _prop("string", "Space-separated command, default: python3 -m pytest."),
             }, ["project_id", "pr_number", "suite_id"]), _agent_ci_check),
    ToolSpec("workshop_agent_ci_list", "List every recorded CI check for a project.",
             _schema({"project_id": _PROJECT_ID}, ["project_id"]), _agent_ci_list),
    ToolSpec("workshop_agent_ci_status", "Get the last recorded CI check, optionally for one branch.",
             _schema({"project_id": _PROJECT_ID, "branch": _prop("string", "Limit to one branch.")}, ["project_id"]), _agent_ci_status),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}
