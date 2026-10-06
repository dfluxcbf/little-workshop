"""What Claude Code shows of little-workshop: the Dashboard's status of a project and its status page
(docs/pillars/dashboard.md, "Status for Claude Code"), the session-title hook and the status mod the
Claude profile installs (docs/pillars/usage.md, "Configuration"). `dashboard.client` is monkeypatched as in
test_dashboard_app.py; the hook runs against scratch git repositories."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client
from orchestration_jobs_fake import patch_jobs
from usage import profile, session_title

NOW = datetime.now(timezone.utc).replace(microsecond=0)  # the page reads the clock: its windows are hours wide
PROJECT = "little-meals"


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def dashboard(paths):
    return TestClient(app_module.build_app(paths))


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


def local_in(**delta) -> str:
    """A scheduled task's `next_run_at`, which Orchestration keeps in local time."""
    return (NOW.astimezone() + timedelta(**delta)).replace(tzinfo=None, microsecond=0).isoformat()


def _stub(monkeypatch, *, projects=(PROJECT,), server=None, jobs=(), runs=(), scheduled=(), policies=(), results=(),
          incidents=(), pillars=None):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": n, "status": "active"} for n in projects])
    monkeypatch.setattr(client, "deployment_status_optional", lambda paths, name: server)
    reads = patch_jobs(monkeypatch, client, list(jobs))
    monkeypatch.setattr(client, "agent_runs", lambda paths, name: list(runs))
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: list(scheduled))
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None, **kw: list(policies))
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: list(results))
    monkeypatch.setattr(client, "list_incidents",
                        lambda paths, status=None: [i for i in incidents if status is None or i.get("status", "open") == status])
    monkeypatch.setattr(client, "core_status",
                        lambda paths: {"pillars": pillars if pillars is not None else {"registry": {"status": "healthy", "crash_count": 0}}})
    monkeypatch.setattr(client, "policy_bypass", lambda paths, **kw: {})
    return reads


def _busy(monkeypatch):
    return _stub(
        monkeypatch,
        server={"unit": "active", "listening": True},
        jobs=[
            {"id": "j1", "type": "agent.run_task", "project_id": PROJECT, "status": "running", "params": {"title": "Export the list"},
             "created_at": ago(minutes=12), "updated_at": ago(minutes=1)},
            {"id": "j2", "type": "build.run", "project_id": "other", "status": "running", "created_at": ago(minutes=5), "updated_at": ago(minutes=1)},
            {"id": "j3", "type": "deployment.deploy", "project_id": PROJECT, "status": "failed", "error": "health check timed out",
             "created_at": ago(minutes=50), "updated_at": ago(minutes=40)},
            {"id": "j4", "type": "version.release", "project_id": PROJECT, "status": "failed", "created_at": ago(hours=4), "updated_at": ago(hours=3)},
            {"id": "j5", "type": "build.run", "project_id": "other", "status": "failed", "created_at": ago(minutes=20), "updated_at": ago(minutes=10)},
            {"id": "j6", "type": "agent.run_task", "project_id": PROJECT, "status": "failed", "params": {"title": "Never started"},
             "created_at": ago(hours=2), "updated_at": ago(hours=1)},
        ],
        runs=[
            {"id": "r1", "title": "Fix the login", "status": "failed", "created_at": ago(days=2), "updated_at": ago(days=1)},
            {"id": "r2", "title": "Polish", "status": "finished", "created_at": ago(days=1), "updated_at": ago(hours=1)},
            {"id": "r3", "title": "Dismissed", "status": "failed", "dismissed_at": ago(hours=1), "created_at": ago(days=1), "updated_at": ago(days=1)},
            {"id": "r4", "title": "Awaiting finish", "status": "awaiting_finish", "created_at": ago(hours=3), "updated_at": ago(hours=2)},
        ],
        scheduled=[
            {"id": "s1", "name": "Nightly tests", "state": "active", "job_template": {"project_id": PROJECT}, "next_run_at": local_in(minutes=30)},
            {"id": "s2", "name": "Later", "state": "active", "job_template": {"project_id": PROJECT}, "next_run_at": local_in(hours=3)},
            {"id": "s3", "name": "Paused", "state": "paused", "job_template": {"project_id": PROJECT}, "next_run_at": local_in(minutes=10)},
            {"id": "s4", "name": "Elsewhere", "state": "active", "job_template": {"project_id": "other"}, "next_run_at": local_in(minutes=10)},
        ],
        policies=[{"id": "p1", "topic": "commit-bumps-version", "enabled": True}, {"id": "p2", "topic": "elsewhere", "enabled": True},
                  {"id": "p3", "topic": "switched-off", "enabled": False}],
        results=[
            {"policy_id": "p1", "project_id": PROJECT, "outcome": "fail", "detail": "VERSION not raised", "checked_at": ago(minutes=20)},
            {"policy_id": "p2", "project_id": "other", "outcome": "fail", "detail": "x", "checked_at": ago(minutes=20)},
            {"policy_id": "p3", "project_id": PROJECT, "outcome": "fail", "detail": "x", "checked_at": ago(minutes=20)},
        ],
        incidents=[
            {"id": "i1", "project_id": "little-workshop", "pillar": "policy", "exception_type": "TimeoutError", "created_at": ago(minutes=30)},
            {"id": "i2", "project_id": PROJECT, "pillar": "agent", "exception_type": "ValueError", "created_at": ago(hours=5)},
            {"id": "i3", "project_id": PROJECT, "pillar": "agent", "exception_type": "KeyError", "status": "dismissed", "created_at": ago(minutes=10)},
        ],
        pillars={"registry": {"status": "healthy", "crash_count": 0}, "policy": {"status": "crashed", "crash_count": 2},
                 "agent": {"status": "faulted", "crash_count": 5}, "build": {"status": "disabled", "crash_count": 0}},
    )


# --- REQ-000000411: A project's status for Claude Code is one read ---


def test_the_status_counts_each_topic_of_the_project_and_of_the_workshop(paths, monkeypatch):
    _busy(monkeypatch)
    status = app_module.collect_status(paths, PROJECT, now=NOW)
    assert status["project"] == PROJECT and status["unavailable"] == []
    assert status["server"] == {"known": True, "running": True, "listening": True, "unit": "active"}
    assert status["running"]["count"] == 1 and status["running"]["items"][0]["title"] == "Export the list"
    # The Tasks page's "needs attention" group: a failed run, one awaiting finish and a task Job that failed
    # before its run existed; a dismissed one is done.
    assert {item["id"] for item in status["tasks_attention"]["items"]} == {"r1", "r4", "j6"}
    # A task Job that failed is a failed Job too; one that failed three hours ago, or on another project, is not.
    assert [item["id"] for item in status["failed_jobs"]["items"]] == ["j3", "j6"] and status["failed_jobs"]["hours"] == 2
    assert [item["title"] for item in status["scheduled"]["items"]] == ["Nightly tests"] and status["scheduled"]["hours"] == 1
    assert [item["title"] for item in status["policies"]["items"]] == ["commit-bumps-version"]
    assert [item["id"] for item in status["incidents"]["items"]] == ["i1"] and status["incidents"]["hours"] == 2
    assert [item["id"] for item in status["dead_pillars"]["items"]] == ["agent", "policy"] and status["dead_pillars"]["of"] == 3


def test_the_status_reads_the_jobs_filtered_by_their_owner(paths, monkeypatch):
    reads = _busy(monkeypatch)
    app_module.collect_status(paths, PROJECT, now=NOW)
    assert reads and all(read["project_id"] == PROJECT and read["summary"] for read in reads)
    failed = [read for read in reads if read.get("status") == "failed"]
    assert len(failed) == 1 and failed[0]["updated_since"] == NOW - timedelta(hours=2)


def test_a_project_the_registry_does_not_know_gets_only_the_workshop(paths, monkeypatch):
    _busy(monkeypatch)
    status = app_module.collect_status(paths, "nope", now=NOW)
    assert status["project"] is None and "running" not in status and "server" not in status
    assert status["incidents"]["count"] == 1 and status["dead_pillars"]["count"] == 2


def test_a_pillar_that_does_not_answer_is_unknown_never_zero(paths, monkeypatch):
    _busy(monkeypatch)

    def down(*args, **kwargs):
        raise client.PillarUnavailable("down")

    monkeypatch.setattr(client, "list_jobs", down)
    monkeypatch.setattr(client, "scheduled_tasks", down)
    monkeypatch.setattr(client, "deployment_status_optional", down)
    status = app_module.collect_status(paths, PROJECT, now=NOW)
    assert status["unavailable"] == ["deployment", "orchestration"]
    assert {key: status[key]["count"] for key in ("running", "tasks_attention", "failed_jobs", "scheduled")} == dict.fromkeys(
        ("running", "tasks_attention", "failed_jobs", "scheduled"))
    assert status["server"]["known"] is False and status["policies"]["count"] == 1


def test_a_project_without_a_server_has_none(paths, monkeypatch):
    _stub(monkeypatch, server=None)
    status = app_module.collect_status(paths, PROJECT, now=NOW)
    assert status["server"] is None and status["running"]["count"] == 0


def test_status_json_answers_for_the_project_asked(dashboard, monkeypatch):
    _busy(monkeypatch)
    body = dashboard.get("/status.json", params={"project": PROJECT}).json()
    assert body["project"] == PROJECT and body["policies"]["count"] == 1
    assert dashboard.get("/status.json").json()["project"] is None


# --- REQ-000000412: The status page shows every topic, zeros included ---


def test_the_status_page_shows_every_topic_and_the_items_behind_the_counts(dashboard, monkeypatch):
    _busy(monkeypatch)
    page = dashboard.get(f"/status/{PROJECT}").text
    for label in ("Server", "Running jobs", "Tasks needing attention", "Failed jobs", "Scheduled", "Failing policies", "Incidents", "Dead pillars"):
        assert label in page
    assert "Running</span>" in page and "of 3" in page
    assert 'href="/jobs/j3"' in page and "health check timed out" in page
    assert 'href="/tasks/little-meals/r1"' in page and 'href="/policies/p1"' in page and 'href="/incidents/i1"' in page
    assert 'data-live="jobs,tasks,schedule,policies,incidents,pillars"' in page and "window.liveRefresh" in page


def test_a_quiet_project_shows_its_zeros_and_no_empty_panels(dashboard, monkeypatch):
    _stub(monkeypatch, server={"unit": "inactive", "listening": False})
    page = dashboard.get(f"/status/{PROJECT}").text
    assert page.count('<span class="stat-value">0') == 7  # five project topics and two of the workshop's, zeros shown
    assert "Stopped</span>" in page and 'id="st-running"' not in page
    assert "Nothing runs, waits or failed for little-meals" in page and "every pillar is up" in page


def test_the_status_page_of_an_unknown_project_says_so(dashboard, monkeypatch):
    _stub(monkeypatch)
    page = dashboard.get("/status/nope").text
    assert "knows no project called nope" in page and "Dead pillars" in page and "Running jobs" not in page


# --- REQ-000000413: A Claude session is named after its project, version and branch ---


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=dev", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    checkout = tmp_path / PROJECT
    checkout.mkdir()
    _git(checkout, "init")
    (checkout / "VERSION").write_text("0.10.6\n")
    _git(checkout, "add", "VERSION")
    _git(checkout, "commit", "-m", "first")
    return checkout


def test_the_title_names_the_project_version_and_branch_of_the_checkout(repo):
    assert session_title.checkout(str(repo)) == {"project": PROJECT, "version": "0.10.6", "branch": "dev"}
    _git(repo, "worktree", "add", "-b", "improvement/x", str(repo.parent / "wt"))
    assert session_title.checkout(str(repo.parent / "wt")) == {"project": PROJECT, "version": "0.10.6", "branch": "improvement/x"}
    assert session_title.title(session_title.checkout(str(repo))) == "little-meals · 0.10.6 · dev"


def test_a_checkout_without_a_version_file_is_not_named(tmp_path):
    plain = tmp_path / "scratch"
    plain.mkdir()
    _git(plain, "init")
    assert session_title.checkout(str(plain)) is None and session_title.checkout(str(tmp_path)) is None


def test_the_project_is_found_for_a_plain_repository_a_submodule_and_a_bare_one():
    assert session_title.project_name("/home/me/little-meals/.git") == "little-meals"
    assert session_title.project_name("/home/me/little-projects/.git/modules/little-workshop") == "little-workshop"
    assert session_title.project_name("/srv/git/little-home.git") == "little-home"


def test_the_session_is_renamed_only_when_the_branch_or_the_version_changes():
    current = {"project": PROJECT, "version": "0.10.6", "branch": "dev"}
    prompt = {"hook_event_name": "UserPromptSubmit"}
    assert session_title.decide(prompt, current, None) == "little-meals · 0.10.6 · dev"
    assert session_title.decide(prompt, current, dict(current)) is None  # a name given since then stays
    assert session_title.decide(prompt, {**current, "branch": "feature/y"}, current) == "little-meals · 0.10.6 · feature/y"
    assert session_title.decide(prompt, {**current, "version": "0.10.7-dev.1"}, current) == "little-meals · 0.10.7-dev.1 · dev"
    for source in ("clear", "compact"):
        assert session_title.decide({"hook_event_name": "SessionStart", "source": source}, current, None) is None
    assert session_title.decide({"hook_event_name": "SessionStart", "source": "resume"}, current, None) is not None
    assert session_title.decide(prompt, None, None) is None


def test_the_hook_answers_with_the_title_once_per_change(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "s-1", "cwd": str(repo)}
    assert session_title.run(event) == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "little-meals · 0.10.6 · dev"}}
    assert session_title.run(event) is None
    (repo / "VERSION").write_text("0.10.7-dev.1\n")
    assert session_title.run(event)["hookSpecificOutput"]["sessionTitle"] == "little-meals · 0.10.7-dev.1 · dev"
    assert session_title.run({**event, "session_id": "s-2"}) is not None  # each session is named once on its own


def test_the_hook_never_blocks_on_what_it_cannot_read(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert session_title.main() == 0 and capsys.readouterr().out == ""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": "/nonexistent"})))
    assert session_title.main() == 0 and capsys.readouterr().out == ""


# --- REQ-000000414: The phone shows the project's status as icons ---


def test_the_mod_is_written_with_the_dashboards_socket_and_url():
    files = profile.mod_files(profile.StatusMod(Path("/state/sockets/dashboard.sock"), "http://localhost:8790/"))
    assert set(files) == set(profile.MOD_FILES)
    manifest = json.loads(files[".claude-plugin/plugin.json"])
    assert manifest["name"] == profile.MOD_NAME and manifest["types"] == "./types/index.d.ts"
    assert json.loads(files["hooks/hooks.json"]) == {"modules": ["./register.tsx"]}
    module = files["hooks/register.tsx"]
    assert 'const DASHBOARD_SOCKET = "/state/sockets/dashboard.sock"' in module
    assert 'const DASHBOARD_URL = "http://localhost:8790"' in module
    assert "@@" not in "".join(files.values())
    assert f"'{profile.MOD_NAME}'" in files["types/index.d.ts"]


def test_the_phone_and_the_status_page_use_the_same_icons():
    module = profile.mod_files(profile.StatusMod(Path("/s"), "http://localhost:8790"))["hooks/register.tsx"]
    for _, icon, *_ in (*app_module.STATUS_PROJECT_TOPICS, *app_module.STATUS_WORKSHOP_TOPICS):
        assert f"'{icon}'" in module
