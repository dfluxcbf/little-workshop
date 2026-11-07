"""The bootstrap interview (REQ-000000519) and what follows an import (REQ-000000520)."""

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import registry.app as app_module
from agent.flow import AgentFlow, INTENTS
from common.paths import Paths
from registry import db
from registry.app import build_app


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def client(paths, tmp_path):
    conn = db.connect(tmp_path / "registry.db")
    (tmp_path / "workdir").mkdir()
    yield TestClient(build_app(conn, paths)), tmp_path / "workdir"
    conn.close()


# --- REQ-000000519: A bootstrap can start an interview that fills docs/design.md ---


def test_bootstrap_with_interview_starts_a_manual_interview_task(client, monkeypatch):
    app_client, workdir = client
    submitted = []
    monkeypatch.setattr(app_module, "submit_job", lambda paths, template, **kw: (submitted.append(template), {"id": "job-1", "status": "running"})[1])

    body = app_client.post("/v1/jobs/project.bootstrap", json={
        "job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir), "interview": True},
    }).json()

    assert body["outcome"] == "succeeded" and body["result"]["interview_job_id"] == "job-1"
    (job,) = submitted
    assert (job["type"], job["target_pillar"], job["project_id"]) == ("agent.run_task", "agent", "little-widget")
    assert job["params"]["intent"] == "interview" and job["params"]["review_mode"] == "manual"


def test_bootstrap_without_interview_starts_nothing(client, monkeypatch):
    app_client, workdir = client
    monkeypatch.setattr(app_module, "submit_job", lambda *a, **kw: pytest.fail("no Job expected"))
    body = app_client.post("/v1/jobs/project.bootstrap", json={
        "job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir)},
    }).json()
    assert body["outcome"] == "succeeded" and body["result"]["interview_job_id"] is None


def test_an_interview_that_cannot_start_leaves_the_bootstrap_succeeded(client, monkeypatch):
    app_client, workdir = client
    monkeypatch.setattr(app_module, "submit_job", lambda *a, **kw: {"status": "failed", "error": "Orchestration is unreachable"})
    body = app_client.post("/v1/jobs/project.bootstrap", json={
        "job_id": "j1", "params": {"name": "little-widget", "dest_dir": str(workdir), "interview": True},
    }).json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["interview_error"] == "Orchestration is unreachable"


def _interview_prompt(tmp_path: Path, governance: str, grill_me: bool) -> str:
    checkout = tmp_path / f"p-{governance}-{grill_me}"
    checkout.mkdir()
    if grill_me:
        (checkout / ".claude" / "skills" / "grill-me").mkdir(parents=True)
        (checkout / ".claude" / "skills" / "grill-me" / "SKILL.md").write_text("---\nname: grill-me\n---\n", encoding="utf-8")
    return AgentFlow._intent_instructions("interview", checkout, governance)


def test_the_interview_prompt_uses_grill_me_when_installed_and_requirements_only_when_full(tmp_path):
    assert "interview" in INTENTS
    with_skill = _interview_prompt(tmp_path, "full", True)
    assert "`grill-me` skill" in with_skill and "docs/requirements.md" in with_skill
    plain = _interview_prompt(tmp_path, "light", False)
    assert "grill-me" not in plain and "plain, concrete questions" in plain
    assert "Write no requirements" in plain and "docs/design.md" in plain
    assert "{" not in plain  # every placeholder filled


# --- REQ-000000520: An import syncs the Claude files and runs a preflight ---


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "existing"
    repo.mkdir()
    for args in (["init", "-b", "dev"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, check=True, capture_output=True)
    return repo


def test_import_submits_the_sync_and_shows_the_preflight_in_its_result(client, monkeypatch, tmp_path):
    app_client, _ = client
    submitted = []

    def fake_submit(paths, template, **kw):
        submitted.append(template)
        return {"id": "sync-1", "status": "succeeded",
                "result": {"projects": [{"project_id": "little-widget", "changed": [".claude/AGENTS.md"], "written": True,
                                         "branch": "improvement/claude-files-sync"}]}}

    def fake_evaluate(paths, project_id, gate_point="branch_finish"):
        assert (project_id, gate_point) == ("little-widget", "branch_finish")
        return {"gate_point": gate_point, "blocking": ["secrets"], "results": [{"topic": "secrets", "outcome": "fail", "detail": "a key"}]}

    monkeypatch.setattr(app_module, "submit_job", fake_submit)
    monkeypatch.setattr(app_module, "run_preflight", fake_evaluate)

    body = app_client.post("/v1/jobs/project.import", json={
        "job_id": "j1", "params": {"name": "little-widget", "path": str(_repo(tmp_path))},
    }).json()

    assert body["outcome"] == "succeeded"
    (job,) = submitted
    assert (job["type"], job["project_id"], job["params"]) == ("registry.sync_claude_files", "little-widget", {"write": True})
    assert body["result"]["sync"]["job_id"] == "sync-1" and body["result"]["sync"]["written"] is True
    assert body["result"]["preflight"]["blocking"] == ["secrets"]


def test_import_with_the_pillars_down_still_registers_and_says_why(client, tmp_path):
    app_client, _ = client
    body = app_client.post("/v1/jobs/project.import", json={
        "job_id": "j1", "params": {"name": "little-widget", "path": str(_repo(tmp_path))},
    }).json()
    assert body["outcome"] == "succeeded"
    assert body["result"]["sync"]["error"] and body["result"]["preflight"]["error"]
