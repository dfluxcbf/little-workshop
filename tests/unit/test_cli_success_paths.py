"""cli.main's success paths — argv -> client-call -> stdout/exit-code
translation for every subcommand. `tests/unit/test_cli.py` already covers
the daemon-unavailable branch for a sample of commands; this file closes
the rest: what each `cmd_*` actually does once its `cli.client` call
succeeds, including how optional flags (--issue, --continue/--abort,
--branch-mode, --confirm-timeout, ...) translate into the params/kwargs
`cli.client` is called with.

`cli.client`'s own HTTP-over-Unix-socket correctness is exercised for
real elsewhere (test_cli.py's daemon-unreachable paths, a handful of
real-daemon success paths in test_cli_client_daemon_success.py, and the
full tests/integration suite) — here `cli.client`'s functions are
monkeypatched to capture what they were called with, so these tests stay
focused on cli.main's own translation and formatting logic, the thing
that was actually uncovered.
"""

from __future__ import annotations

import json

import pytest

from cli import main
from common.paths import Paths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: p))
    return p


def _out(capsys):
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# core / job
# ---------------------------------------------------------------------------

def test_core_status_reports_daemon_source_when_reachable(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "core_status", lambda p, **kw: {"source": "daemon", "pillars": {}})
    assert main.main(["core", "status"]) == 0
    assert _out(capsys)["source"] == "daemon"


def test_job_create_success_exits_0(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded", "id": "j1"})
    assert main.main(["job", "create", "--type", "noop", "--target-pillar", "reference"]) == 0
    assert _out(capsys)["status"] == "succeeded"


def test_job_create_returns_0_even_when_the_job_itself_failed(paths, capsys, monkeypatch):
    """Unlike the specific subcommands (project/version/deployment/agent),
    the generic `job create` escape hatch has no idea what "success" means
    for an arbitrary job type — it reports 0 whenever the dispatch call
    itself succeeded, leaving the caller to read `status`/`error` from the
    printed Job."""
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "failed", "error": "boom"})
    assert main.main(["job", "create", "--type", "noop", "--target-pillar", "reference"]) == 0
    assert _out(capsys)["status"] == "failed"


def test_job_create_parses_params_json_and_depends_on(paths, capsys, monkeypatch):
    captured = {}

    def fake_create_job(p, **kw):
        captured.update(kw)
        return {"status": "succeeded"}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)
    main.main([
        "job", "create", "--type", "noop", "--target-pillar", "reference",
        "--params", '{"a": 1}', "--queue-key", "qk",
        "--depends-on", "j1", "--depends-on", "j2",
    ])
    assert captured["params"] == {"a": 1}
    assert captured["queue_key"] == "qk"
    assert captured["depends_on"] == ["j1", "j2"]


def test_job_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "list_jobs", lambda p, **kw: {"source": "daemon", "jobs": [{"id": "j1"}]})
    assert main.main(["job", "list"]) == 0
    assert _out(capsys)["jobs"] == [{"id": "j1"}]


def test_job_status_found_exits_0(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "job_status", lambda p, job_id, **kw: {"source": "daemon", "job": {"id": job_id}})
    assert main.main(["job", "status", "j1"]) == 0
    assert _out(capsys)["job"]["id"] == "j1"


def test_job_status_missing_exits_1(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "job_status", lambda p, job_id, **kw: {"source": "daemon", "job": None})
    assert main.main(["job", "status", "nope"]) == 1


def test_job_stop_success_exits_0(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "stop_job", lambda p, job_id, **kw: {"stopped": True, "reason": None})
    assert main.main(["job", "stop", "j1"]) == 0


def test_job_stop_not_stoppable_exits_1(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "stop_job", lambda p, job_id, **kw: {"stopped": False, "reason": "not stoppable"})
    assert main.main(["job", "stop", "j1"]) == 1


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def test_schedule_add_recurring_builds_the_right_payload(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_scheduled_task", lambda p, **kw: (captured.update(kw), {"id": "s1"})[1])
    assert main.main([
        "schedule", "add", "nightly-tests", "--type", "noop", "--target-pillar", "reference",
        "--days", "0,1,2,3,4", "--time-of-day", "02:00",
    ]) == 0
    assert captured["name"] == "nightly-tests"
    assert captured["job_template"]["type"] == "noop"
    assert captured["schedule"] == {"kind": "recurring", "days_of_week": [0, 1, 2, 3, 4], "time_of_day": "02:00"}


def test_schedule_add_once_builds_the_right_payload(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_scheduled_task", lambda p, **kw: (captured.update(kw), {"id": "s1"})[1])
    main.main(["schedule", "add", "one-off", "--type", "noop", "--target-pillar", "reference", "--at", "2030-01-01T02:00:00"])
    assert captured["schedule"] == {"kind": "once", "at": "2030-01-01T02:00:00"}


def test_schedule_list_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "list_scheduled_tasks", lambda p, **kw: {"source": "daemon", "scheduled_tasks": []})
    assert main.main(["schedule", "list"]) == 0


def test_schedule_status_found_exits_0(paths, monkeypatch):
    monkeypatch.setattr(
        main.client, "get_scheduled_task",
        lambda p, task_id, **kw: {"source": "daemon", "scheduled_task": {"id": task_id}},
    )
    assert main.main(["schedule", "status", "s1"]) == 0


def test_schedule_status_missing_exits_1(paths, monkeypatch):
    monkeypatch.setattr(
        main.client, "get_scheduled_task", lambda p, task_id, **kw: {"source": "daemon", "scheduled_task": None}
    )
    assert main.main(["schedule", "status", "ghost"]) == 1


def test_schedule_remove_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "remove_scheduled_task", lambda p, task_id, **kw: {"id": task_id, "removed": True})
    assert main.main(["schedule", "remove", "s1"]) == 0


def test_schedule_trigger_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "trigger_scheduled_task", lambda p, task_id, **kw: {"id": "j1", "status": "running"})
    assert main.main(["schedule", "trigger", "s1"]) == 0


def test_schedule_cancel_success_exits_0(paths, monkeypatch):
    monkeypatch.setattr(main.client, "cancel_scheduled_task", lambda p, task_id, **kw: {"cancelled": "next_occurrence"})
    assert main.main(["schedule", "cancel", "s1"]) == 0


def test_schedule_cancel_nothing_to_cancel_exits_1(paths, monkeypatch):
    monkeypatch.setattr(
        main.client, "cancel_scheduled_task", lambda p, task_id, **kw: {"cancelled": None, "reason": "nothing pending"}
    )
    assert main.main(["schedule", "cancel", "s1"]) == 1


def test_schedule_postpone_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.client, "postpone_scheduled_task",
        lambda p, task_id, at, **kw: (captured.update(task_id=task_id, at=at), {"next_run_at": at})[1],
    )
    assert main.main(["schedule", "postpone", "s1", "--at", "2030-01-01T05:00:00"]) == 0
    assert captured == {"task_id": "s1", "at": "2030-01-01T05:00:00"}


def test_schedule_queue_get_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "get_queue_capacity", lambda p, queue_key, **kw: {"queue_key": queue_key, "capacity": 1})
    assert main.main(["schedule", "queue", "get", "little-meals"]) == 0


def test_schedule_queue_set_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.client, "set_queue_capacity",
        lambda p, queue_key, capacity, **kw: (captured.update(queue_key=queue_key, capacity=capacity), {"capacity": capacity})[1],
    )
    assert main.main(["schedule", "queue", "set", "little-meals", "--capacity", "3"]) == 0
    assert captured == {"queue_key": "little-meals", "capacity": 3}


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------

def test_project_bootstrap_success_exits_0(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded", "result": {"name": "x"}})
    assert main.main(["project", "bootstrap", "little-widget", "--dest-dir", "/tmp/x"]) == 0


def test_project_bootstrap_failed_exits_1(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "failed", "error": "already exists"})
    assert main.main(["project", "bootstrap", "little-widget", "--dest-dir", "/tmp/x"]) == 1


def test_project_bootstrap_passes_all_flags_through_as_params(paths, monkeypatch):
    captured = {}

    def fake(p, **kw):
        captured.update(kw)
        return {"status": "succeeded"}

    monkeypatch.setattr(main.client, "create_job", fake)
    main.main([
        "project", "bootstrap", "little-widget", "--dest-dir", "/tmp/x",
        "--owner", "someone", "--description", "a widget", "--create-github-repo",
        "--public", "--remote-host", "example.com", "--github-token", "tok",
    ])
    params = captured["params"]
    assert params["owner"] == "someone"
    assert params["description"] == "a widget"
    assert params["create_github_repo"] is True
    assert params["public"] is True
    assert params["remote_host"] == "example.com"
    assert params["github_token"] == "tok"


def test_project_import_success_exits_0(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["project", "import", "little-widget", "--path", "/repos/little-widget"]) == 0
    assert captured["type"] == "project.import"
    assert captured["target_pillar"] == "registry"
    assert captured["params"] == {"name": "little-widget", "path": "/repos/little-widget"}


def test_project_import_failed_exits_1(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "failed", "error": "not a git repository"})
    assert main.main(["project", "import", "little-widget", "--path", "/repos/little-widget"]) == 1


def test_project_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "list_projects", lambda p, **kw: {"source": "daemon", "projects": []})
    assert main.main(["project", "list"]) == 0


def test_project_remove_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["project", "remove", "little-widget"]) == 0


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

def test_version_init_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["version", "init", "--project-id", "little-widget"]) == 0


def test_version_init_passes_branch_names(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main([
        "version", "init", "--project-id", "little-widget",
        "--production-branch", "master", "--integration-branch", "dev",
    ])
    assert captured["params"] == {"production_branch": "master", "integration_branch": "dev"}


def test_version_feature_start_success_with_issue(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "version", "feature-start", "--project-id", "little-widget", "--issue", "42", "widgets",
    ]) == 0
    assert captured["type"] == "version.feature_start"
    assert captured["params"] == {"name": "widgets", "issue": 42, "force": False}


def test_version_feature_finish_continue_sets_action(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "feature-finish", "--project-id", "little-widget", "--continue"])
    assert captured["params"]["action"] == "continue"


def test_version_feature_finish_abort_sets_action(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "feature-finish", "--project-id", "little-widget", "--abort"])
    assert captured["params"]["action"] == "abort"


def test_version_hotfix_start_and_finish(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["version", "hotfix-start", "--project-id", "little-widget", "fix-it"]) == 0
    assert main.main(["version", "hotfix-finish", "--project-id", "little-widget"]) == 0


def test_version_improvement_start_and_finish(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "improvement-start", "--project-id", "little-widget", "cleanup"])
    assert captured["type"] == "version.improvement_start"
    main.main(["version", "improvement-finish", "--project-id", "little-widget"])
    assert captured["type"] == "version.improvement_finish"


def test_version_status_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "version_status", lambda p, kind, project_id, **kw: {"success": True, "message": "", "data": {"branch": f"{kind}/x"}})
    assert main.main(["version", "status", "--project-id", "little-widget", "feature"]) == 0
    assert _out(capsys)["data"]["branch"] == "feature/x"


def test_version_release_start_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["version", "release-start", "--project-id", "little-widget"]) == 0


def test_version_release_candidate_cut_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "release-candidate-cut", "--project-id", "little-widget", "--number", "2"])
    assert captured["params"]["number"] == 2


def test_version_release_candidate_list_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "version_release_candidates", lambda p, project_id, **kw: {"candidates": [1, 2]})
    assert main.main(["version", "release-candidate-list", "--project-id", "little-widget"]) == 0
    assert _out(capsys)["candidates"] == [1, 2]


def test_version_release_candidate_promote_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "release-candidate-promote", "--project-id", "little-widget", "3"])
    assert captured["params"] == {"rc_number": 3}


def test_version_release_finish_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["version", "release-finish", "--project-id", "little-widget"]) == 0


def test_version_support_start_and_finish(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "support-start", "--project-id", "little-widget", "1.2.0"])
    assert captured["params"]["version"] == "1.2.0"
    main.main(["version", "support-finish", "--project-id", "little-widget"])
    assert captured["type"] == "version.support_finish"


def test_version_config_list_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "version_config", lambda p, project_id, **kw: {"production_branch": "master"})
    assert main.main(["version", "config-list", "--project-id", "little-widget"]) == 0
    assert _out(capsys)["production_branch"] == "master"


def test_version_config_add_and_remove_hook(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "config-add-hook", "--project-id", "little-widget", "tools/hook.sh"])
    assert captured["params"] == {"hook_path": "tools/hook.sh"}
    main.main(["version", "config-remove-hook", "--project-id", "little-widget", "tools/hook.sh"])
    assert captured["type"] == "version.config_remove_hook"


# ---------------------------------------------------------------------------
# deployment
# ---------------------------------------------------------------------------

def test_deployment_deploy_success_default_branch(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["deployment", "deploy", "--project-id", "little-meals"]) == 0
    assert captured["params"] == {"branch": "dev"}


def test_deployment_stop_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["deployment", "stop", "--project-id", "little-meals"]) == 0


def test_deployment_confirm_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["deployment", "confirm", "--project-id", "little-meals"]) == 0


def test_deployment_seed_confirmation_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["deployment", "seed-confirmation", "--project-id", "little-meals"]) == 0


def test_deployment_rollback_timeout_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "deployment_rollback_timeout",
        lambda p, project_id, deploy_id, **kw: {"rolled_back": True, "message": "ok"},
    )
    assert main.main(["deployment", "rollback-timeout", "little-meals", "--deploy-id", "d1"]) == 0
    assert _out(capsys)["rolled_back"] is True


def test_deployment_status_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "deployment_status", lambda p, project_id: {"name": project_id, "listening": True})
    assert main.main(["deployment", "status", "--project-id", "little-meals"]) == 0
    assert _out(capsys)["listening"] is True


def test_deployment_history_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "deployment_history", lambda p, project_id: [{"id": "d1"}])
    assert main.main(["deployment", "history", "--project-id", "little-meals"]) == 0
    assert _out(capsys) == [{"id": "d1"}]


def test_deployment_confirmation_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "deployment_confirmation", lambda p, project_id: {"state": "confirmed"})
    assert main.main(["deployment", "confirmation", "--project-id", "little-meals"]) == 0


def test_deployment_branches_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "deployment_branches", lambda p, project_id: ["dev", "master"])
    assert main.main(["deployment", "branches", "--project-id", "little-meals"]) == 0
    assert _out(capsys) == ["dev", "master"]


def test_deployment_redeploy_pillar_without_confirm_timeout(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["deployment", "redeploy-pillar", "deployment", "--commit", "abc123"]) == 0
    assert "confirm_timeout_seconds" not in captured["params"]


def test_deployment_redeploy_pillar_with_confirm_timeout(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "running"})[1])
    assert main.main(["deployment", "redeploy-pillar", "deployment", "--confirm-timeout", "30"]) == 0
    assert captured["params"]["confirm_timeout_seconds"] == 30


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

def test_agent_run_task_success_without_branching(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "running"})[1])
    assert main.main(["agent", "run-task", "--project-id", "little-meals", "--title", "fix it"]) == 0
    assert "branching" not in captured["params"]
    assert captured["params"]["intent"] == "task"
    assert captured["params"]["review_mode"] == "manual"


def test_agent_run_task_success_with_branching(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main([
        "agent", "run-task", "--project-id", "little-meals", "--title", "fix it",
        "--branch-mode", "new", "--branch-type", "feature", "--base", "dev",
    ])
    assert captured["params"]["branching"] == {"mode": "new", "branch_type": "feature", "base": "dev", "branch": None}


def test_agent_finish_task_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["agent", "finish-task", "--project-id", "little-meals", "run-1"]) == 0
    assert captured["params"] == {"run_id": "run-1"}


def test_agent_skip_task_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["agent", "skip-task", "--project-id", "little-meals", "run-1"]) == 0


def test_agent_runs_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "agent_runs", lambda p, project_id, **kw: {"runs": []})
    assert main.main(["agent", "runs", "--project-id", "little-meals"]) == 0


def test_agent_run_status_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "agent_run_status", lambda p, project_id, run_id, **kw: {"id": run_id})
    assert main.main(["agent", "run-status", "--project-id", "little-meals", "run-1"]) == 0
    assert _out(capsys)["id"] == "run-1"


# ---------------------------------------------------------------------------
# worktree / mcp
# ---------------------------------------------------------------------------

def test_worktree_provision_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "provision_worktree", lambda p, project_id, commitish: {"worktree_id": "w1"})
    assert main.main(["worktree", "provision", "--project-id", "little-meals"]) == 0
    assert _out(capsys)["worktree_id"] == "w1"


def test_worktree_release_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "release_worktree", lambda p, project_id, worktree_id: {"released": True})
    assert main.main(["worktree", "release", "--project-id", "little-meals", "w1"]) == 0


def test_mcp_serve_invokes_the_stdio_server_with_the_resolved_paths(paths, monkeypatch):
    captured = {}

    def fake_serve(p):
        captured["paths"] = p
        return 0

    monkeypatch.setattr("mcp_server.server.serve", fake_serve)
    assert main.main(["mcp", "serve"]) == 0
    assert captured["paths"] is paths
