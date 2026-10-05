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
    """Claude's answers, in order, and the prompts it was given."""
    calls = {"prompts": [], "answers": []}

    def query(paths, prompt, *, job_id, project_id=None, ref=None, timeout=0):
        calls["prompts"].append((prompt, job_id, project_id))
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


def test_an_entry_deleted_while_claude_refines_it_keeps_nothing(client, claude, monkeypatch):
    entry = _add(client).json()

    def query_then_delete(paths, prompt, *, job_id, project_id=None, ref=None, timeout=0):
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


def test_the_task_prompt_comes_from_the_refined_entry_alone_and_ends_with_the_delete_instruction(client, claude):
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
    stored = client.get(f"/v1/backlog/{entry['id']}").json()["task_prompt"]
    assert stored.startswith("Implement the weekly export.")
    ending = stored.rsplit("## When you are done", 1)[1]
    assert f"lws project backlog delete --project-id little-meals {entry['id']}" in ending
    assert "workshop_project_backlog_delete" in ending and entry["id"] in ending

    client.post("/v1/jobs/registry.backlog_task_prompt", json={"job_id": "j3", "params": {"entry_id": entry["id"]}})
    replaced = client.get(f"/v1/backlog/{entry['id']}").json()["task_prompt"]
    assert replaced.startswith("Implement it differently.") and replaced.count("## When you are done") == 1


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
