"""A project's backlog in Registry & Portfolio: the entries, their four fields, deletion that keeps
nothing, and the two Jobs that have Claude refine an entry and write its task prompt."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from registry import agent_client, backlog, db
from registry.app import build_app
from registry.models import Project, ProjectStatus


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    now = datetime.now(timezone.utc)
    for name in ("little-meals", "little-home"):
        db.insert_project(conn=connection, project=Project(name=name, path=f"/tmp/{name}", status=ProjectStatus.ACTIVE, created_at=now, updated_at=now))
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths):
    return TestClient(build_app(conn, paths))


@pytest.fixture
def claude(monkeypatch):
    """Claude's answers, in order, the prompts it was given and whether each read the project's checkout."""
    calls = {"prompts": [], "answers": [], "read_checkout": []}

    def query(paths, prompt, *, job_id, project_id=None, ref=None, read_checkout=False, timeout=0):
        calls["prompts"].append((prompt, job_id, project_id))
        calls["read_checkout"].append(read_checkout)
        return calls["answers"].pop(0)

    monkeypatch.setattr(agent_client, "query", query)
    return calls


def _add(client, text="Weekly meal plan export\nExport the plan as a PDF.", project="little-meals"):
    return client.post("/v1/backlog", json={"project_name": project, "primary_entry": text})


# --- REQ-000000369: Each project has a backlog ---


def test_an_entry_is_added_listed_read_and_edited_per_project(client):
    entry = _add(client).json()
    assert entry["project_name"] == "little-meals" and entry["primary_entry"].startswith("Weekly meal plan export")
    assert entry["refinement_notes"] == entry["refined_entry"] == entry["task_prompt"] == ""
    assert entry["title"] == "Weekly meal plan export"
    _add(client, "Shared shopping list", project="little-home")

    assert [e["id"] for e in client.get("/v1/backlog", params={"project_name": "little-meals"}).json()] == [entry["id"]]
    assert len(client.get("/v1/backlog").json()) == 2
    edited = client.patch(f"/v1/backlog/{entry['id']}", json={"refinement_notes": "PDF and CSV"}).json()
    assert edited["refinement_notes"] == "PDF and CSV" and edited["notes_updated_at"]
    edited = client.patch(f"/v1/backlog/{entry['id']}", json={"primary_entry": "Export the weekly plan"}).json()
    assert client.get(f"/v1/backlog/{entry['id']}").json()["primary_entry"] == "Export the weekly plan" == edited["primary_entry"]


def test_an_entry_needs_a_registered_project_and_some_text(client):
    assert _add(client, project="nobody").status_code == 404
    assert _add(client, text="   ").status_code == 422
    entry = _add(client).json()
    assert client.patch(f"/v1/backlog/{entry['id']}", json={"primary_entry": " "}).status_code == 422
    assert client.patch("/v1/backlog/missing", json={"refinement_notes": "x"}).status_code == 404


def test_a_deleted_entry_is_gone_with_everything_it_held(client, conn, tmp_path):
    entry = _add(client, "A very particular secret wish").json()
    client.patch(f"/v1/backlog/{entry['id']}", json={"refinement_notes": "notes that must go too"})
    answer = client.delete(f"/v1/backlog/{entry['id']}").json()
    assert answer["deleted"] is True and answer["id"] == entry["id"]
    assert client.get(f"/v1/backlog/{entry['id']}").status_code == 404
    assert client.delete(f"/v1/backlog/{entry['id']}").status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM backlog_entries").fetchone()[0] == 0
    # Nothing of it is left in the database's files either (secure delete, the log folded back).
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("registry.db*"))
    assert b"particular secret wish" not in raw and b"notes that must go too" not in raw


def test_removing_a_project_removes_its_backlog(client, conn):
    _add(client)
    _add(client, project="little-home")
    db.delete_project(conn, "little-meals")
    assert [e["project_name"] for e in db.list_backlog_entries(conn)] == ["little-home"]


def test_the_version_changes_with_every_change_of_the_backlog(client):
    seen = {client.get("/v1/backlog/version").json()["version"]}
    entry = _add(client).json()
    seen.add(client.get("/v1/backlog/version").json()["version"])
    client.patch(f"/v1/backlog/{entry['id']}", json={"refinement_notes": "n"})
    seen.add(client.get("/v1/backlog/version").json()["version"])
    client.delete(f"/v1/backlog/{entry['id']}")
    seen.add(client.get("/v1/backlog/version").json()["version"])
    assert len(seen) == 4


def test_a_cards_title_is_the_refined_heading_else_the_first_line_written():
    assert backlog.entry_title({"primary_entry": "\n\n## Export the plan\nmore"}) == "Export the plan"
    assert backlog.entry_title({"primary_entry": "first", "refined_entry": "Intro\n# Weekly PDF export\n..."}) == "Weekly PDF export"
    assert backlog.entry_title({"primary_entry": "x" * 200}).endswith("…")
    assert backlog.entry_title({"primary_entry": ""}) == "(empty entry)"


# --- REQ-000000370: Refine entry ---


def test_refining_has_claude_write_from_the_primary_entry_the_refined_entry_and_the_notes(client, claude):
    entry = _add(client).json()
    client.patch(f"/v1/backlog/{entry['id']}", json={"refinement_notes": "Also CSV."})
    claude["answers"] = ["# Weekly plan export\n\nFirst refinement", "# Weekly plan export\n\nSecond refinement"]

    done = client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j1", "params": {"entry_id": entry["id"]}}).json()
    assert done["outcome"] == "succeeded"
    prompt, job_id, project = claude["prompts"][0]
    assert (job_id, project) == ("j1", "little-meals")
    assert "Export the plan as a PDF." in prompt and "Also CSV." in prompt and "(none yet)" in prompt
    assert client.get(f"/v1/backlog/{entry['id']}").json()["refined_entry"] == "# Weekly plan export\n\nFirst refinement"

    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j2", "params": {"entry_id": entry["id"]}})
    assert "First refinement" in claude["prompts"][1][0]  # the current refined entry is part of the next refinement
    stored = client.get(f"/v1/backlog/{entry['id']}").json()
    assert stored["refined_entry"].endswith("Second refinement") and stored["title"] == "Weekly plan export"


def test_claude_refines_reading_the_projects_checkout_but_takes_the_requirements_from_the_owner(client, claude):
    entry = _add(client).json()
    claude["answers"] = ["# Weekly plan export"]
    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j1", "params": {"entry_id": entry["id"]}})
    assert claude["read_checkout"] == [True]
    prompt = claude["prompts"][0][0]
    assert "checkout" in prompt and "Do not invent requirements" in prompt


def test_an_entry_deleted_while_claude_refines_it_keeps_nothing(client, claude, monkeypatch):
    entry = _add(client).json()

    def query_then_delete(paths, prompt, *, job_id, project_id=None, ref=None, read_checkout=False, timeout=0):
        client.delete(f"/v1/backlog/{entry['id']}")
        return "a refined entry nobody will see"

    monkeypatch.setattr(agent_client, "query", query_then_delete)
    done = client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j1", "params": {"entry_id": entry["id"]}}).json()
    assert done["outcome"] == "failed" and "deleted while Claude worked" in done["error"]
    assert client.get("/v1/backlog").json() == []


def test_refining_a_missing_entry_or_without_claude_fails_cleanly(client, monkeypatch):
    assert client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j1", "params": {"entry_id": "nope"}}).json()["outcome"] == "failed"
    entry = _add(client).json()

    def down(*args, **kwargs):
        raise agent_client.AgentClientError("the Agentic Development pillar is unavailable")

    monkeypatch.setattr(agent_client, "query", down)
    done = client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j2", "params": {"entry_id": entry["id"]}}).json()
    assert done == {"outcome": "failed", "error": "the Agentic Development pillar is unavailable"}
    assert client.get(f"/v1/backlog/{entry['id']}").json()["refined_entry"] == ""


# --- REQ-000000371: Generate task prompt ---


def test_the_task_prompt_comes_from_the_refined_entry_and_says_nothing_about_the_backlog(client, claude):
    entry = _add(client, "Primary words that must not reach the prompt").json()
    client.patch(f"/v1/backlog/{entry['id']}", json={"refinement_notes": "notes that must not reach it either"})
    refusal = client.post("/v1/jobs/registry.backlog_task_prompt", json={"job_id": "j0", "params": {"entry_id": entry["id"]}}).json()
    assert refusal["outcome"] == "failed" and "refine it first" in refusal["error"]

    claude["answers"] = ["# Export\n\nThe refined entry", "Implement the weekly export.", "Implement it differently."]
    client.post("/v1/jobs/registry.backlog_refine", json={"job_id": "j1", "params": {"entry_id": entry["id"]}})
    done = client.post("/v1/jobs/registry.backlog_task_prompt", json={"job_id": "j2", "params": {"entry_id": entry["id"]}}).json()
    assert done["outcome"] == "succeeded"

    prompt = claude["prompts"][1][0]
    assert "The refined entry" in prompt and "Primary words" not in prompt and "notes that must not" not in prompt
    assert "checkout" in prompt and claude["read_checkout"] == [True, True]  # read for the files and tests to name
    stored = client.get(f"/v1/backlog/{entry['id']}").json()["task_prompt"]
    assert stored == "Implement the weekly export."  # the task never closes the entry (REQ-000000524)
    assert "backlog delete" not in stored and entry["id"] not in stored

    client.post("/v1/jobs/registry.backlog_task_prompt", json={"job_id": "j3", "params": {"entry_id": entry["id"]}})
    replaced = client.get(f"/v1/backlog/{entry['id']}").json()["task_prompt"]
    assert replaced == "Implement it differently."


def test_lws_project_backlog_commands_act_on_the_named_projects_backlog(monkeypatch, capsys):
    import json as json_module

    from cli import client as cli_client
    from cli import main

    entries = {"e1": {"id": "e1", "project_name": "little-meals", "title": "Export", "primary_entry": "Export", "refinement_notes": "",
                      "refined_entry": "", "task_prompt": "", "updated_at": "t"}}
    calls = []

    def fake_backlog(paths, method, path="", *, params=None, json=None, timeout=10.0):
        calls.append((method, path, params, json))
        if method == "GET" and path:
            if path[1:] not in entries:
                raise cli_client.DaemonUnavailable("the backlog refused it: no such backlog entry")
            return entries[path[1:]]
        if method == "GET":
            return [e for e in entries.values() if e["project_name"] == params["project_name"]]
        if method == "POST":
            return {**entries["e1"], "primary_entry": json["primary_entry"]}
        return {"deleted": True, "id": path[1:]}

    monkeypatch.setattr(cli_client, "backlog", fake_backlog)
    assert main.main(["project", "backlog", "list", "--project-id", "little-meals"]) == 0
    assert json_module.loads(capsys.readouterr().out)[0]["id"] == "e1"
    assert main.main(["project", "backlog", "add", "--project-id", "little-meals", "Export it"]) == 0
    assert calls[-1] == ("POST", "", None, {"project_name": "little-meals", "primary_entry": "Export it"})
    assert main.main(["project", "backlog", "delete", "--project-id", "little-home", "e1"]) == 1  # not that project's entry
    assert not any(c[0] == "DELETE" for c in calls)
    assert main.main(["project", "backlog", "delete", "--project-id", "little-meals", "e1"]) == 0
    assert calls[-1][:2] == ("DELETE", "/e1")
    assert main.main(["project", "backlog", "show", "--project-id", "little-meals", "nope"]) == 1


# --- REQ-000000397: A query can read a project's checkout and change nothing ---


def test_the_backlog_asks_for_a_query_that_reads_the_checkout_and_waits_long_enough_for_it(paths, monkeypatch):
    import httpx

    from agent.query import CHECKOUT_QUERY_TIMEOUT_SECONDS
    from dashboard.app import BACKLOG_ACTIVITY_STALE
    from orchestration.dispatch import dispatch_timeout_for

    sent = {}

    class FakeClient:
        def __init__(self, socket_path, timeout):
            sent["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, json):
            sent.update(json)
            return httpx.Response(200, json={"outcome": "succeeded", "result": {"text": "ok"}}, request=httpx.Request("POST", "http://x" + path))

    monkeypatch.setattr(agent_client, "resolve_pillar_socket", lambda *a, **k: "/x")
    monkeypatch.setattr(agent_client, "client_for_socket", FakeClient)
    assert agent_client.query(paths, "Refine", job_id="j1", project_id="little-meals", read_checkout=True) == "ok"
    assert sent["params"]["read_checkout"] is True and sent["project_id"] == "little-meals"
    # Each waits longer than what it waits for: Orchestration for the Job, the Job for the agent, the agent for
    # Claude; and the Dashboard calls Claude's work on the entry unfinished only once Orchestration gave up on it.
    assert CHECKOUT_QUERY_TIMEOUT_SECONDS < sent["timeout"] < dispatch_timeout_for("registry.backlog_refine")
    assert dispatch_timeout_for("registry.backlog_task_prompt") == dispatch_timeout_for("registry.backlog_refine")
    assert dispatch_timeout_for("registry.backlog_refine") < BACKLOG_ACTIVITY_STALE.total_seconds()
    agent_client.query(paths, "A plain query", job_id="j2")
    assert "read_checkout" not in sent["params"] and sent["timeout"] == agent_client.QUERY_TIMEOUT_SECONDS


# --- REQ-000000524: Backlog entries have a status and a priority the owner sets ---


def test_entries_start_open_and_normal_and_the_owner_sets_both(client):
    entry = _add(client).json()
    assert (entry["status"], entry["priority"]) == ("open", "normal")
    changed = client.patch(f"/v1/backlog/{entry['id']}", json={"status": "done", "priority": "high"}).json()
    assert (changed["status"], changed["priority"]) == ("done", "high")
    assert client.patch(f"/v1/backlog/{entry['id']}", json={"status": "closed"}).status_code == 422
    assert client.patch(f"/v1/backlog/{entry['id']}", json={"priority": "urgent"}).status_code == 422
    assert client.patch("/v1/backlog/nope", json={"status": "done"}).status_code == 404


def test_the_list_filters_by_status_and_puts_the_highest_priority_first(client):
    low, normal, high, done = (_add(client, text).json() for text in ("low", "normal", "high", "done"))
    client.patch(f"/v1/backlog/{low['id']}", json={"priority": "low"})
    client.patch(f"/v1/backlog/{high['id']}", json={"priority": "high"})
    client.patch(f"/v1/backlog/{done['id']}", json={"status": "done", "priority": "high"})
    open_ids = [e["id"] for e in client.get("/v1/backlog", params={"project_name": "little-meals", "status": "open"}).json()]
    assert open_ids == [high["id"], normal["id"], low["id"]]
    assert [e["id"] for e in client.get("/v1/backlog", params={"status": "done"}).json()] == [done["id"]]
    assert len(client.get("/v1/backlog").json()) == 4


def test_an_entry_from_before_the_status_is_open_and_normal(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE backlog_entries (id TEXT PRIMARY KEY, project_name TEXT NOT NULL, primary_entry TEXT NOT NULL,
        refinement_notes TEXT NOT NULL DEFAULT '', refined_entry TEXT NOT NULL DEFAULT '', task_prompt TEXT NOT NULL DEFAULT '',
        primary_updated_at TEXT NOT NULL, notes_updated_at TEXT, refined_at TEXT, prompt_at TEXT, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    old.execute("INSERT INTO backlog_entries (id, project_name, primary_entry, primary_updated_at, created_at, updated_at) VALUES ('e', 'p', 'x', 't', 't', 't')")
    old.commit()
    old.close()
    migrated = db.connect(path)
    entry = db.get_backlog_entry(migrated, "e")
    migrated.close()
    assert (entry["status"], entry["priority"], entry["task_job"]) == ("open", "normal", None)


def test_a_launched_task_is_remembered_and_leaves_the_entry_open(client):
    entry = _add(client).json()
    after = client.post(f"/v1/backlog/{entry['id']}/task", json={"job_id": "job-7"}).json()
    assert after["task_job"] == "job-7" and after["status"] == "open"
    assert client.post("/v1/backlog/nope/task", json={"job_id": "j"}).status_code == 404


def test_lws_updates_an_entrys_status_and_priority(monkeypatch, capsys):
    import json as json_module

    from cli import client as cli_client
    from cli import main
    entry = {"id": "e1", "project_name": "little-meals", "title": "Export", "status": "open", "priority": "normal",
             "refined_entry": "", "task_prompt": "", "updated_at": "t"}
    calls = []

    def fake_backlog(paths, method, path="", *, params=None, json=None, timeout=10.0):
        calls.append((method, path, params, json))
        if method == "PATCH":
            return {**entry, **json}
        return entry if path else [entry]

    monkeypatch.setattr(cli_client, "backlog", fake_backlog)
    assert main.main(["project", "backlog", "update", "--project-id", "little-meals", "e1", "--status", "done", "--priority", "high"]) == 0
    assert calls[-1] == ("PATCH", "/e1", None, {"status": "done", "priority": "high"})
    assert json_module.loads(capsys.readouterr().out)["status"] == "done"
    assert main.main(["project", "backlog", "list", "--project-id", "little-meals", "--status", "done"]) == 0
    assert calls[-1][2] == {"project_name": "little-meals", "status": "done"}
    assert main.main(["project", "backlog", "update", "--project-id", "little-meals", "e1"]) == 1  # nothing to set



def _dashboard_entry(**overrides):
    entry = {"id": "e1", "project_name": "little-meals", "title": "Export", "primary_entry": "Export", "refinement_notes": "",
             "refined_entry": "", "task_prompt": "", "created_at": "2026-10-05T06:00:00+00:00", "updated_at": "2026-10-05T06:00:00+00:00",
             "status": "open", "priority": "normal", "task_job": None}
    entry.update(overrides)
    return entry


def test_the_dashboard_filters_by_status_sorts_by_priority_and_sets_both(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client as dashboard_client

    entries = [_dashboard_entry(id="a", title="Low one", priority="low", updated_at="2026-10-07T00:00:00+00:00"),
               _dashboard_entry(id="b", title="High one", priority="high", updated_at="2026-10-01T00:00:00+00:00")]
    asked = []
    monkeypatch.setattr(dashboard_client, "backlog_list", lambda paths, project=None, status=None: asked.append(status) or [dict(e) for e in entries])
    monkeypatch.setattr(dashboard_client, "backlog_get", lambda paths, entry_id: _dashboard_entry())
    edits = []
    monkeypatch.setattr(dashboard_client, "backlog_edit", lambda paths, entry_id, **fields: edits.append((entry_id, fields)) or {})
    test_client = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))

    page = test_client.get("/backlog/little-meals").text
    assert asked[-1] == "open" and page.index("High one") < page.index("Low one") and "high priority" in page
    test_client.get("/backlog/little-meals?status=all")
    assert asked[-1] is None
    test_client.get("/backlog/little-meals?status=done")
    assert asked[-1] == "done"

    response = test_client.post("/backlog/little-meals/e1/state", data={"status": "dropped", "priority": "low"}, follow_redirects=False)
    assert response.status_code == 303 and edits == [("e1", {"status": "dropped", "priority": "low"})]


def test_the_entry_offers_mark_done_once_its_launched_task_succeeded(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client as dashboard_client

    entry = _dashboard_entry(task_job="job-9")
    run = {"id": "r1", "project_id": "little-meals", "title": "Export", "status": "awaiting_finish", "completion_status": "done"}
    monkeypatch.setattr(dashboard_client, "backlog_get", lambda paths, entry_id: dict(entry))
    monkeypatch.setattr(dashboard_client, "agent_run_for_job", lambda paths, job_id: dict(run) if job_id == "job-9" else None)
    test_client = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))

    assert "Mark done" in test_client.get("/backlog/little-meals/e1").text
    run["completion_status"] = "partial"
    assert "Mark done" not in test_client.get("/backlog/little-meals/e1").text
    run["completion_status"], entry["status"] = "done", "done"
    assert "Mark done" not in test_client.get("/backlog/little-meals/e1").text


def test_launching_a_task_from_an_entry_records_it_on_the_entry(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client as dashboard_client

    monkeypatch.setattr(dashboard_client, "create_job", lambda paths, **f: {"id": "job-3", "status": "running"})
    launched = []
    monkeypatch.setattr(dashboard_client, "backlog_task_launched", lambda paths, entry_id, job_id: launched.append((entry_id, job_id)))
    test_client = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    test_client.post("/tasks/new", data={"project": "little-meals", "title": "Export", "backlog_id": "e1"}, follow_redirects=False)
    assert launched == [("e1", "job-3")]
