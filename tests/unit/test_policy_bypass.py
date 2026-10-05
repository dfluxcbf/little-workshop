"""The user's temporary policy bypass (docs/pillars/policy_compliance.md, "Policy bypass"): the origin of
every request, the flag that lapses by itself, and which origins it lets a failing check past."""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common import origin as origins
from common.paths import Paths
from orchestration import db as orch_db
from orchestration import policy_gate
from orchestration.models import Job, JobStatus
from policy import app as app_module
from policy import bypass, db
from policy.app import build_app
from policy.models import CheckOutcome
from policy.sandbox import SandboxResult


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


# --- REQ-000000392: every request records its origin ------------------------------------------

def _proc(tmp_path, tree):
    """A fake /proc: {pid: (parent pid, comm, environ dict)}."""
    for pid, (ppid, comm, env) in tree.items():
        d = tmp_path / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0")
        (d / "comm").write_text(comm + "\n")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()))
    return tmp_path


def test_claude_is_recognised_by_its_environment_even_without_a_terminal(tmp_path):
    assert origins.cli_origin({"CLAUDECODE": "1"}, _proc(tmp_path, {})) == origins.CLAUDE


def test_claude_is_recognised_by_an_ancestor_process_when_sudo_cleared_the_environment(tmp_path, monkeypatch):
    proc = _proc(tmp_path, {10: (20, "sudo", {}), 20: (30, "bash", {}), 30: (1, "claude", {})})
    monkeypatch.setattr(origins.os, "getppid", lambda: 10)
    assert origins.invoked_by_claude({}, proc) is True


def test_an_ancestor_carrying_claudes_environment_marks_the_session_as_claudes(tmp_path, monkeypatch):
    proc = _proc(tmp_path, {10: (20, "bash", {"CLAUDE_CODE_ENTRYPOINT": "cli"}), 20: (1, "node", {})})
    monkeypatch.setattr(origins.os, "getppid", lambda: 10)
    assert origins.invoked_by_claude({}, proc) is True


def test_a_plain_terminal_is_the_users_cli(tmp_path, monkeypatch):
    proc = _proc(tmp_path, {10: (20, "bash", {}), 20: (1, "sshd", {})})
    monkeypatch.setattr(origins.os, "getppid", lambda: 10)
    tty = types.SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(origins.sys, "stdin", tty)
    monkeypatch.setattr(origins.sys, "stderr", tty)
    assert origins.cli_origin({}, proc) == origins.USER_CLI


def test_a_call_without_a_terminal_is_never_taken_for_the_user(tmp_path, monkeypatch):
    proc = _proc(tmp_path, {10: (1, "systemd", {})})
    monkeypatch.setattr(origins.os, "getppid", lambda: 10)
    notty = types.SimpleNamespace(isatty=lambda: False)
    monkeypatch.setattr(origins.sys, "stdin", notty)
    monkeypatch.setattr(origins.sys, "stderr", notty)
    assert origins.cli_origin({}, proc) == origins.UNKNOWN


def test_an_unrecognised_origin_is_unknown():
    assert origins.normalize("root-please") == origins.UNKNOWN
    assert origins.normalize(None) == origins.UNKNOWN
    assert origins.normalize("ui_job") == origins.UI_JOB


def test_the_clients_stamp_their_origin_on_every_job(monkeypatch):
    from cli import client as cli_client
    from dashboard import client as dashboard_client
    from mcp_server import client as mcp_client

    sent = []

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            sent.append(json)
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    for module in (cli_client, dashboard_client, mcp_client):
        monkeypatch.setattr(module, "client_for_socket", lambda *a, **k: Fake())
    monkeypatch.setattr(dashboard_client, "resolve_pillar_socket", lambda *a, **k: "s")
    monkeypatch.setattr(mcp_client, "resolve_pillar_socket", lambda *a, **k: "s")
    monkeypatch.setattr(cli_client, "cli_origin", lambda: origins.USER_CLI)
    paths = Paths(config_home=Path("/x"), state_home=Path("/y/state"))
    fields = dict(type="t", target_pillar="p")
    cli_client.post_job(paths, **fields)
    dashboard_client.create_job(paths, **fields)
    mcp_client.create_job(paths, **fields)
    assert [s["origin"] for s in sent] == [origins.USER_CLI, origins.UI_JOB, origins.CLAUDE]


def test_a_job_keeps_its_origin_in_the_store(tmp_path):
    store = orch_db.connect(tmp_path / "o.db")
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="p", origin=origins.SCHEDULED_JOB)
    orch_db.insert_job(store, job)
    assert orch_db.get_job(store, job.id).origin == origins.SCHEDULED_JOB
    assert Job(type="x", target_pillar="y").origin == origins.UNKNOWN


def test_the_scheduler_creates_its_jobs_with_the_scheduled_origin(tmp_path):
    from orchestration import scheduler
    from orchestration.models import JobTemplate, Schedule, ScheduledTask, ScheduleKind

    store = orch_db.connect(tmp_path / "o.db")
    task = ScheduledTask(name="n", job_template=JobTemplate(type="deployment.deploy", target_pillar="deployment"),
                         schedule=Schedule(kind=ScheduleKind.ONCE, at=datetime.now(timezone.utc)))
    job = scheduler._create_job_from_template(store, task, lambda pillar: None)
    assert job.origin == origins.SCHEDULED_JOB


# --- REQ-000000393: the flag, its duration and its expiry -------------------------------------

def test_the_flag_lasts_two_minutes_by_default_then_lapses(conn):
    clock = Clock()
    state = bypass.enable(conn, minutes=None, origin=origins.USER_CLI, actor="me", now=clock)
    assert state["bypass_policies"] is True and state["remaining_seconds"] == 120
    clock.advance(seconds=119)
    assert bypass.status(conn, clock)["bypass_policies"] is True
    clock.advance(seconds=2)
    assert bypass.status(conn, clock) == {"bypass_policies": False, "expires_at": None, "remaining_seconds": 0, "enabled_by": None}
    assert [e["action"] for e in bypass.log_entries(conn)] == ["expired", "enabled"]


def test_the_flag_lasts_the_time_asked_up_to_thirty_minutes(conn):
    state = bypass.enable(conn, minutes=30, origin=origins.USER_CLI, actor="me", now=Clock())
    assert state["remaining_seconds"] == 30 * 60


@pytest.mark.parametrize("minutes", [31, 0, -5])
def test_a_time_outside_one_to_thirty_minutes_is_refused_not_clamped(conn, minutes):
    with pytest.raises(bypass.BypassRefused, match="between 1 and 30"):
        bypass.enable(conn, minutes=minutes, origin=origins.USER_CLI, actor="me")
    assert bypass.status(conn)["bypass_policies"] is False


def test_asking_again_while_true_restarts_the_timer_from_now(conn):
    clock = Clock()
    bypass.enable(conn, minutes=10, origin=origins.USER_CLI, actor="me", now=clock)
    clock.advance(minutes=8)
    state = bypass.enable(conn, minutes=3, origin=origins.USER_CLI, actor="me", now=clock)
    assert state["remaining_seconds"] == 180


@pytest.mark.parametrize("origin", [origins.CLAUDE, origins.SCHEDULED_JOB, origins.UI_JOB, origins.AUTOMATION, origins.UNKNOWN])
def test_only_the_users_cli_can_turn_the_flag_on(conn, origin):
    with pytest.raises(bypass.BypassRefused, match="user's own terminal"):
        bypass.enable(conn, minutes=5, origin=origin, actor="me")
    assert bypass.status(conn)["bypass_policies"] is False


def test_turning_it_on_needs_the_super_user_the_cli_names(conn):
    with pytest.raises(bypass.BypassRefused, match="sudo"):
        bypass.enable(conn, minutes=5, origin=origins.USER_CLI, actor="")


# --- REQ-000000396: turning it on and off is audited (the log is read here and in the endpoint test) ---

def test_anyone_can_turn_the_flag_off_at_once_and_it_is_logged(conn):
    clock = Clock()
    bypass.enable(conn, minutes=5, origin=origins.USER_CLI, actor="me", now=clock)
    state = bypass.disable(conn, origin=origins.UI_JOB, actor="dashboard", now=clock)
    assert state["bypass_policies"] is False
    assert [(e["action"], e["actor"], e["origin"]) for e in bypass.log_entries(conn)][0] == ("disabled", "dashboard", "ui_job")
    assert bypass.disable(conn, origin=origins.UI_JOB, actor="dashboard", now=clock)["bypass_policies"] is False
    assert [e["action"] for e in bypass.log_entries(conn)] == ["disabled", "enabled"]  # off when off: nothing to log


def test_the_flag_endpoint_enables_refuses_and_disables(conn, tmp_path):
    api = TestClient(build_app(conn, Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    assert api.get("/v1/bypass").json()["bypass_policies"] is False
    refused = api.post("/v1/bypass", json={"enabled": True, "minutes": 5, "origin": "claude", "actor": "me"})
    assert refused.status_code == 403
    too_long = api.post("/v1/bypass", json={"enabled": True, "minutes": 45, "origin": "user_cli", "actor": "me"})
    assert too_long.status_code == 400
    ok = api.post("/v1/bypass", json={"enabled": True, "minutes": 5, "origin": "user_cli", "actor": "me"})
    assert ok.json()["bypass_policies"] is True and api.get("/v1/bypass").json()["enabled_by"] == "me"
    assert [e["action"] for e in api.get("/v1/bypass/log").json()] == ["enabled"]
    api.post("/v1/bypass", json={"enabled": False, "origin": "ui_job", "actor": "dashboard"})
    assert api.get("/v1/bypass").json()["bypass_policies"] is False


# --- REQ-000000394: what the flag lets through, and what it never does -------------------------

@pytest.fixture
def api(conn, tmp_path, monkeypatch):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    paths.ensure_layout()
    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: ["proj-a"])
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {"path": str(tmp_path), "name": project_id})
    client = TestClient(build_app(conn, paths))
    params = {"ears_text": "The project shall be tidy.", "test_script": "def test_x(): pass\n", "topic": "tidy",
              "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
              "gate_points": ["branch_finish"], "approved": True}
    assert client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()["outcome"] == "succeeded"
    return client


def _gating(api, origin):
    rows = api.get("/v1/policies/gating-for-job", params={"project_id": "proj-a", "categories": "branch_finish", "origin": origin}).json()
    return [(r["gating"], r["bypassed"]) for r in rows]


def _bypass_on(api):
    api.post("/v1/bypass", json={"enabled": True, "minutes": 5, "origin": "user_cli", "actor": "me"})


def test_without_the_flag_every_origin_is_gated(api):
    for origin in origins.ORIGINS:
        assert _gating(api, origin) == [(True, False)]


def test_with_the_flag_the_users_cli_is_checked_but_not_gated(api):
    _bypass_on(api)
    assert _gating(api, "user_cli") == [(False, True)]


@pytest.mark.parametrize("origin", ["claude", "scheduled_job", "ui_job", "automation", "unknown", "nonsense"])
def test_with_the_flag_claude_scheduled_jobs_and_the_ui_are_still_gated(api, origin):
    _bypass_on(api)
    assert _gating(api, origin) == [(True, False)]


def test_a_lapsed_flag_gates_the_users_cli_again(api, conn):
    _bypass_on(api)
    conn.execute("UPDATE policy_bypass SET expires_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    conn.commit()
    assert _gating(api, "user_cli") == [(True, False)]


def _evaluate(api, monkeypatch, origin):
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda script, mounts, **kw: SandboxResult(CheckOutcome.FAIL, "untidy"))
    return api.post("/v1/policies/evaluate", json={"project_id": "proj-a", "gate_point": "branch_finish", "origin": origin}).json()


def test_evaluate_from_the_users_cli_reports_the_violation_without_blocking(api, monkeypatch):
    _bypass_on(api)
    result = _evaluate(api, monkeypatch, "user_cli")
    assert result["blocking"] == [] and result["bypassed"] == ["tidy"]
    assert result["results"][0]["outcome"] == "fail" and result["results"][0]["bypassed"] is True
    assert [e["action"] for e in api.get("/v1/bypass/log").json()][0] == "violation_bypassed"


def test_evaluate_from_claude_still_blocks_while_the_flag_is_true(api, monkeypatch):
    _bypass_on(api)
    result = _evaluate(api, monkeypatch, "claude")
    assert result["blocking"] == ["tidy"] and result["bypassed"] == []


def test_the_cli_tells_the_user_a_violation_was_bypassed():
    from cli import main

    text = main.format_evaluation({
        "gate_point": "branch_finish", "project_id": "p", "blocking": [], "bypassed": ["tidy"],
        "results": [{"topic": "tidy", "outcome": "fail", "gating": False, "bypassed": True, "ears_text": "r", "detail": "untidy"}]})
    assert "BYPASSED" in text and "tidy failed and would have blocked" in text


# --- the check job: gating decided by origin, verdict still recorded ----------------------------

def _gate_app(seen):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str, origin: str = "unknown") -> list[dict]:
        seen.append(origin)
        bypassed = origin == "user_cli"
        return [{"id": "pol1", "gating": not bypassed, "bypassed": bypassed}]

    return app


def test_a_jobs_origin_reaches_the_policy_pillar_and_its_check_records_it(tmp_path, monkeypatch):
    from socket_test_helpers import patch_socket_apps

    seen = []
    patch_socket_apps(monkeypatch, policy_gate, {"fake-socket-policy": _gate_app(seen)})
    store = orch_db.connect(tmp_path / "o.db")
    held = {}
    for origin in ("user_cli", "claude", "scheduled_job", "ui_job"):
        job = Job(type="deployment.deploy", target_pillar="deployment", project_id="p", origin=origin)
        orch_db.insert_job(store, job)
        policy_gate.attach_policy_gate(store, lambda pillar: "fake-socket-policy", job)
        check = orch_db.get_job(store, job.depends_on[0])
        check.status, check.result = JobStatus.SUCCEEDED, {"outcome": "fail"}
        orch_db.update_job(store, check)
        held[origin] = policy_gate.maybe_gate(store, job, runner=_Runner())
        assert check.params["origin"] == origin
    assert seen == ["user_cli", "claude", "scheduled_job", "ui_job"]
    assert held == {"user_cli": False, "claude": True, "scheduled_job": True, "ui_job": True}


class _Runner:
    def run(self, argv, timeout=None, cwd=None):
        from common.shell import CommandResult

        return CommandResult(0, "", "")


def test_a_bypassed_failure_is_still_recorded_with_its_origin(api, conn, monkeypatch, tmp_path):
    """The check job runs and records `fail`, flagged bypassed, so the Dashboard and its statistics count it."""
    import time

    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda script, mounts, **kw: SandboxResult(CheckOutcome.FAIL, "untidy"))
    policy_id = db.list_policies(conn)[0].id
    api.post("/v1/jobs/policy.check_project", json={"job_id": "jc", "project_id": "proj-a", "params": {
        "policy_id": policy_id, "gating": False, "bypassed": True, "origin": "user_cli"}})
    for _ in range(100):
        rows = db.list_check_results(conn)
        if rows:
            break
        time.sleep(0.05)
    assert [(r.outcome, r.origin, r.bypassed) for r in rows] == [(CheckOutcome.FAIL, "user_cli", True)]
    listed = api.get("/v1/policy_check_results", params={"outcome": "fail"}).json()
    assert listed[0]["bypassed"] is True and listed[0]["origin"] == "user_cli"


# --- REQ-000000393 (CLI): sudo, not Claude ----------------------------------------------------

def _cli(monkeypatch, *, root=True, sudo_user="me", claude=False, calls=None):
    from cli import main
    from cli import client as cli_client

    monkeypatch.setattr(main.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr(main.os, "environ", {"SUDO_USER": sudo_user} if sudo_user else {})
    monkeypatch.setattr(origins, "invoked_by_claude", lambda *a, **k: claude)
    monkeypatch.setattr(main, "_bypass_paths", lambda: Paths(config_home=Path("/c"), state_home=Path("/s")))
    sent = calls if calls is not None else []

    def fake_set(paths, *, enabled, minutes, actor, **kw):
        sent.append((enabled, minutes, actor))
        return {"bypass_policies": enabled, "remaining_seconds": (minutes or 0) * 60}

    monkeypatch.setattr(cli_client, "policy_bypass_set", fake_set)
    return main, sent


def test_sudo_lws_bypass_policies_true_sets_the_flag(monkeypatch, capsys):
    main, sent = _cli(monkeypatch)
    assert main.main(["bypass_policies", "true", "--time", "7"]) == 0
    assert sent == [(True, 7, "me")]
    assert "bypass_policies is TRUE for 7m00s" in capsys.readouterr().out


def test_without_time_the_cli_asks_for_the_two_minute_default(monkeypatch):
    main, sent = _cli(monkeypatch)
    assert main.main(["bypass_policies", "true"]) == 0
    assert sent == [(True, 2, "me")]


def test_more_than_thirty_minutes_is_an_error_not_a_clamp(monkeypatch, capsys):
    main, sent = _cli(monkeypatch)
    assert main.main(["bypass_policies", "true", "--time", "31"]) == 2
    assert sent == [] and "between 1 and 30" in capsys.readouterr().err


def test_without_the_super_user_the_command_fails(monkeypatch, capsys):
    main, sent = _cli(monkeypatch, root=False, sudo_user=None)
    assert main.main(["bypass_policies", "true"]) == 1
    assert sent == [] and "needs the super user" in capsys.readouterr().err


def test_plain_root_without_sudo_is_not_enough(monkeypatch):
    main, sent = _cli(monkeypatch, root=True, sudo_user=None)
    assert main.main(["bypass_policies", "true"]) == 1 and sent == []


def test_invoked_by_claude_the_command_fails_even_as_root(monkeypatch, capsys):
    main, sent = _cli(monkeypatch, claude=True)
    assert main.main(["bypass_policies", "true"]) == 1
    assert sent == [] and "Claude may not turn it on" in capsys.readouterr().err


def test_the_cli_can_turn_the_flag_off_and_read_it_without_sudo(monkeypatch, capsys):
    from cli import client as cli_client

    main, sent = _cli(monkeypatch, root=False, sudo_user=None)
    monkeypatch.setattr(cli_client, "policy_bypass_status", lambda paths: {"bypass_policies": True, "remaining_seconds": 61})
    assert main.main(["bypass_policies", "false"]) == 0
    assert sent == [(False, None, "")]
    assert main.main(["bypass_policies", "status"]) == 0
    assert "TRUE for 1m01s" in capsys.readouterr().out


def test_the_pillars_refusal_of_the_origin_is_reported(monkeypatch, capsys):
    from cli import client as cli_client

    main, _ = _cli(monkeypatch)

    def refuse(*a, **k):
        raise cli_client.PolicyBypassRefused("origin 'claude' refused")

    monkeypatch.setattr(cli_client, "policy_bypass_set", refuse)
    assert main.main(["bypass_policies", "true"]) == 1
    assert "origin 'claude' refused" in capsys.readouterr().err


# --- REQ-000000395: the Dashboard warns and can switch it off ------------------------------------

@pytest.fixture
def dash(tmp_path):
    from dashboard import app as dashboard_app

    return TestClient(dashboard_app.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))


def test_the_dashboard_warns_with_the_remaining_time_while_the_flag_is_true(dash, monkeypatch):
    from dashboard import client

    monkeypatch.setattr(client, "policy_bypass", lambda paths: {"bypass_policies": True, "remaining_seconds": 95})
    text = dash.get("/policies").text
    assert "bypass_policies is TRUE" in text and ">1:35<" in text and "Set to FALSE now" in text


def test_the_dashboard_shows_no_warning_while_the_flag_is_false(dash, monkeypatch):
    from dashboard import client

    monkeypatch.setattr(client, "policy_bypass", lambda paths: {"bypass_policies": False, "remaining_seconds": 0})
    assert "bypass_policies is TRUE" not in dash.get("/policies").text


def test_the_dashboards_button_sets_the_flag_to_false_and_goes_back(dash, monkeypatch):
    from dashboard import client

    calls = []
    monkeypatch.setattr(client, "policy_bypass_off", lambda paths: calls.append("off") or {"bypass_policies": False})
    response = dash.post("/policy-bypass/off", data={"next": "/policies"}, follow_redirects=False)
    assert calls == ["off"] and response.status_code == 303
    assert response.headers["location"].startswith("/policies?message=")


def test_the_dashboard_button_only_ever_lowers_the_flag(monkeypatch, tmp_path):
    from dashboard import client

    sent = []

    def fake_send(paths, pillar, method, path, *, json=None, timeout=30.0):
        sent.append((pillar, method, path, json))
        return {}

    monkeypatch.setattr(client, "_send", fake_send)
    client.policy_bypass_off(Paths(config_home=tmp_path, state_home=tmp_path))
    assert sent == [("policy", "POST", "/v1/bypass", {"enabled": False, "origin": "ui_job", "actor": "dashboard"})]
