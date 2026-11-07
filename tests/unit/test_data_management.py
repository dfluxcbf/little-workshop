"""System > Data management: deleting a project's local data, keeping its requirements apart, and what an
unregistered project no longer raises."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent import db as agent_db
from build import db as build_db
from common.paths import Paths
from dashboard import app as app_module
from dashboard import client
from deployment import db as deployment_db
from orchestration import db as orchestration_db
from orchestration.models import Job, JobStatus, JobTemplate, Schedule, ScheduledTask
from policy import db as policy_db
from requirements import db as req_db
from requirements import transfer
from requirements.app import analyze_conflicts
from requirements.models import Milestone, Requirement, TestResult, TestSuite, TestVerdict, UseCase


def _fresh(module, tmp_path, name):
    return module.connect(tmp_path / f"{name}.db")


# --- REQ-000000469: Data management deletes a project's local data and registration ---

def test_policy_purge_removes_the_projects_results_and_associations_only(tmp_path):
    conn = _fresh(policy_db, tmp_path, "policy")
    for project in ("gone", "kept"):
        conn.execute("INSERT INTO policy_projects (policy_id, project_id, gating_enabled) VALUES ('p1', ?, 1)", (project,)) \
            if "gating_enabled" in {r["name"] for r in conn.execute("PRAGMA table_info(policy_projects)")} else \
            conn.execute("INSERT INTO policy_projects (policy_id, project_id) VALUES ('p1', ?)", (project,))
    removed = policy_db.purge_project(conn, "gone")
    assert removed["policy_projects"] == 1
    assert [r["project_id"] for r in conn.execute("SELECT project_id FROM policy_projects")] == ["kept"]


def test_agent_purge_removes_incidents_and_runs_of_the_project_only(tmp_path):
    conn = _fresh(agent_db, tmp_path, "agent")
    agent_db.create_incident(conn, "i1", project_id="gone", pillar="x", context="", exception_type="E", message="", traceback="")
    agent_db.create_incident(conn, "i2", project_id="kept", pillar="x", context="", exception_type="E", message="", traceback="")
    removed = agent_db.purge_project(conn, "gone")
    assert removed["incidents"] == 1
    assert [i["id"] for i in agent_db.list_incidents(conn)] == ["i2"]


def test_deployment_and_build_purge_remove_the_projects_records(tmp_path):
    deployment = _fresh(deployment_db, tmp_path, "deployment")
    deployment_db.save_server_state(deployment, "gone", "master", "1.0.0", "abc")
    deployment_db.save_server_state(deployment, "kept", "master", "1.0.0", "abc")
    assert deployment_db.purge_project(deployment, "gone")["servers"] == 1
    assert [s["project_id"] for s in deployment_db.list_server_states(deployment)] == ["kept"]

    build = _fresh(build_db, tmp_path, "build")
    build.execute("INSERT INTO build_runs (id, project_id, commitish, command, status, started_at) VALUES ('b1','gone','x','c','done','t')")
    assert build_db.purge_project(build, "gone")["build_runs"] == 1


def test_orchestration_purge_removes_every_job_of_the_project_and_its_scheduled_tasks(tmp_path):
    conn = _fresh(orchestration_db, tmp_path, "orchestration")
    done = Job(type="noop", target_pillar="x", project_id="gone", status=JobStatus.SUCCEEDED)
    running = Job(type="noop", target_pillar="x", project_id="gone", status=JobStatus.RUNNING)
    other = Job(type="noop", target_pillar="x", project_id="kept", status=JobStatus.SUCCEEDED)
    for job in (done, running, other):
        orchestration_db.insert_job(conn, job)
    task = ScheduledTask(name="t", job_template=JobTemplate(type="noop", target_pillar="x", project_id="gone"),
                         schedule=Schedule(kind="recurring", days_of_week=[0], time_of_day="09:00"))
    orchestration_db.insert_scheduled_task(conn, task)
    result = orchestration_db.purge_project(conn, "gone")
    assert result["jobs_deleted"] == 2 and result["scheduled_tasks_deleted"] == 1
    assert {j.id for j in orchestration_db.list_jobs(conn)} == {other.id}
    assert done.id not in {j.id for j in orchestration_db.list_jobs(conn)} and running.id not in {j.id for j in orchestration_db.list_jobs(conn)}


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def web(paths, monkeypatch):
    calls = []

    def create_job(paths, **fields):
        calls.append(fields)
        return {"status": "succeeded", **fields}

    monkeypatch.setattr(client, "create_job", create_job)
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "path": "/x", "status": "active"}])
    monkeypatch.setattr(client, "requirement_projects", lambda paths: [
        {"project_id": "little-widget", "requirements": 2, "published": 1, "test_suites": 1, "use_cases": 0, "milestones": 0,
         "test_results": 3, "open_conflicts": 0, "updated_at": None},
        {"project_id": "little-mindcheck", "requirements": 5, "published": 5, "test_suites": 1, "use_cases": 0, "milestones": 0,
         "test_results": 0, "open_conflicts": 1, "updated_at": None}])
    purged, stopped = [], []
    monkeypatch.setattr(client, "purge_orchestration", lambda paths, project: purged.append(project) or {})
    monkeypatch.setattr(client, "deployment_status_optional", lambda paths, name: None)
    monkeypatch.setattr(client, "running_job_ids", lambda paths, project: ["running-1"])
    monkeypatch.setattr(client, "stop_job", lambda paths, job_id, force=False: stopped.append(job_id) or {})
    test_client = TestClient(app_module.build_app(paths), follow_redirects=False)
    test_client.calls, test_client.purged, test_client.stopped = calls, purged, stopped
    return test_client


def test_delete_project_needs_the_name_typed(web):
    response = web.post("/data/projects/little-widget/delete", data={"confirm": "nope"})
    assert response.status_code == 303 and web.calls == []


def test_delete_project_removes_the_registration_then_every_pillars_data_but_not_requirements(web):
    response = web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert response.status_code == 303
    types = [call["type"] for call in web.calls]
    assert types[0] == "project.remove"
    assert set(types[1:]) == {"usage.purge_project", "policy.purge_project", "agent.purge_project",
                              "deployment.purge_project", "build.purge_project"}
    assert not any(t.startswith("requirements.") for t in types)
    assert web.purged == ["little-widget"]
    assert web.stopped == ["running-1"]  # the project's running jobs are stopped before its jobs are deleted


def test_a_running_server_is_stopped_before_the_project_is_removed(web, monkeypatch):
    monkeypatch.setattr(client, "deployment_status_optional", lambda paths, name: {"unit": "active", "listening": True})
    web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert [c["type"] for c in web.calls][:2] == ["deployment.stop", "project.remove"]


def test_a_server_that_would_not_stop_keeps_the_project(web, monkeypatch):
    monkeypatch.setattr(client, "deployment_status_optional", lambda paths, name: {"listening": True})
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: {"status": "failed", "error": "port still busy"})
    response = web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert "could not be stopped" in unquote_message(response.headers["location"])
    assert web.purged == []


def test_nothing_is_deleted_when_deployment_cannot_say_whether_a_server_runs(web, monkeypatch):
    def down(paths, name):
        raise client.PillarUnavailable("deployment")

    monkeypatch.setattr(client, "deployment_status_optional", down)
    web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert web.calls == []


def test_a_held_project_removal_purges_nothing_else(web, monkeypatch):
    monkeypatch.setattr(client, "create_job", lambda paths, **fields: {"status": "pending_confirmation"})
    web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert web.purged == []


def test_data_page_lists_projects_and_tags_unregistered_requirement_projects(web):
    text = web.get("/data").text
    assert "Delete project" in text and "little-widget" in text
    mindcheck = text.index("little-mindcheck")
    assert "not registered" in text[mindcheck:mindcheck + 400]
    assert text.count("not registered") == 1


# --- REQ-000000470: An unregistered project raises nothing ---

def test_attention_and_incidents_skip_unregistered_projects(paths, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget"}])
    monkeypatch.setattr(client, "policy_list", lambda *a, **k: [])
    monkeypatch.setattr(client, "list_jobs", lambda *a, **k: [])
    monkeypatch.setattr(client, "requirement_conflicts", lambda paths, **k: [
        {"project_id": "little-mindcheck", "status": "open"}, {"project_id": "little-widget", "status": "open"}])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [
        {"id": "1", "project_id": "little-mindcheck", "exception_type": "E", "pillar": "p", "created_at": "t"},
        {"id": "2", "project_id": "little-widget", "exception_type": "E", "pillar": "p", "created_at": "t"}])
    monkeypatch.setattr(app_module, "_latest_policy_results", lambda paths, failing=False: {})
    projects = {item["project"] for item in app_module.collect_attention(paths)}
    assert projects == {"little-widget"}


def test_nothing_is_quieted_when_the_registry_does_not_answer(paths, monkeypatch):
    def down(paths, status=None):
        raise client.PillarUnavailable("registry")

    monkeypatch.setattr(client, "list_projects", down)
    rows = [{"project_id": "anything"}]
    assert app_module.only_registered(rows, app_module.registered_names(paths)) == rows


def test_conflict_analysis_of_every_project_covers_only_registered_ones(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    import threading
    for project in ("little-widget", "little-mindcheck"):
        for key in ("REQ-000000001", "REQ-000000002"):
            req_db.insert_requirement(conn, Requirement(id=f"{project}-{key}", project_id=project, key=key, title=key,
                                                        description="The system shall x."))
    result = analyze_conflicts(conn, threading.Lock(), only={"little-widget"})
    assert set(result) == {"little-widget"}


# --- REQ-000000471: Data management lists and deletes stored requirements ---

def _seed(conn, project="gone", n=2):
    use_case = UseCase(id=f"{project}-uc", project_id=project, name="uc")
    milestone = Milestone(id=f"{project}-ms", project_id=project, name="ms")
    suite = TestSuite(id=f"{project}-suite", project_id=project, name="suite")
    req_db.insert_use_case(conn, use_case)
    req_db.insert_milestone(conn, milestone)
    req_db.insert_test_suite(conn, suite)
    for i in range(1, n + 1):
        req = Requirement(id=f"{project}-r{i}", project_id=project, key=f"REQ-00000000{i}", title=f"t{i}",
                          description="The system shall x.", use_case_id=use_case.id, milestone_id=milestone.id)
        req_db.insert_requirement(conn, req)
        req_db.link_requirement_to_suite(conn, req.id, suite.id)
    req_db.insert_test_result(conn, TestResult(id=f"{project}-res", project_id=project, suite_id=suite.id, test_name="t",
                                               verdict=TestVerdict.PASSED))


def test_summaries_count_what_is_stored_per_project(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    _seed(conn, "gone", 3)
    _seed(conn, "other", 1)
    summary = {s["project_id"]: s for s in transfer.project_summaries(conn)}
    assert summary["gone"]["requirements"] == 3 and summary["gone"]["test_results"] == 1 and summary["gone"]["test_suites"] == 1
    assert [s["project_id"] for s in transfer.project_summaries(conn)][0] == "gone"


def test_deleting_a_projects_requirements_leaves_other_projects_alone(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    _seed(conn, "gone")
    _seed(conn, "other")
    transfer.delete_project(conn, "gone")
    assert req_db.list_requirements(conn, project_id="gone") == []
    assert len(req_db.list_requirements(conn, project_id="other")) == 2
    assert conn.execute("SELECT COUNT(*) FROM requirement_test_mappings WHERE suite_id LIKE 'gone%'").fetchone()[0] == 0
    assert len(req_db.list_test_results(conn, project_id="other")) == 1


def test_delete_route_needs_the_project_id_typed(web):
    assert web.post("/data/requirements/little-mindcheck/delete", data={"confirm": "x"}).status_code == 303
    assert web.calls == []
    web.post("/data/requirements/little-mindcheck/delete", data={"confirm": "little-mindcheck"})
    assert web.calls[0]["type"] == "requirements.delete_project_data" and web.calls[0]["project_id"] == "little-mindcheck"


# --- REQ-000000472: Requirements export and import resolve ids ---

def test_export_then_import_round_trips_under_new_ids(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    _seed(conn, "gone", 2)
    backup = transfer.export_project(conn, "gone")
    transfer.delete_project(conn, "gone")
    result = transfer.import_project(conn, backup, project_id="restored")
    assert result["imported"]["requirements"] == 2 and result["renumbered"] == []
    restored = req_db.list_requirements(conn, project_id="restored")
    assert [r.key for r in restored] == ["REQ-000000001", "REQ-000000002"]
    assert not {r.id for r in restored} & {"gone-r1", "gone-r2"}
    suite_ids = req_db.suites_for_requirements(conn, [r.id for r in restored])
    assert all(len(ids) == 1 for ids in suite_ids.values())
    assert req_db.list_use_cases(conn, project_id="restored")[0].id != "gone-uc"
    assert restored[0].use_case_id == req_db.list_use_cases(conn, project_id="restored")[0].id
    assert len(req_db.list_test_results(conn, project_id="restored")) == 1


def test_import_into_a_project_with_the_same_keys_renumbers_them_to_the_next_free_key(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    _seed(conn, "gone", 2)
    _seed(conn, "target", 2)
    result = transfer.import_project(conn, transfer.export_project(conn, "gone"), project_id="target")
    assert result["renumbered"] == [{"old": "REQ-000000001", "new": "REQ-000000003"}, {"old": "REQ-000000002", "new": "REQ-000000004"}]
    keys = [r.key for r in req_db.list_requirements(conn, project_id="target")]
    assert len(keys) == len(set(keys)) == 4
    ids = [r.id for r in req_db.list_requirements(conn)]
    assert len(ids) == len(set(ids))


def test_next_free_key_keeps_prefix_and_width_and_handles_keys_without_a_number():
    assert transfer.next_free_key("REQ-000000009", {"REQ-000000009", "REQ-000000012"}) == "REQ-000000013"
    assert transfer.next_free_key("FEATURE", {"FEATURE", "FEATURE-2"}) == "FEATURE-3"


def test_import_rejects_a_foreign_file_a_bad_id_and_writes_nothing(tmp_path):
    conn = _fresh(req_db, tmp_path, "requirements")
    with pytest.raises(transfer.TransferError):
        transfer.import_project(conn, {"format": "something-else"}, project_id="x")
    _seed(conn, "gone")
    backup = transfer.export_project(conn, "gone")
    with pytest.raises(transfer.TransferError):
        transfer.import_project(conn, backup, project_id="not valid/id")
    backup["requirements"][0]["status"] = "bogus"
    with pytest.raises(transfer.TransferError):
        transfer.import_project(conn, backup, project_id="fine")
    assert req_db.list_requirements(conn, project_id="fine") == []


def test_import_route_uses_the_chosen_project_id(web, monkeypatch):
    sent = []
    monkeypatch.setattr(client, "requirements_import", lambda paths, project, data: sent.append((project, data)) or {
        "outcome": "succeeded", "result": {"imported": {"requirements": 1}, "renumbered": [{"old": "A-1", "new": "A-2"}]}})
    backup = json.dumps({"format": transfer.FORMAT, "version": 1, "project_id": "little-mindcheck"}).encode()

    def post(**form):
        return web.post("/data/requirements/import", files={"backup": ("b.json", backup, "application/json")}, data=form)

    post(target="original")
    post(target="custom", custom_id="archive-1")
    post(target="existing", existing="little-widget")
    assert [project for project, _ in sent] == ["little-mindcheck", "archive-1", "little-widget"]
    assert "renumbered" in unquote_message(post(target="original").headers["location"])


def unquote_message(location: str) -> str:
    from urllib.parse import unquote
    return unquote(location)


def test_export_route_downloads_a_json_backup(web, monkeypatch):
    monkeypatch.setattr(client, "requirements_export", lambda paths, project: {"format": transfer.FORMAT, "project_id": project})
    response = web.get("/data/requirements/little-mindcheck/export")
    assert response.headers["content-disposition"].startswith('attachment; filename="requirements-little-mindcheck-')
    assert response.json()["project_id"] == "little-mindcheck"


def test_no_trigger_may_create_a_purge_job():
    from orchestration import triggers
    for job_type in ("usage.purge_project", "policy.purge_project", "agent.purge_project", "build.purge_project"):
        assert triggers.FORBIDDEN_TARGET.search(job_type)


# --- REQ-000000469: a pillar that cannot be reached is tried again, and needs attention when it stays unreachable ---

from datetime import datetime, timedelta, timezone  # noqa: E402

from dashboard import deletions  # noqa: E402

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
STEPS = [{"type": "policy.purge_project", "pillar": "policy"}, {"type": "orchestration.purge", "pillar": "orchestration"}]


def _runner(failing: set[str], log: list | None = None):
    def run(project, step):
        if log is not None:
            log.append(step["pillar"])
        return (False, f"{step['pillar']} unreachable", "job-1") if step["pillar"] in failing else (True, "", None)
    return run


def test_a_deletion_owing_nothing_is_not_recorded(paths):
    deletions.begin(paths, "gone", [], [], T0)
    assert deletions.load(paths) == {}


def test_a_retry_attempts_only_what_is_owing_and_forgets_a_finished_deletion(paths):
    deletions.begin(paths, "gone", STEPS, [], T0)
    log = []
    left = deletions.retry(paths, "gone", _runner({"orchestration"}, log), T0 + timedelta(minutes=10))
    assert [s["pillar"] for s in left["pending"]] == ["orchestration"] and left["attempts"] == 2
    assert left["errors"][-1]["error"] == "orchestration unreachable" and left["errors"][-1]["job_id"] == "job-1"
    deletions.retry_all(paths, _runner(set(), log), T0 + timedelta(minutes=20))
    assert log == ["policy", "orchestration", "orchestration"] and deletions.load(paths) == {}


def test_a_deletion_is_stuck_only_after_two_hours(paths):
    deletions.begin(paths, "gone", STEPS, [], T0)
    deletion = deletions.load(paths)["gone"]
    assert not deletions.stuck(deletion, T0 + timedelta(hours=2))
    assert deletions.stuck(deletion, T0 + timedelta(hours=2, seconds=1))


def test_try_again_restarts_the_wait_and_try_again_once_does_not(paths):
    deletions.begin(paths, "gone", STEPS, [], T0)
    later = T0 + timedelta(hours=3)
    deletions.retry(paths, "gone", _runner({"policy", "orchestration"}), later)  # once
    assert deletions.stuck(deletions.load(paths)["gone"], later)
    deletions.retry(paths, "gone", _runner({"policy", "orchestration"}), later, restart_clock=True)
    assert not deletions.stuck(deletions.load(paths)["gone"], later)


def test_only_the_last_errors_are_kept(paths):
    deletions.begin(paths, "gone", STEPS, [], T0)
    for n in range(deletions.KEPT_ERRORS):
        deletions.retry(paths, "gone", _runner({"policy", "orchestration"}), T0 + timedelta(minutes=n))
    assert len(deletions.load(paths)["gone"]["errors"]) == deletions.KEPT_ERRORS


def test_an_unreachable_pillar_leaves_its_step_owing_and_the_rest_done(web, monkeypatch, paths):
    def create_job(paths_, **fields):
        if fields["type"] == "agent.purge_project":
            return {"status": "failed", "error": "agent daemon unreachable", "id": "j-agent"}
        return {"status": "succeeded"}

    monkeypatch.setattr(client, "create_job", create_job)
    response = web.post("/data/projects/little-widget/delete", data={"confirm": "little-widget"})
    assert "agent could not be reached" in unquote_message(response.headers["location"])
    owing = deletions.load(paths)["little-widget"]
    assert [s["pillar"] for s in owing["pending"]] == ["agent"] and owing["errors"][0]["job_id"] == "j-agent"


def test_a_stuck_deletion_needs_attention_with_its_errors_and_an_unstuck_one_does_not(paths, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [])
    monkeypatch.setattr(client, "policy_list", lambda *a, **k: [])
    monkeypatch.setattr(client, "list_jobs", lambda *a, **k: [])
    monkeypatch.setattr(client, "requirement_conflicts", lambda paths, **k: [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    monkeypatch.setattr(app_module, "_latest_policy_results", lambda paths, failing=False: {})
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    deletions.begin(paths, "gone", STEPS, [{"at": started.isoformat(), "pillar": "policy", "type": "x", "error": "policy unreachable", "job_id": None}], started)
    assert app_module.collect_attention(paths) == []
    deletions.begin(paths, "gone", STEPS, [{"at": started.isoformat(), "pillar": "policy", "type": "x", "error": "policy unreachable", "job_id": None}],
                    started - timedelta(hours=2))
    item, = app_module.collect_attention(paths)
    assert item["kind"] == "deletion" and "gone" in item["title"] and "policy unreachable" in item["detail"]


def test_the_retry_routes_and_the_page_show_what_is_owing(web, monkeypatch, paths):
    deletions.begin(paths, "gone", STEPS, [{"at": T0.isoformat(), "pillar": "policy", "type": "x", "error": "policy unreachable", "job_id": "j9"}],
                    datetime.now(timezone.utc) - timedelta(hours=3))
    text = web.get("/data").text
    assert "Deletions not finished" in text and "policy unreachable" in text and "Try again (once)" in text and "/jobs/j9" in text
    monkeypatch.setattr(client, "create_job", lambda paths_, **f: {"status": "failed", "error": "still down"})
    web.post("/data/deletions/gone/retry", data={"mode": "once"})
    assert deletions.stuck(deletions.load(paths)["gone"], datetime.now(timezone.utc))
    web.post("/data/deletions/gone/retry", data={"mode": "again"})
    assert not deletions.stuck(deletions.load(paths)["gone"], datetime.now(timezone.utc))
    monkeypatch.setattr(client, "create_job", lambda paths_, **f: {"status": "succeeded"})
    web.post("/data/deletions/gone/retry", data={"mode": "again"})
    assert deletions.load(paths) == {}


def test_the_dashboard_retries_owed_steps_on_its_own(paths, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(deletions, "RETRY_SECONDS", 0.05)
    monkeypatch.setattr(client, "create_job", lambda paths_, **f: {"status": "succeeded"})
    monkeypatch.setattr(client, "running_job_ids", lambda paths_, project: [])
    monkeypatch.setattr(client, "purge_orchestration", lambda paths_, project: {})
    deletions.begin(paths, "gone", STEPS, [], T0)
    with TestClient(app_module.build_app(paths)):  # the lifespan starts the background loops
        deadline = time.monotonic() + 5
        while deletions.load(paths) and time.monotonic() < deadline:
            time.sleep(0.05)
    assert deletions.load(paths) == {}
