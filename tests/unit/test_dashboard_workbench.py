"""The Dashboard pages added on 2026-10-04: the backlog, New task filled from a backlog entry or an
automation candidate, force stop, the links between a task and its job, the policy filters, the
schedule calendar, an incident's kind, and the pages that update themselves."""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client
from test_dashboard_app import _job, _policy, _stub_jobs, _usage_summary


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def test_client(paths):
    return TestClient(app_module.build_app(paths))


@pytest.fixture
def created(monkeypatch):
    """Jobs the Dashboard submits (also from its background threads), as they were asked for."""
    jobs = []

    def create_job(paths, **fields):
        jobs.append(fields)
        return {"id": f"job{len(jobs)}", "status": "queued"}

    monkeypatch.setattr(client, "create_job", create_job)
    monkeypatch.setattr(app_module, "_fire_and_forget", lambda paths, on_done=None, **job: jobs.append(job))
    return jobs


def _entry(**overrides):
    base = {"id": "e1", "project_name": "little-meals", "title": "Weekly export", "primary_entry": "Weekly export\nAs a PDF.",
            "refinement_notes": "", "refined_entry": "", "task_prompt": "", "refined_at": None, "prompt_at": None,
            "created_at": "2026-10-04T10:00:00+00:00", "updated_at": "2026-10-04T10:00:00+00:00"}
    base.update(overrides)
    return base


def _backlog(monkeypatch, entries):
    store = {e["id"]: dict(e) for e in entries}
    calls = []
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-meals", "status": "active"}, {"name": "little-home", "status": "active"}])
    monkeypatch.setattr(client, "backlog_list", lambda paths, project=None: [dict(e) for e in store.values() if project in (None, e["project_name"])])
    monkeypatch.setattr(client, "backlog_get", lambda paths, entry_id: dict(store[entry_id]) if entry_id in store else None)

    def add(paths, project, text):
        if not text.strip():
            raise client.Refused(422, "the entry is empty")
        store["e9"] = _entry(id="e9", project_name=project, primary_entry=text, title=text.splitlines()[0])
        calls.append(("add", project, text))
        return dict(store["e9"])

    def edit(paths, entry_id, **fields):
        store[entry_id].update(fields)
        calls.append(("edit", entry_id, fields))
        return dict(store[entry_id])

    def delete(paths, entry_id):
        del store[entry_id]
        calls.append(("delete", entry_id))
        return {"deleted": True, "id": entry_id}

    monkeypatch.setattr(client, "backlog_add", add)
    monkeypatch.setattr(client, "backlog_edit", edit)
    monkeypatch.setattr(client, "backlog_delete", delete)
    return store, calls


# --- REQ-000000372: The Backlog pages ---


def test_the_backlog_is_under_documentation_and_lists_each_projects_entries(test_client, monkeypatch):
    _backlog(monkeypatch, [_entry(), _entry(id="e2", project_name="little-home", title="Shared list")])
    _stub_jobs(monkeypatch, [])
    index = test_client.get("/backlog").text
    assert 'href="/backlog"' in index and ">Backlog<" in index  # the Documentation group's page
    assert 'href="/backlog/little-meals"' in index and "1 entry" in index
    page = test_client.get("/backlog/little-meals").text
    assert 'href="/backlog/little-meals/e1"' in page and "Weekly export" in page and "Shared list" not in page
    assert 'action="/backlog/little-meals/add"' in page and 'data-live="backlog"' in page


def test_an_entry_page_shows_its_four_fields_and_every_action(test_client, monkeypatch):
    _backlog(monkeypatch, [_entry(refinement_notes="Also CSV", refined_entry="# Weekly export\n\n- [ ] PDF", task_prompt="Do it.\n\n## When you are done")])
    _stub_jobs(monkeypatch, [])
    page = test_client.get("/backlog/little-meals/e1").text
    assert "Primary entry" in page and "As a PDF." in page and "Refinement notes" in page and "Also CSV" in page
    refined = page.split('class="markdown backlog-refined">', 1)[1].split("</div>", 1)[0]
    assert "Refined entry" in page and "Weekly export" in refined and "PDF" in refined
    assert 'id="backlog-prompt"' in page and 'class="copy-icon" data-copy="backlog-prompt"' in page
    assert 'formaction="/backlog/little-meals/e1/refine"' in page and 'action="/backlog/little-meals/e1/prompt"' in page
    assert 'href="/tasks/new?project=little-meals&amp;backlog=e1"' in page
    assert 'action="/backlog/little-meals/e1/delete"' in page and "data-confirm=" in page


def test_saving_adding_and_deleting_go_to_the_registry(test_client, monkeypatch):
    store, calls = _backlog(monkeypatch, [_entry()])
    _stub_jobs(monkeypatch, [])
    added = test_client.post("/backlog/little-meals/add", data={"text": "Dark mode\nfor the app"}, follow_redirects=False)
    assert added.headers["location"].startswith("/backlog/little-meals/e9")
    refused = unquote(test_client.post("/backlog/little-meals/add", data={"text": " "}, follow_redirects=False).headers["location"])
    assert "Not added: the entry is empty" in refused

    saved = test_client.post("/backlog/little-meals/e1/save", data={"primary_entry": "Weekly export\r\nAs a PDF.", "refinement_notes": "CSV too"},
                             follow_redirects=False)
    assert "Saved." in unquote(saved.headers["location"])
    assert ("edit", "e1", {"refinement_notes": "CSV too"}) in calls  # only what changed (line endings as stored)

    gone = test_client.post("/backlog/little-meals/e1/delete", follow_redirects=False)
    assert gone.headers["location"].startswith("/backlog/little-meals?") and "e1" not in store
    assert "no longer in the backlog" in test_client.get("/backlog/little-meals/e1").text


def test_refine_saves_what_was_typed_then_starts_the_job_and_the_prompt_needs_a_refined_entry(test_client, monkeypatch, created):
    store, calls = _backlog(monkeypatch, [_entry()])
    _stub_jobs(monkeypatch, [])
    refused = unquote(test_client.post("/backlog/little-meals/e1/prompt", follow_redirects=False).headers["location"])
    assert "Refine the entry first" in refused and created == []

    test_client.post("/backlog/little-meals/e1/refine", data={"primary_entry": store["e1"]["primary_entry"], "refinement_notes": "Add CSV"})
    assert store["e1"]["refinement_notes"] == "Add CSV"
    (job,) = created
    assert (job["type"], job["target_pillar"], job["project_id"], job["params"]) == ("registry.backlog_refine", "registry", "little-meals", {"entry_id": "e1"})

    store["e1"]["refined_entry"] = "# Weekly export"
    test_client.post("/backlog/little-meals/e1/prompt")
    assert created[-1]["type"] == "registry.backlog_task_prompt" and created[-1]["params"] == {"entry_id": "e1"}


def test_while_claude_works_on_an_entry_the_page_says_so_and_a_second_start_is_refused(test_client, monkeypatch, created):
    from datetime import datetime, timezone

    _backlog(monkeypatch, [_entry(refined_entry="# r", refining_since=datetime.now(timezone.utc).isoformat(), refining_job="j1",
                                  prompt_error="agent.query failed")])
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: None)
    page = test_client.get("/backlog/little-meals/e1").text
    assert "Claude is refining…" in page and "The last task prompt failed: agent.query failed" in page
    again = unquote(test_client.post("/backlog/little-meals/e1/refine", data={}, follow_redirects=False).headers["location"])
    assert "already working" in again and created == []


def test_the_activity_of_an_entry_is_kept_on_the_entry():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    fresh = {"refining_since": (now - timedelta(minutes=2)).isoformat(), "refining_job": "j1", "prompt_error": "over budget"}
    assert app_module.backlog_activity(fresh, now) == {"refine": "j1", "refine_error": None, "prompt": None, "prompt_error": "over budget"}
    stale = {"prompting_since": (now - timedelta(minutes=30)).isoformat(), "prompting_job": "j2"}
    assert app_module.backlog_activity(stale, now)["prompt"] is None
    assert app_module.backlog_activity(stale, now)["prompt_error"] == "the last task prompt did not finish"
    assert app_module.backlog_activity({}, now) == {"refine": None, "refine_error": None, "prompt": None, "prompt_error": None}


def test_launch_task_fills_new_task_with_the_task_prompt(test_client, monkeypatch):
    prompt = "Implement the weekly export as PDF and CSV.\n\n## When you are done\n\nDelete the entry e1 from the backlog."
    _backlog(monkeypatch, [_entry(refined_entry="# Weekly export\n\nThe refined entry, not the task.", task_prompt=prompt)])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    page = test_client.get("/tasks/new", params={"project": "little-meals", "backlog": "e1"}).text
    assert 'value="Weekly export"' in page and "Implement the weekly export as PDF and CSV." in page
    assert "Delete the entry e1 from the backlog." in page and "The refined entry, not the task." not in page
    assert 'href="/backlog/little-meals/e1"' in page and '<option value="little-meals" selected' in page
    # With no task prompt yet, its refined entry; not refined yet, its primary entry: each said so.
    _backlog(monkeypatch, [_entry(refined_entry="# Weekly export\n\nExport the plan as PDF and CSV.")])
    page = test_client.get("/tasks/new", params={"backlog": "e1"}).text
    assert "Export the plan as PDF and CSV." in page and "no task prompt yet" in page
    _backlog(monkeypatch, [_entry()])
    page = test_client.get("/tasks/new", params={"backlog": "e1"}).text
    assert "As a PDF." in page and "not refined yet" in page


# --- REQ-000000373: "Make it a task" opens New task ---


def test_make_it_a_task_fills_new_task_and_starting_it_marks_the_candidate(test_client, monkeypatch, created):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-workshop", "status": "active"}])
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    monkeypatch.setattr(client, "usage_candidate_task", lambda paths, cid: {
        "project_id": "little-workshop", "title": "Automate repeated Claude work", "intent": "task", "review_mode": "manual",
        "description": "Turn work that Claude sessions repeat by hand into code."} if cid == "c1" else None)
    started = []
    monkeypatch.setattr(client, "usage_candidate_task_started", lambda paths, cid, job_id: started.append((cid, job_id)))
    page = test_client.get("/tasks/new", params={"candidate": "c1"}).text
    assert 'value="Automate repeated Claude work"' in page and "Turn work that Claude sessions repeat" in page
    assert 'name="candidate_id" value="c1"' in page and '<option value="task" selected' in page

    test_client.post("/tasks/new", data={"project": "little-workshop", "title": "Automate", "description": "d", "candidate_id": "c1"})
    assert created[0]["type"] == "agent.run_task" and started == [("c1", "job1")]
    assert "no longer open" in test_client.get("/tasks/new", params={"candidate": "gone"}).text


# --- REQ-000000366: A job can be force-stopped ---


def test_a_running_job_page_offers_stop_and_explains_force_stop(test_client, monkeypatch):
    job = _job(1, type="agent.run_task", status="running", result=None)
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: {**job, "steps": []})
    monkeypatch.setattr(client, "agent_run_for_job", lambda paths, job_id: None)
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: None)
    page = test_client.get(f"/jobs/{job['id']}").text
    assert "Stop job" in page and 'name="force" value="1"' in page and "not undone" in page
    assert '<details class="force-stop">' in page  # closed until a stop is refused
    refused = test_client.get(f"/jobs/{job['id']}?refused=1").text
    assert '<details class="force-stop" open>' in refused and "It would not stop" in refused


def test_force_stop_asks_orchestration_to_force_and_a_refusal_offers_it(test_client, monkeypatch):
    calls = []

    def stop_job(paths, job_id, force=False):
        calls.append((job_id, force))
        return {"stopped": True, "reason": None, "forced": True} if force else {"stopped": False, "reason": "not stoppable"}

    monkeypatch.setattr(client, "stop_job", stop_job)
    refused = unquote(test_client.post("/jobs/j1/stop", data={"next": "/tasks/p/r1"}, follow_redirects=False).headers["location"])
    assert refused.startswith("/tasks/p/r1?refused=1&") and "not stoppable" in refused
    forced = unquote(test_client.post("/jobs/j1/stop", data={"force": "1", "next": "/tasks/p/r1"}, follow_redirects=False).headers["location"])
    assert forced.startswith("/tasks/p/r1?") and "force-stopped" in forced
    assert calls == [("j1", False), ("j1", True)]
    assert test_client.post("/jobs/j1/stop", data={"next": "//elsewhere"}, follow_redirects=False).headers["location"].startswith("/jobs/j1")


# --- REQ-000000377: A task and its job link to each other ---


def _run(**overrides):
    base = {"id": "r1", "job_id": "00000001aaaabbbbccccdddd", "project_id": "little-meals", "title": "Do it", "status": "running",
            "branch": None, "created_at": "2026-10-04T10:00:00+00:00", "updated_at": "2026-10-04T10:00:00+00:00"}
    base.update(overrides)
    return base


def test_the_task_page_links_to_its_job_and_the_job_page_to_its_task(test_client, monkeypatch):
    job = _job(1, type="agent.run_task", status="running", project_id="little-meals", result=None)
    monkeypatch.setattr(client, "agent_run", lambda paths, project, run_id: _run())
    monkeypatch.setattr(client, "get_job", lambda paths, job_id: {**job, "steps": []})
    monkeypatch.setattr(client, "agent_peek", lambda paths, job_id, **kw: None)
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [])
    task_page = test_client.get("/tasks/little-meals/r1").text
    assert f'href="/jobs/{job["id"]}"' in task_page and "Stop job" in task_page  # a running task can be stopped from it
    monkeypatch.setattr(client, "agent_run_for_job", lambda paths, job_id: _run())
    job_page = test_client.get(f"/jobs/{job['id']}").text
    assert 'href="/tasks/little-meals/r1"' in job_page and "Task: Do it" in job_page


def test_a_task_jobs_card_on_the_jobs_page_opens_its_task(test_client, monkeypatch):
    _stub_jobs(monkeypatch, [_job(1, type="agent.run_task", project_id="little-meals", result=None, status="running")])
    monkeypatch.setattr(client, "agent_runs", lambda paths, project: [_run()])
    page = test_client.get("/jobs").text
    assert 'href="/tasks/little-meals/r1">Open the task' in page


# --- REQ-000000376: Policies can be filtered ---


POLICIES = [
    _policy(id="p1", topic="commit-bumps-version", ears_text="Every commit shall raise VERSION.", gate_points=["commit"],
            projects=[{"project_id": "little-meals", "gating_enabled": True}]),
    _policy(id="p2", topic="branch-commit-dev-suffix", ears_text="A working branch shall carry -dev.N.", gate_points=["commit", "branch_finish"],
            projects=[{"project_id": "little-home", "gating_enabled": False}], enabled=False),
    _policy(id="p3", topic="no-committed-secrets", ears_text="No commit shall add a secret.", gate_points=["release"],
            projects=[{"project_id": "little-home", "gating_enabled": True}]),
]


@pytest.mark.parametrize("filters,expected", [
    ({}, {"p1", "p2", "p3"}),
    ({"q": "commit*"}, {"p1"}),                 # a glob over the whole name (or the whole description)
    ({"q": "*commit*"}, {"p1", "p2", "p3"}),
    ({"q": "commit"}, {"p1", "p2", "p3"}),       # no wildcard: anywhere, in the name or the description
    ({"q": "raise version"}, {"p1"}),            # found in the description only
    ({"q": "b?anch*"}, {"p2"}),
    ({"status": "disabled"}, {"p2"}),
    ({"status": "enabled"}, {"p1", "p3"}),
    ({"status": "failing"}, {"p3"}),
    ({"gates": ["branch_finish", "release"]}, {"p2", "p3"}),
    ({"projects": ["little-home"]}, {"p2", "p3"}),
    ({"gating": True}, {"p1", "p3"}),
    ({"q": "*commit*", "projects": ["little-home"], "gating": True}, {"p3"}),
])
def test_the_policy_filters_combine(filters, expected):
    rows = [{**p, "failing_count": 1 if p["id"] == "p3" else 0} for p in POLICIES]
    assert {p["id"] for p in rows if app_module.policy_matches(p, **filters)} == expected


def test_the_policies_page_applies_the_filters_in_its_address(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None, **kw: [dict(p) for p in POLICIES])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: [])
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-meals", "status": "active"}])
    page = test_client.get("/policies", params=[("q", "*commit*"), ("gate", "commit"), ("project", "little-home")]).text
    shown = {m.group(1): not m.group(2) for m in re.finditer(r'data-policy="(p\d)"[^>]*?( hidden)?>', page, re.S)}
    assert shown == {"p1": False, "p2": True, "p3": False}
    assert "1 of 3 policies" in page
    assert 'name="gate" value="commit" checked' in page and 'name="project" value="little-home" checked' in page
    assert 'value="little-meals"' in page  # every project can be chosen, governed or registered
    assert 'data-live="policies"' in page and 'id="policies-live" data-live-region' in page


def test_the_policies_page_filters_as_the_user_types_with_the_same_glob(test_client, monkeypatch):
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None, **kw: [dict(p) for p in POLICIES])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: [])
    page = test_client.get("/policies").text
    assert "function globRegex" in page and "history.replaceState" in page and "addEventListener('input', apply)" in page
