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
    assert params["ensure_labels"] is True


def test_project_bootstrap_no_labels_flag(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["project", "bootstrap", "little-widget", "--dest-dir", "/tmp/x", "--no-labels"])
    assert captured["params"]["ensure_labels"] is False


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


def test_version_issue_set_with_explicit_branch(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "issue-set", "--project-id", "little-widget", "42", "feature/widgets"])
    assert captured["type"] == "version.issue_set"
    assert captured["params"] == {"issue_number": 42, "branch": "feature/widgets"}


def test_version_issue_set_defaults_branch_to_none(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "issue-set", "--project-id", "little-widget", "42"])
    assert captured["params"] == {"issue_number": 42, "branch": None}


def test_version_issue_unset(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main(["version", "issue-unset", "--project-id", "little-widget", "feature/widgets"])
    assert captured["type"] == "version.issue_unset"
    assert captured["params"] == {"branch": "feature/widgets"}


def test_version_issue_list_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "version_issues", lambda p, project_id, **kw: {"links": {"feature/widgets": 42}})
    assert main.main(["version", "issue-list", "--project-id", "little-widget"]) == 0
    assert _out(capsys)["links"] == {"feature/widgets": 42}


def test_version_branch_audit_single_project_clean(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: {"status": "succeeded", "result": {"status": "clean", "violations": []}},
    )
    assert main.main(["version", "branch-audit", "--project-id", "little-widget"]) == 0
    assert _out(capsys)["reports"] == [{"project_id": "little-widget", "status": "clean", "violations": []}]


def test_version_branch_audit_single_project_with_violations_returns_1(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: {"status": "succeeded", "result": {"status": "violations", "violations": [{"kind": "naming"}]}},
    )
    assert main.main(["version", "branch-audit", "--project-id", "little-widget"]) == 1


def test_version_branch_audit_all_projects_loops_registry(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "list_projects",
        lambda p, **kw: {"projects": [{"name": "little-widget"}, {"name": "little-gadget"}]},
    )
    captured = []

    def fake_create_job(p, **kw):
        captured.append(kw["project_id"])
        return {"status": "succeeded", "result": {"status": "clean", "violations": []}}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)
    assert main.main(["version", "branch-audit", "--all"]) == 0
    assert captured == ["little-widget", "little-gadget"]
    assert len(_out(capsys)["reports"]) == 2


def test_version_branch_audit_reports_a_failed_project_without_aborting(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "list_projects",
        lambda p, **kw: {"projects": [{"name": "a"}, {"name": "b"}]},
    )

    def fake_create_job(p, **kw):
        if kw["project_id"] == "a":
            return {"status": "failed", "error": "not registered"}
        return {"status": "succeeded", "result": {"status": "clean", "violations": []}}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)
    assert main.main(["version", "branch-audit", "--all"]) == 0
    reports = _out(capsys)["reports"]
    assert reports[0] == {"project_id": "a", "status": "failed", "error": "not registered"}
    assert reports[1]["project_id"] == "b"


def test_version_stabilize_dry_run_by_default(paths, monkeypatch, capsys):
    captured = {}

    def fake_create_job(p, **kw):
        captured.update(kw)
        return {"status": "succeeded", "result": {"status": "stabilized", "branch_before": "dev", "pushed": ["dev"]}}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)
    assert main.main(["version", "stabilize", "--project-id", "little-widget"]) == 0
    assert captured["params"] == {"execute": False}
    out = _out(capsys)
    assert out["mode"] == "DRY RUN"
    assert out["reports"] == [{"project_id": "little-widget", "status": "stabilized", "branch_before": "dev", "pushed": ["dev"]}]


def test_version_stabilize_execute_flag_passed_through(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: (captured.update(kw), {"status": "succeeded", "result": {"status": "stabilized"}})[1],
    )
    main.main(["version", "stabilize", "--project-id", "little-widget", "--execute"])
    assert captured["params"] == {"execute": True}


def test_version_stabilize_needs_manual_resolution_returns_1(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: {"status": "succeeded", "result": {"status": "needs_manual_resolution", "message": "diverged"}},
    )
    assert main.main(["version", "stabilize", "--project-id", "little-widget", "--execute"]) == 1


def test_version_stabilize_all_projects_loops_registry(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "list_projects",
        lambda p, **kw: {"projects": [{"name": "little-widget"}, {"name": "little-gadget"}]},
    )
    captured = []

    def fake_create_job(p, **kw):
        captured.append(kw["project_id"])
        return {"status": "succeeded", "result": {"status": "stabilized"}}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)
    assert main.main(["version", "stabilize", "--all"]) == 0
    assert captured == ["little-widget", "little-gadget"]


# ---------------------------------------------------------------------------
# deployment
# ---------------------------------------------------------------------------

def test_deployment_set_manifest_success(paths, monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text("[server]\nname = \"x\"\ncommand = \"run.sh\"\nport = 9000\n", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: (captured.update(kw), {"status": "succeeded", "result": {"name": "x", "port": 9000}})[1],
    )
    assert main.main([
        "deployment", "set-manifest", "--project-id", "little-gadget", "--file", str(manifest_path),
    ]) == 0
    assert captured["type"] == "deployment.set_manifest"
    assert captured["params"]["toml"] == manifest_path.read_text(encoding="utf-8")


def test_deployment_set_manifest_failure_returns_1(paths, monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.toml"
    manifest_path.write_text("not valid [", encoding="utf-8")
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "failed", "error": "invalid manifest"})
    assert main.main([
        "deployment", "set-manifest", "--project-id", "little-gadget", "--file", str(manifest_path),
    ]) == 1


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


def test_agent_ci_check_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "agent", "ci-check", "--project-id", "little-widget", "--pr-number", "5", "--suite-id", "s1",
    ]) == 0
    assert captured["type"] == "agent.ci_check"
    assert captured["params"] == {"pr_number": 5, "suite_id": "s1", "context": "little-workshop/ci"}
    assert captured["queue_key"] == "little-widget"


def test_agent_ci_check_with_test_command(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    main.main([
        "agent", "ci-check", "--project-id", "little-widget", "--pr-number", "5", "--suite-id", "s1",
        "--test-command", "python3 -m pytest tests/unit",
    ])
    assert captured["params"]["test_command"] == ["python3", "-m", "pytest", "tests/unit"]


def test_agent_ci_list_success(paths, monkeypatch):
    monkeypatch.setattr(main.client, "agent_ci_checks", lambda p, project_id, **kw: {"ci_checks": []})
    assert main.main(["agent", "ci-list", "--project-id", "little-widget"]) == 0


def test_agent_ci_status_success(paths, monkeypatch, capsys):
    monkeypatch.setattr(main.client, "agent_latest_ci_check", lambda p, project_id, **kw: {"ci_check": {"state": "success"}})
    assert main.main(["agent", "ci-status", "--project-id", "little-widget"]) == 0
    assert _out(capsys)["ci_check"]["state"] == "success"


def test_agent_ci_status_none_recorded_returns_1(paths, monkeypatch):
    monkeypatch.setattr(main.client, "agent_latest_ci_check", lambda p, project_id, **kw: {"ci_check": None})
    assert main.main(["agent", "ci-status", "--project-id", "little-widget"]) == 1


def test_agent_session_start_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: (captured.update(kw), {"status": "succeeded", "result": {"session_name": "lws-little-widget-session"}})[1],
    )
    assert main.main(["agent", "session-start", "--project-id", "little-widget"]) == 0
    assert captured["type"] == "agent.session_start"
    assert captured["project_id"] == "little-widget"


def test_agent_session_start_failure_returns_1(paths, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "failed", "error": "not trusted"})
    assert main.main(["agent", "session-start", "--project-id", "little-widget"]) == 1


def test_agent_session_stop_success(paths, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        main.client, "create_job",
        lambda p, **kw: (captured.update(kw), {"status": "succeeded", "result": {"stopped": True}})[1],
    )
    assert main.main(["agent", "session-stop", "--project-id", "little-widget"]) == 0
    assert captured["type"] == "agent.session_stop"


def test_agent_session_list_loops_registry(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "list_projects",
        lambda p, **kw: {"projects": [{"name": "little-widget"}, {"name": "little-gadget"}]},
    )
    captured = []

    def fake_status(p, project_id, **kw):
        captured.append(project_id)
        return {"project_id": project_id, "running": False, "session_name": None}

    monkeypatch.setattr(main.client, "agent_session_status", fake_status)
    assert main.main(["agent", "session-list"]) == 0
    assert captured == ["little-widget", "little-gadget"]
    assert len(_out(capsys)["sessions"]) == 2


# ---------------------------------------------------------------------------
# job queue


def test_job_queue_show_groups_by_queue_key(paths, monkeypatch, capsys):
    def fake_list_jobs(p, *, status=None):
        if status == "running":
            return {"jobs": [{"id": "j1", "queue_key": "little-widget", "type": "agent.run_task"}]}
        return {"jobs": [{"id": "j2", "queue_key": "little-widget", "type": "agent.ci_check"}]}

    monkeypatch.setattr(main.client, "list_jobs", fake_list_jobs)
    assert main.main(["job", "queue", "show"]) == 0
    out = _out(capsys)
    assert out["queues"] == [{"queue_key": "little-widget", "running": {"id": "j1", "queue_key": "little-widget", "type": "agent.run_task"}, "queued": [{"id": "j2", "queue_key": "little-widget", "type": "agent.ci_check"}]}]


def test_job_queue_show_filters_by_queue_key(paths, monkeypatch, capsys):
    def fake_list_jobs(p, *, status=None):
        if status == "running":
            return {"jobs": [
                {"id": "j1", "queue_key": "a", "type": "x"}, {"id": "j3", "queue_key": "b", "type": "x"},
            ]}
        return {"jobs": []}

    monkeypatch.setattr(main.client, "list_jobs", fake_list_jobs)
    assert main.main(["job", "queue", "show", "--queue-key", "a"]) == 0
    out = _out(capsys)
    assert [q["queue_key"] for q in out["queues"]] == ["a"]


def test_job_queue_release_stops_and_pumps(paths, monkeypatch, capsys):
    monkeypatch.setattr(
        main.client, "list_jobs",
        lambda p, *, status=None: {"jobs": [{"id": "j1", "queue_key": "little-widget", "type": "agent.run_task"}]},
    )
    captured = {}
    monkeypatch.setattr(main.client, "stop_job", lambda p, job_id, **kw: (captured.update(job_id=job_id), {"stopped": True, "reason": None})[1])
    monkeypatch.setattr(main.client, "pump", lambda p, **kw: {"dispatched": []})
    assert main.main(["job", "queue", "release", "little-widget"]) == 0
    assert captured["job_id"] == "j1"


def test_job_queue_release_errors_when_no_holder(paths, monkeypatch):
    monkeypatch.setattr(main.client, "list_jobs", lambda p, *, status=None: {"jobs": []})
    assert main.main(["job", "queue", "release", "little-widget"]) == 1


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


# ---------------------------------------------------------------------------
# requirements
# ---------------------------------------------------------------------------

def test_requirements_suite_create_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "suite", "create", "--project-id", "p1", "core", "--description", "d"]) == 0
    assert captured["type"] == "requirements.create_test_suite"
    assert captured["target_pillar"] == "requirements"
    assert captured["params"] == {"name": "core", "description": "d"}


def test_requirements_suite_delete_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["requirements", "suite", "delete", "--project-id", "p1", "s1"]) == 0


def test_requirements_suite_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "requirements_list_test_suites", lambda p, project_id: {"source": "daemon", "test_suites": []})
    assert main.main(["requirements", "suite", "list"]) == 0
    assert _out(capsys)["source"] == "daemon"


def test_requirements_suite_status_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "requirements_get_test_suite", lambda p, suite_id: {"test_suite": {"id": suite_id}})
    assert main.main(["requirements", "suite", "status", "s1"]) == 0
    assert _out(capsys)["test_suite"]["id"] == "s1"


def test_requirements_use_case_create_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "use-case", "create", "--project-id", "p1", "checkout"]) == 0
    assert captured["type"] == "requirements.create_use_case"
    assert captured["params"]["name"] == "checkout"


def test_requirements_milestone_create_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "milestone", "create", "--project-id", "p1", "beta"]) == 0
    assert captured["type"] == "requirements.create_milestone"


def test_requirements_create_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "requirements", "create", "--project-id", "p1", "REQ-1", "thing works",
        "--use-case-id", "u1", "--milestone-id", "m1",
    ]) == 0
    assert captured["params"] == {
        "key": "REQ-1", "title": "thing works", "description": "", "category": "",
        "use_case_id": "u1", "milestone_id": "m1",
    }


def test_requirements_update_only_includes_provided_fields(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "update", "--project-id", "p1", "r1", "--title", "new title"]) == 0
    assert captured["params"] == {"requirement_id": "r1", "title": "new title"}


def test_requirements_publish_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["requirements", "publish", "--project-id", "p1", "r1"]) == 0


def test_requirements_delete_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["requirements", "delete", "--project-id", "p1", "r1"]) == 0


def test_requirements_link_and_unlink_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["requirements", "link", "--project-id", "p1", "r1", "s1"]) == 0
    assert main.main(["requirements", "unlink", "--project-id", "p1", "r1", "s1"]) == 0


def test_requirements_list_with_status_filter(paths, capsys, monkeypatch):
    captured = {}

    def fake_list(p, project_id, status):
        captured["args"] = (project_id, status)
        return {"source": "daemon", "requirements": []}

    monkeypatch.setattr(main.client, "requirements_list_requirements", fake_list)
    assert main.main(["requirements", "list", "--project-id", "p1", "--status", "published"]) == 0
    assert captured["args"] == ("p1", "published")


def test_requirements_status_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "requirements_get_requirement", lambda p, req_id: {"requirement": {"id": req_id}})
    assert main.main(["requirements", "status", "r1"]) == 0
    assert _out(capsys)["requirement"]["id"] == "r1"


def test_requirements_result_record_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "result", "record", "--project-id", "p1", "s1", "t1", "failed", "--detail", "boom"]) == 0
    assert captured["params"] == {"suite_id": "s1", "test_name": "t1", "verdict": "failed", "detail": "boom"}


def test_requirements_result_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "requirements_list_test_results", lambda p, project_id, suite_id: {"source": "daemon", "test_results": []})
    assert main.main(["requirements", "result", "list"]) == 0


def test_requirements_import_junit_reads_the_xml_file_and_builds_job_params(paths, capsys, monkeypatch, tmp_path):
    xml_file = tmp_path / "report.xml"
    xml_file.write_text("<testsuite/>", encoding="utf-8")
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["requirements", "import-junit", "--project-id", "p1", "s1", str(xml_file)]) == 0
    assert captured["params"] == {"suite_id": "s1", "xml": "<testsuite/>"}


def test_requirements_run_tests_builds_expected_job_params_with_custom_command(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "requirements", "run-tests", "--project-id", "p1", "s1", "--commitish", "abc123",
        "--test-command", "python3 -m pytest tests/unit",
    ]) == 0
    assert captured["params"] == {
        "suite_id": "s1", "commitish": "abc123", "test_command": ["python3", "-m", "pytest", "tests/unit"],
    }
    assert captured["timeout"] == 600.0


def test_requirements_report_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "requirements_report", lambda p, project_id: {"project_id": project_id, "requirements": []})
    assert main.main(["requirements", "report", "--project-id", "p1"]) == 0
    assert _out(capsys)["project_id"] == "p1"


def test_requirements_report_writes_md_csv_json_files(paths, capsys, monkeypatch, tmp_path):
    rows = [
        {"requirement_id": "r1", "key": "REQ-1", "status": "published", "suite_ids": ["s1"], "tested": True, "passing": True, "done": True},
        {"requirement_id": "r2", "key": "REQ-2", "status": "draft", "suite_ids": [], "tested": False, "passing": False, "done": False},
    ]
    monkeypatch.setattr(main.client, "requirements_report", lambda p, project_id: {"project_id": project_id, "requirements": rows})
    out_dir = tmp_path / "out"
    assert main.main([
        "requirements", "report", "--project-id", "p1", "--md", "--csv", "--json-file", "--path", str(out_dir),
    ]) == 0
    generated = _out(capsys)["generated"]
    assert len(generated) == 3
    assert (out_dir / "requirements_report.md").exists()
    assert (out_dir / "requirements_report.csv").exists()
    assert (out_dir / "requirements_report.json").exists()
    assert "REQ-1" in (out_dir / "requirements_report.md").read_text()


def test_requirements_report_filters_by_suite(paths, capsys, monkeypatch, tmp_path):
    rows = [
        {"requirement_id": "r1", "key": "REQ-1", "status": "published", "suite_ids": ["s1"], "tested": True, "passing": True, "done": True},
        {"requirement_id": "r2", "key": "REQ-2", "status": "draft", "suite_ids": ["s2"], "tested": False, "passing": False, "done": False},
    ]
    monkeypatch.setattr(main.client, "requirements_report", lambda p, project_id: {"project_id": project_id, "requirements": rows})
    out_dir = tmp_path / "out"
    assert main.main([
        "requirements", "report", "--project-id", "p1", "--md", "--suite", "s1", "--path", str(out_dir),
    ]) == 0
    content = (out_dir / "requirements_report-s1.md").read_text()
    assert "REQ-1" in content
    assert "REQ-2" not in content


def test_requirements_export_writes_all_three_formats_by_default(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [
            {"id": "id1", "key": "REQ-1", "title": "Widget support", "description": "desc", "status": "published"},
        ]},
    )
    monkeypatch.setattr(
        main.client, "requirements_get_requirement",
        lambda p, requirement_id: {"requirement": {"suite_ids": ["s1"]}},
    )
    out_dir = tmp_path / "out"
    assert main.main(["requirements", "export", "--project-id", "p1", "--path", str(out_dir)]) == 0
    generated = _out(capsys)["generated"]
    assert len(generated) == 3
    md = (out_dir / "requirement_export.md").read_text()
    assert "REQ-1" in md and "Widget support" in md
    csv = (out_dir / "requirement_export.csv").read_text()
    assert "REQ-1" in csv
    json_payload = json.loads((out_dir / "requirement_export.json").read_text())
    assert json_payload["requirements"][0]["key"] == "REQ-1"


def test_requirements_export_only_one_format(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [
            {"id": "id1", "key": "REQ-1", "title": "Widget", "description": "", "status": "draft"},
        ]},
    )
    monkeypatch.setattr(main.client, "requirements_get_requirement", lambda p, requirement_id: {"requirement": {"suite_ids": []}})
    out_dir = tmp_path / "out"
    assert main.main(["requirements", "export", "--project-id", "p1", "--only", "json", "--path", str(out_dir)]) == 0
    assert not (out_dir / "requirement_export.md").exists()
    assert (out_dir / "requirement_export.json").exists()


def test_requirements_export_filters_by_suite(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [
            {"id": "id1", "key": "REQ-1", "title": "In suite", "description": "", "status": "draft"},
            {"id": "id2", "key": "REQ-2", "title": "Not in suite", "description": "", "status": "draft"},
        ]},
    )

    def fake_get(p, requirement_id):
        suite_ids = ["s1"] if requirement_id == "id1" else ["s2"]
        return {"requirement": {"suite_ids": suite_ids}}

    monkeypatch.setattr(main.client, "requirements_get_requirement", fake_get)
    out_dir = tmp_path / "out"
    assert main.main([
        "requirements", "export", "--project-id", "p1", "--suite", "s1", "--only", "json", "--path", str(out_dir),
    ]) == 0
    payload = json.loads((out_dir / "requirement_export.json").read_text())
    assert [r["key"] for r in payload["requirements"]] == ["REQ-1"]


def test_requirements_import_creates_and_links_new_requirements(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(main.client, "requirements_list_requirements", lambda p, project_id, status=None: {"requirements": []})
    captured = []

    def fake_create_job(p, **kw):
        captured.append(kw)
        if kw["type"] == "requirements.create_requirement":
            return {"status": "succeeded", "result": {"id": "new-id-1"}}
        return {"status": "succeeded", "result": {}}

    monkeypatch.setattr(main.client, "create_job", fake_create_job)

    json_path = tmp_path / "payload.json"
    json_path.write_text(json.dumps({"list": [{"key": "REQ-1", "title": "T", "description": "D", "suite_id": "s1"}]}), encoding="utf-8")

    assert main.main(["requirements", "import", "--project-id", "p1", "--json", str(json_path)]) == 0
    assert _out(capsys) == {"created": ["REQ-1"], "updated": [], "skipped_conflicts": []}
    job_types = [kw["type"] for kw in captured]
    assert job_types == ["requirements.create_requirement", "requirements.link_requirement_to_suite"]
    assert captured[1]["params"] == {"requirement_id": "new-id-1", "suite_id": "s1"}


def test_requirements_import_skips_existing_key_without_edit(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [{"id": "id1", "key": "REQ-1", "status": "draft"}]},
    )
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: pytest.fail("should not create a job"))

    json_path = tmp_path / "payload.json"
    json_path.write_text(json.dumps({"list": [{"key": "REQ-1", "title": "T"}]}), encoding="utf-8")

    assert main.main(["requirements", "import", "--project-id", "p1", "--json", str(json_path)]) == 0
    assert _out(capsys) == {"created": [], "updated": [], "skipped_conflicts": ["REQ-1"]}


def test_requirements_import_edit_updates_existing_draft(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [{"id": "id1", "key": "REQ-1", "status": "draft"}]},
    )
    captured = []
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.append(kw), {"status": "succeeded", "result": {}})[1])

    json_path = tmp_path / "payload.json"
    json_path.write_text(json.dumps({"list": [{"key": "REQ-1", "title": "New title", "description": "New"}]}), encoding="utf-8")

    assert main.main(["requirements", "import", "--project-id", "p1", "--json", str(json_path), "--edit"]) == 0
    assert _out(capsys) == {"created": [], "updated": ["REQ-1"], "skipped_conflicts": []}
    assert captured[0]["type"] == "requirements.update_requirement"
    assert captured[0]["params"] == {"requirement_id": "id1", "title": "New title", "description": "New"}


def test_requirements_import_edit_rejects_published_requirement(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_requirements",
        lambda p, project_id, status=None: {"requirements": [{"id": "id1", "key": "REQ-1", "status": "published"}]},
    )
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: pytest.fail("should not create a job"))

    json_path = tmp_path / "payload.json"
    json_path.write_text(json.dumps({"list": [{"key": "REQ-1", "title": "New title"}]}), encoding="utf-8")

    assert main.main(["requirements", "import", "--project-id", "p1", "--json", str(json_path), "--edit"]) == 1


def test_requirements_test_structure_create_with_force(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_test_suites",
        lambda p, project_id: {"test_suites": [{"name": "Widgets/Core"}, {"name": "Auth"}]},
    )
    tests_root = tmp_path / "workspace"
    tests_root.mkdir()
    assert main.main([
        "requirements", "test-structure-create", "--project-id", "p1", "--tests-root", str(tests_root), "--force",
    ]) == 0
    result = _out(capsys)
    assert (tests_root / "tests" / "Widgets_Core" / "__init__.py").exists()
    assert (tests_root / "tests" / "Auth" / "__init__.py").exists()
    assert len(result["created"]) == 2
    assert result["warnings"] == []


def test_requirements_test_structure_create_warns_on_existing_file(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_test_suites", lambda p, project_id: {"test_suites": [{"name": "Auth"}]},
    )
    tests_root = tmp_path / "workspace"
    (tests_root / "tests" / "Auth").mkdir(parents=True)
    (tests_root / "tests" / "Auth" / "__init__.py").write_text("", encoding="utf-8")
    assert main.main([
        "requirements", "test-structure-create", "--project-id", "p1", "--tests-root", str(tests_root), "--force",
    ]) == 0
    result = _out(capsys)
    assert result["created"] == []
    assert len(result["warnings"]) == 1


def test_requirements_test_structure_create_prompts_and_aborts_without_force(paths, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        main.client, "requirements_list_test_suites", lambda p, project_id: {"test_suites": [{"name": "Auth"}]},
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    tests_root = tmp_path / "workspace"
    tests_root.mkdir()
    assert main.main([
        "requirements", "test-structure-create", "--project-id", "p1", "--tests-root", str(tests_root),
    ]) == 0
    out = capsys.readouterr().out
    json_text = out[out.index("{"):]
    assert json.loads(json_text)["aborted"] is True
    assert not (tests_root / "tests" / "Auth").exists()


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def test_build_run_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "build", "run", "--project-id", "p1", "--commitish", "abc123", "--from-fork", "--", "python3", "-m", "pytest",
    ]) == 0
    assert captured["type"] == "build.run"
    assert captured["target_pillar"] == "build"
    assert captured["params"] == {"commitish": "abc123", "command": ["python3", "-m", "pytest"], "from_fork": True}
    assert captured["timeout"] == 1800.0


def test_build_run_without_double_dash_still_captures_the_command(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["build", "run", "--project-id", "p1", "true"]) == 0
    assert captured["params"]["command"] == ["true"]


def test_build_run_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "build_list_runs", lambda p, project_id: {"source": "daemon", "build_runs": []})
    assert main.main(["build", "run-list"]) == 0
    assert _out(capsys)["source"] == "daemon"


def test_build_run_status_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "build_get_run", lambda p, run_id: {"build_run": {"id": run_id}})
    assert main.main(["build", "run-status", "r1"]) == 0
    assert _out(capsys)["build_run"]["id"] == "r1"


def test_build_publish_pypi_reads_the_file_and_base64_encodes_it(paths, capsys, monkeypatch, tmp_path):
    wheel = tmp_path / "pkg-1.0.0.whl"
    wheel.write_bytes(b"fake wheel bytes")
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "build", "publish-pypi", "--project-id", "p1", "--build-run-id", "r1", "pkg", "1.0.0", str(wheel),
    ]) == 0
    assert captured["type"] == "build.publish_pypi"
    assert captured["params"]["filename"] == "pkg-1.0.0.whl"
    import base64
    assert base64.b64decode(captured["params"]["content_base64"]) == b"fake wheel bytes"


def test_build_publish_vsix_reads_the_file_and_base64_encodes_it(paths, capsys, monkeypatch, tmp_path):
    vsix = tmp_path / "ext-0.1.0.vsix"
    vsix.write_bytes(b"fake vsix bytes")
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "build", "publish-vsix", "--project-id", "p1", "--build-run-id", "r1", "ext", "0.1.0", str(vsix),
    ]) == 0
    assert captured["type"] == "build.publish_vsix"


def test_build_package_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "build_list_packages", lambda p, project_id, kind: {"source": "daemon", "packages": []})
    assert main.main(["build", "package-list"]) == 0


def test_build_package_delete_success(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["build", "package-delete", "--project-id", "p1", "pkg1"]) == 0
    assert captured["params"] == {"package_id": "pkg1"}


def test_build_print_pip_config(capsys):
    assert main.main(["build", "print-pip-config", "--host", "myhost", "--port", "9999"]) == 0
    out = capsys.readouterr().out
    assert "index-url = http://myhost:9999/simple" in out
    assert "trusted-host = myhost" in out


def test_build_write_pip_config(tmp_path, capsys):
    target = tmp_path / "pip.conf"
    assert main.main(["build", "write-pip-config", "--host", "myhost", "--port", "9999", "--path", str(target)]) == 0
    assert "index-url = http://myhost:9999/simple" in target.read_text()
    written = _out(capsys)
    assert written["index_url"] == "http://myhost:9999/simple"


def test_build_pip_shells_to_real_pip_with_the_extra_index(monkeypatch):
    captured = {}

    def fake_run(command, check=False):
        captured["command"] = command

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    # --host/--port must precede the positional `package` — nargs=REMAINDER
    # greedily swallows everything after it, the same ordering requirement
    # little-pypi's own `pip` command already has.
    assert main.main(["build", "pip", "--host", "myhost", "--port", "9999", "requests", "--", "--upgrade"]) == 0
    assert "requests" in captured["command"]
    assert "--extra-index-url" in captured["command"]
    assert "http://myhost:9999/simple" in captured["command"]
    assert "--upgrade" in captured["command"]


def test_build_print_vsix_urls_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(
        main.client, "build_list_packages",
        lambda p, project_id, kind: {"source": "daemon", "packages": [{"name": "my-ext", "version": "1.0.0"}]},
    )
    assert main.main(["build", "print-vsix-urls", "--host", "myhost", "--port", "9999"]) == 0
    out = _out(capsys)
    assert out["urls"] == ["http://myhost:9999/vsix/my-ext/1.0.0"]


def test_build_print_vsix_urls_honors_explicit_base_url(paths, capsys, monkeypatch):
    monkeypatch.setattr(
        main.client, "build_list_packages",
        lambda p, project_id, kind: {"source": "daemon", "packages": [{"name": "my-ext", "version": "1.0.0"}]},
    )
    assert main.main(["build", "print-vsix-urls", "--base-url", "https://example.com/"]) == 0
    out = _out(capsys)
    assert out["urls"] == ["https://example.com/vsix/my-ext/1.0.0"]


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def test_policy_author_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "policy", "author", "the system shall x", "--topic", "t", "--target-pillar", "deployment",
        "--scope-level", "project", "--scope-target", "proj-a", "--created-by", "user",
    ]) == 0
    assert captured["type"] == "policy.author"
    assert captured["target_pillar"] == "policy"
    assert captured["params"] == {
        "ears_text": "the system shall x", "topic": "t", "target_pillar": "deployment",
        "scope_level": "project", "scope_target": "proj-a", "created_by": "user",
    }


def test_policy_activate_reads_draft_json_and_marks_approved(paths, capsys, monkeypatch, tmp_path):
    import json as jsonlib

    draft = {
        "ears_text": "WHEN x, the system shall y.", "test_script": "def test_x(): pass",
        "topic": "t", "target_pillar": "deployment", "scope_level": "all_projects", "scope_target": None,
        "created_by": "user",
    }
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(jsonlib.dumps(draft), encoding="utf-8")

    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "activate", str(draft_file)]) == 0
    assert captured["type"] == "policy.activate"
    assert captured["params"]["approved"] is True
    assert captured["params"]["ears_text"] == "WHEN x, the system shall y."


def test_policy_activate_with_policy_id_edits_in_place(paths, capsys, monkeypatch, tmp_path):
    import json as jsonlib

    draft_file = tmp_path / "draft.json"
    draft_file.write_text(jsonlib.dumps({
        "ears_text": "x", "test_script": "y", "topic": "t", "target_pillar": "deployment",
        "scope_level": "all_projects", "created_by": "user",
    }), encoding="utf-8")

    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "activate", str(draft_file), "--policy-id", "pol1"]) == 0
    assert captured["params"]["policy_id"] == "pol1"


def test_policy_remove_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["policy", "remove", "pol1"]) == 0


def test_policy_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_list", lambda p, topic: {"source": "daemon", "policies": []})
    assert main.main(["policy", "list"]) == 0


def test_policy_status_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_get", lambda p, policy_id: {"policy": {"id": policy_id}})
    assert main.main(["policy", "status", "pol1"]) == 0
    assert _out(capsys)["policy"]["id"] == "pol1"


def test_policy_winning_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_winning", lambda p, topic, project_id: {"policy": None})
    assert main.main(["policy", "winning", "--topic", "t", "--project-id", "proj-a"]) == 0


def test_policy_check_project_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "check-project", "pol1", "--project-id", "proj-a"]) == 0
    assert captured["type"] == "policy.check_project"
    assert captured["project_id"] == "proj-a"
    assert captured["params"] == {"policy_id": "pol1"}


def test_policy_check_all_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["policy", "check-all", "pol1"]) == 0


def test_policy_results_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_check_results", lambda p, policy_id, project_id: {"source": "daemon", "results": []})
    assert main.main(["policy", "results"]) == 0


def test_job_policy_confirm_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_confirm_job", lambda p, job_id: {"confirmed": True, "job": {"id": job_id}})
    assert main.main(["job", "policy-confirm", "j1"]) == 0


def test_job_policy_confirm_timeout_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_confirm_timeout_job", lambda p, job_id: {"timed_out": True})
    assert main.main(["job", "policy-confirm-timeout", "j1"]) == 0


def test_policy_catalog_list_prints_every_entry(paths, capsys):
    assert main.main(["policy", "catalog", "list"]) == 0
    output = _out(capsys)
    assert len(output) >= 50
    assert any(e["topic"] == "two-long-lived-branches" for e in output)


def test_policy_catalog_run_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "catalog", "run", "two-long-lived-branches", "--target-pillar", "version"]) == 0
    assert captured["type"] == "policy.author"
    assert captured["params"]["topic"] == "two-long-lived-branches"
    assert captured["params"]["target_pillar"] == "version"
    assert captured["params"]["scope_level"] == "all_projects"
    assert captured["params"]["created_by"] == "framework"
    assert "exactly two long-lived branches" in captured["params"]["ears_text"]


def test_policy_catalog_run_unknown_topic_fails_cleanly(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: {"status": "succeeded"})
    assert main.main(["policy", "catalog", "run", "ghost-topic", "--target-pillar", "version"]) == 1
    assert "error" in capsys.readouterr().err


def test_policy_author_includes_gate_points_when_given(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main([
        "policy", "author", "the system shall x", "--topic", "t", "--target-pillar", "deployment",
        "--scope-level", "all_projects", "--gate-points", "server_launch,release",
    ]) == 0
    assert captured["params"]["gate_points"] == ["server_launch", "release"]


def test_policy_configure_disable_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "configure", "pol1", "--disable"]) == 0
    assert captured["type"] == "policy.configure"
    assert captured["params"] == {"policy_id": "pol1", "enabled": False}


def test_policy_configure_gate_points_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "configure", "pol1", "--enable", "--gate-points", "build_install"]) == 0
    assert captured["params"] == {"policy_id": "pol1", "enabled": True, "gate_points": ["build_install"]}


def test_policy_configure_empty_gate_points_clears_them(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "configure", "pol1", "--gate-points", ""]) == 0
    assert captured["params"]["gate_points"] == []


def test_policy_projects_set_builds_expected_job_params(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "projects", "set", "pol1", "--project", "proj-a", "--project-no-gating", "proj-b"]) == 0
    assert captured["type"] == "policy.set_projects"
    assert captured["params"] == {"policy_id": "pol1", "projects": [
        {"project_id": "proj-a", "gating_enabled": True}, {"project_id": "proj-b", "gating_enabled": False},
    ]}


def test_policy_projects_set_with_no_projects_deselects_everything(paths, capsys, monkeypatch):
    captured = {}
    monkeypatch.setattr(main.client, "create_job", lambda p, **kw: (captured.update(kw), {"status": "succeeded"})[1])
    assert main.main(["policy", "projects", "set", "pol1"]) == 0
    assert captured["params"] == {"policy_id": "pol1", "projects": []}


def test_policy_projects_list_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_list_projects", lambda p, policy_id, project_id: {"source": "daemon", "projects": []})
    assert main.main(["policy", "projects", "list"]) == 0


def test_policy_gating_for_job_success(paths, capsys, monkeypatch):
    monkeypatch.setattr(main.client, "policy_gating_for_job", lambda p, project_id, categories: {"policies": []})
    assert main.main(["policy", "gating-for-job", "--project-id", "proj-a", "--categories", "server_launch"]) == 0
