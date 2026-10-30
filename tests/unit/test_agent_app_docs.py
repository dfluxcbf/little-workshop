"""agent.app's documentation-review endpoints: docs, comments, checkout,
`agent.update_docs` and its stop hook."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import db
from agent.app import build_app, resume_doc_updates
from agent.doc_update import DocUpdateFlow
from common.paths import Paths
from common.shell import CommandResult


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class NoSession:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        return CommandResult(3, "inactive\n", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("# Title\n\nHello.\n", encoding="utf-8")
    (repo / "logo.png").write_bytes(b"\x89PNG fake")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["branch", "feature/x"], repo)
    monkeypatch.setattr("agent.app.resolve_project", lambda p, pid, **kw: {"name": pid, "path": str(repo), "repo_url": None})
    conn = db.connect(paths.pillar_db_path("agent"))
    flow = DocUpdateFlow(paths=paths, runner=NoSession())
    flow.launch = lambda *a, **kw: None  # the worker thread: covered in test_agent_doc_update.py
    return TestClient(build_app(paths, conn, doc_flow=flow)), repo, conn, flow, paths


def _put(client, cid=None, **fields):
    cid = cid or str(uuid.uuid4())
    body = {"branch": "dev", "path": "README.md", "body": "say more", "anchor": {"start_line": 3, "end_line": 3, "exact": "Hello."}}
    return client.put(f"/v1/reviews/proj/comments/{cid}", json={**body, **fields})


def test_docs_overview_lists_files_branches_and_comment_counts(env):
    client, *_ = env
    _put(client)
    body = client.get("/v1/docs/proj").json()
    assert body["branch"] == "dev" and body["branches"] == ["dev", "feature/x"] and body["dirty"] is False
    assert body["files"] == [{"path": "README.md", "open": 1, "done": 0, "certified": 0}]


def test_certified_comments_are_counted_apart_from_open_ones(env):
    client, *_ = env
    _put(client)
    _put(client, certified=True)
    assert client.get("/v1/docs/proj").json()["files"][0] == {"path": "README.md", "open": 1, "done": 0, "certified": 1}


def test_a_doc_and_an_image_are_read_but_nothing_outside_the_project(env):
    client, *_ = env
    assert client.get("/v1/docs/proj/file", params={"path": "README.md"}).json()["source"].startswith("# Title")
    assert client.get("/v1/docs/proj/file", params={"path": "../x.md"}).status_code == 400
    assert client.get("/v1/docs/proj/file", params={"path": "nope.md"}).status_code == 404
    image = client.get("/v1/docs/proj/image", params={"path": "logo.png"})
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"


def test_comments_are_saved_listed_and_deleted(env):
    client, *_ = env
    cid = str(uuid.uuid4())
    assert _put(client, cid).json()["body"] == "say more"
    assert _put(client, cid, body="changed").json()["body"] == "changed"
    listed = client.get("/v1/reviews/proj", params={"branch": "dev", "path": "README.md"}).json()["comments"]
    assert [c["id"] for c in listed] == [cid]
    assert client.get("/v1/reviews/proj", params={"branch": "feature/x"}).json()["comments"] == []
    assert client.delete(f"/v1/reviews/proj/comments/{cid}").json() == {"deleted": True}
    assert _put(client, "nope").status_code == 400


def test_a_done_comments_section_is_located_in_the_file_as_it_is_now(env):
    client, repo, *_ = env
    _put(client, status="done", change={"kind": "edited", "start_line": 1, "end_line": 1, "source": "Hello."})
    comment = client.get("/v1/reviews/proj", params={"path": "README.md"}).json()["comments"][0]
    assert comment["change"]["found"] is True and comment["change"]["start_line"] == 3


def test_checkout_switches_the_projects_branch(env):
    client, repo, *_ = env
    ok = client.post("/v1/jobs/agent.docs_checkout", json={"job_id": "j", "project_id": "proj", "params": {"branch": "feature/x"}}).json()
    assert ok == {"outcome": "succeeded", "result": {"branch": "feature/x"}}
    bad = client.post("/v1/jobs/agent.docs_checkout", json={"job_id": "j", "project_id": "proj", "params": {"branch": "ghost"}}).json()
    assert bad["outcome"] == "failed"


def test_update_docs_claims_the_comments_and_is_accepted(env):
    client, repo, conn, *_ = env
    cid = _put(client).json()["id"]
    answer = client.post("/v1/jobs/agent.update_docs", json={"job_id": "job-1", "project_id": "proj", "params": {"branch": "dev", "remote_control": True}}).json()
    assert answer == {"outcome": "accepted", "result": {"dispatched": True, "comments": 1}}
    assert db.get_doc_update(conn, "job-1")["remote_control"] is True
    assert _put(client, cid, body="edit").status_code == 409  # locked while the job holds it
    again = client.post("/v1/jobs/agent.update_docs", json={"job_id": "job-2", "project_id": "proj", "params": {"branch": "dev"}}).json()
    assert again["outcome"] == "failed" and "already running" in again["error"]


def test_update_docs_fails_without_comments(env):
    client, *_ = env
    answer = client.post("/v1/jobs/agent.update_docs", json={"job_id": "job-1", "project_id": "proj", "params": {"branch": "dev"}}).json()
    assert answer["outcome"] == "failed" and "no open comments" in answer["error"]


def test_stop_hook_stops_an_update_job_and_nothing_else(env):
    client, repo, conn, *_ = env
    cid = _put(client).json()["id"]
    client.post("/v1/jobs/agent.update_docs", json={"job_id": "job-1", "project_id": "proj", "params": {"branch": "dev"}})
    assert client.post("/v1/jobs/other/stop").json() == {"stopped": False, "reason": "not stoppable"}
    assert client.post("/v1/jobs/job-1/stop").json() == {"stopped": True}
    assert _put(client, cid, body="free again").status_code == 200
    assert client.post("/v1/jobs/job-1/stop").json()["stopped"] is False


def test_startup_fails_a_job_that_never_got_its_session_and_resumes_running_ones(env, monkeypatch):
    client, repo, conn, flow, paths = env
    _put(client)
    client.post("/v1/jobs/agent.update_docs", json={"job_id": "job-1", "project_id": "proj", "params": {"branch": "dev"}})
    db.create_doc_update(conn, "job-2", project_id="proj", branch="feature/x", remote_control=False, comment_ids=[])
    db.update_doc_update(conn, "job-2", status=db.DOC_UPDATE_RUNNING, worktree_path=str(repo))
    reported = []
    monkeypatch.setattr("agent.app._complete_orchestration_job", lambda p, job_id, **kw: reported.append((job_id, kw["succeeded"])))
    watched = []
    flow.watch = lambda conn_, job_id, project_dir: watched.append(job_id)
    assert resume_doc_updates(paths, conn, flow) == ["job-2"]
    assert reported == [("job-1", False)]
    assert db.get_doc_update(conn, "job-1")["status"] == db.DOC_UPDATE_FAILED
