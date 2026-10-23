"""dashboard.app's Docs pages (documentation review), with `dashboard.client`
monkeypatched — what the app does with the agent's and Orchestration's answers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client
from orchestration_jobs_fake import patch_jobs


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "little-widget", "status": "active"}])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: [])
    monkeypatch.setattr(client, "policy_list", lambda paths, topic=None, **kw: [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    return TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))


OVERVIEW = {"project_id": "little-widget", "branch": "dev", "branches": ["dev", "feature/x"], "dirty": False,
            "files": [{"path": "README.md", "open": 2, "done": 1}, {"path": "docs/guide.md", "open": 0, "done": 0}]}


def _stub(monkeypatch, *, jobs=(), tasks=(), overview=OVERVIEW):
    monkeypatch.setattr(client, "docs_overview", lambda paths, project: overview)
    patch_jobs(monkeypatch, client, list(jobs))
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: list(tasks))
    monkeypatch.setattr(client, "doc_updates", lambda paths, project: [])


def _message(response):
    return unquote(response.headers["location"].split("message=", 1)[1])


def test_docs_index_lists_projects(test_client):
    assert 'href="/docs/little-widget"' in test_client.get("/docs").text


def test_project_page_lists_docs_branches_and_the_send_form(test_client, monkeypatch):
    _stub(monkeypatch)
    text = test_client.get("/docs/little-widget").text
    assert "/docs/little-widget/view?path=README.md" in text and "/docs/little-widget?dir=docs" in text
    assert "2 open" in text and "1 done" in text
    assert '<option value="feature/x"' in text and '<option value="dev" selected' in text
    assert 'action="/docs/little-widget/update"' in text and "Remote Control" in text


def test_a_queued_update_for_the_branch_hides_the_send_form_and_offers_cancel(test_client, monkeypatch):
    job = {"id": "j1", "type": "agent.update_docs", "project_id": "little-widget", "status": "queued", "params": {"branch": "dev"}}
    _stub(monkeypatch, jobs=[job])
    text = test_client.get("/docs/little-widget").text
    assert 'action="/docs/little-widget/update"' not in text
    assert "/docs/little-widget/update/jobs/j1/stop" in text and "Cancel" in text


def test_sending_an_update_starts_one_job_for_the_checked_out_branch(test_client, monkeypatch):
    _stub(monkeypatch)
    sent = []
    monkeypatch.setattr(client, "create_job", lambda paths, **job: sent.append(job) or {"status": "running"})
    response = test_client.post("/docs/little-widget/update", data={"remote_control": "on", "when": "immediate"}, follow_redirects=False)
    assert "started for dev" in _message(response)
    assert sent[0]["type"] == "agent.update_docs" and sent[0]["params"] == {"branch": "dev", "remote_control": True}
    assert sent[0]["queue_key"] == "little-widget:docs:dev"


def test_a_second_update_for_the_same_branch_is_refused(test_client, monkeypatch):
    task = {"id": "t1", "schedule": {"kind": "once", "at": "2099-01-01T10:00:00"},
            "job_template": {"type": "agent.update_docs", "project_id": "little-widget", "params": {"branch": "dev"}}}
    _stub(monkeypatch, tasks=[task])
    monkeypatch.setattr(client, "create_job", lambda paths, **job: pytest.fail("must not start a second job"))
    response = test_client.post("/docs/little-widget/update", data={"when": "immediate"}, follow_redirects=False)
    assert "already queued or running" in _message(response)


def test_a_later_update_is_scheduled_once(test_client, monkeypatch):
    _stub(monkeypatch)
    made = []
    monkeypatch.setattr(client, "create_scheduled_task", lambda paths, **kw: made.append(kw) or {"id": "t"})
    at = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    response = test_client.post("/docs/little-widget/update", data={"when": "once", "at": at}, follow_redirects=False)
    assert "scheduled" in _message(response)
    assert made[0]["schedule"]["kind"] == "once" and made[0]["job_template"]["params"]["remote_control"] is False


def test_editing_a_scheduled_update_changes_its_time_and_remote_control(test_client, monkeypatch):
    template = {"type": "agent.update_docs", "project_id": "little-widget", "params": {"branch": "dev", "remote_control": False}}
    monkeypatch.setattr(client, "get_scheduled_task", lambda paths, tid: {"id": tid, "job_template": template})
    edits = []
    monkeypatch.setattr(client, "edit_scheduled_task", lambda paths, tid, **kw: edits.append(kw) or {})
    at = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    test_client.post("/docs/little-widget/update/scheduled/t1/edit", data={"at": at, "remote_control": "on"}, follow_redirects=False)
    assert edits[0]["job_template"]["params"] == {"branch": "dev", "remote_control": True}
    assert edits[0]["schedule"]["at"].startswith(at)


def test_stopping_reports_orchestrations_answer(test_client, monkeypatch):
    monkeypatch.setattr(client, "stop_job", lambda paths, jid: {"stopped": False, "reason": "job is succeeded, not running"})
    response = test_client.post("/docs/little-widget/update/jobs/j1/stop", follow_redirects=False)
    assert "not running" in _message(response)


def test_checkout_runs_a_job_and_returns_to_the_page_it_came_from(test_client, monkeypatch):
    sent = []
    monkeypatch.setattr(client, "create_job", lambda paths, **job: sent.append(job) or {"status": "succeeded"})
    back = "/docs/little-widget/view?path=README.md"
    response = test_client.post("/docs/little-widget/checkout", data={"branch": "feature/x", "back": back}, follow_redirects=False)
    assert response.headers["location"].startswith(back + "&message=")
    assert sent[0]["type"] == "agent.docs_checkout" and sent[0]["params"] == {"branch": "feature/x"}
    elsewhere = test_client.post("/docs/little-widget/checkout", data={"branch": "dev", "back": "https://evil.example"}, follow_redirects=False)
    assert elsewhere.headers["location"].startswith("/docs/little-widget?")


def test_viewer_renders_the_document_and_its_comments(test_client, monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(client, "doc_file", lambda paths, project, path: {"path": path, "branch": "dev", "source": "# Hi\n\nText.\n"})
    comment = {"id": "c1", "branch": "dev", "path": "README.md", "body": "**why?**", "status": "open", "job_id": None,
               "anchor": {"start_line": 3, "end_line": 3, "exact": "Text."}}
    monkeypatch.setattr(client, "review_comments", lambda paths, project, branch=None, path=None: [comment])
    text = test_client.get("/docs/little-widget/view", params={"path": "README.md"}).text
    assert '<h1 data-ls="1" data-le="1" id="hi">Hi</h1>' in text
    assert "/static/review.js" in text and 'data-branch="dev"' in text
    data = json.loads(re.search(r'<script type="application/json" id="rv-data">(.*?)</script>', text, re.S).group(1))
    assert data["comments"][0]["html"].strip() == "<p><strong>why?</strong></p>"
    assert data["files"] == ["README.md", "docs/guide.md"]


def test_viewer_shows_a_refusal_as_a_page_not_a_500(test_client, monkeypatch):
    def refuse(paths, project, path):
        raise client.Refused(404, "no such file: 'x.md'")
    monkeypatch.setattr(client, "doc_file", refuse)
    response = test_client.get("/docs/little-widget/view", params={"path": "x.md"})
    assert response.status_code == 200 and "no such file" in response.text


def test_saving_a_comment_proxies_and_renders_it(test_client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "put_review_comment", lambda paths, project, cid, fields: seen.append(fields) or {**fields, "id": cid})
    response = test_client.put("/docs/little-widget/comments/c1", json={"body": "- a\n- b", "branch": "dev", "path": "README.md"})
    assert response.status_code == 200 and "<li>a</li>" in response.json()["html"]
    assert seen[0]["body"] == "- a\n- b"


def test_a_locked_comment_passes_the_refusal_through(test_client, monkeypatch):
    def locked(paths, project, cid, fields):
        raise client.Refused(409, "an update-doc job is working on this comment")
    monkeypatch.setattr(client, "put_review_comment", locked)
    response = test_client.put("/docs/little-widget/comments/c1", json={"body": "x"})
    assert response.status_code == 409 and "update-doc job" in response.json()["detail"]


def test_new_job_page_does_not_list_scheduled_update_doc_jobs(test_client, monkeypatch):
    task = {"id": "t-docs", "name": "Update docs: little-widget (dev)", "schedule": {"kind": "once", "at": "2099-01-01T10:00:00"},
            "job_template": {"type": "agent.update_docs", "project_id": "little-widget", "params": {"branch": "dev"}}}
    monkeypatch.setattr(client, "scheduled_tasks", lambda paths: [task])
    assert "t-docs" not in test_client.get("/jobs/new").text


def test_static_cache_buster_follows_the_newest_static_file(tmp_path):
    import os
    from pathlib import Path
    import importlib.resources
    static = Path(str(importlib.resources.files("dashboard") / "static"))
    app = app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"))
    newest = int(max(os.path.getmtime(p) for p in static.iterdir() if p.is_file()))
    assert TestClient(app).get("/docs").status_code == 200
    assert int(os.path.getmtime(static / "review.js")) <= newest
    assert f"?v={newest}" in TestClient(app).get("/docs").text


# --- REQ-000000431: The Docs page of a project is organised as its folders ---

TREE = {**OVERVIEW, "files": [
    {"path": "README.md", "open": 2, "done": 1, "certified": 0},
    {"path": "docs/guide.md", "open": 1, "done": 0, "certified": 0},
    {"path": "docs/pillars/agent.md", "open": 0, "done": 3, "certified": 1},
    {"path": "docs/pillars/dashboard.md", "open": 4, "done": 0, "certified": 0},
]}


def test_the_root_shows_folders_first_as_cards_with_the_totals_beneath_them(test_client, monkeypatch):
    _stub(monkeypatch, overview=TREE)
    text = test_client.get("/docs/little-widget").text
    assert text.index('class="dg-card dg-folder"') < text.index('class="dg-card dg-file"')
    assert "/docs/little-widget?dir=docs" in text and "3 documents" in text
    assert "5 open" in text and "3 done" in text and "1 certified" in text
    assert "docs/guide.md" not in text and "/docs/little-widget/view?path=README.md" in text
    assert 'action="/docs/little-widget/checkout"' in text  # the branch and update controls stay on the root


def test_a_folder_page_lists_its_own_entries_with_a_breadcrumb_back(test_client, monkeypatch):
    _stub(monkeypatch, overview=TREE)
    text = test_client.get("/docs/little-widget", params={"dir": "docs"}).text
    assert "/docs/little-widget/view?path=docs/guide.md" in text and "/docs/little-widget?dir=docs/pillars" in text
    assert "README.md" not in text and 'action="/docs/little-widget/update"' not in text
    assert 'href="/docs/little-widget">little-widget</a>' in text and "<strong>docs</strong>" in text
    deeper = test_client.get("/docs/little-widget", params={"dir": "docs/pillars"}).text
    assert "/docs/little-widget/view?path=docs/pillars/agent.md" in deeper and "/docs/little-widget?dir=docs\"" in deeper
    assert "<strong>pillars</strong>" in deeper


def test_an_unknown_folder_says_it_is_empty_and_dots_cannot_leave_the_project(test_client, monkeypatch):
    _stub(monkeypatch, overview=TREE)
    assert "This folder has no documents" in test_client.get("/docs/little-widget", params={"dir": "nothing"}).text
    escaped = test_client.get("/docs/little-widget", params={"dir": "../docs"}).text
    assert "/docs/little-widget/view?path=docs/guide.md" in escaped  # `..` is dropped, not followed


def test_folder_view_builds_entries_and_breadcrumbs():
    from dashboard import docs_tree

    view = docs_tree.folder_view(TREE["files"], "docs/")
    assert [f["name"] for f in view["files"]] == ["guide.md"]
    assert view["folders"] == [{"name": "pillars", "path": "docs/pillars", "files": 2, "open": 4, "done": 3, "certified": 1}]
    assert view["crumbs"] == [{"name": "docs", "path": "docs"}]
    assert docs_tree.folder_view([], "")["exists"] is True and docs_tree.folder_view([], "x")["exists"] is False
