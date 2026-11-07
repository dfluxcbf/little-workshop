"""A project's governance decides which policies apply to it (docs/pillars/policy_compliance.md, "Governance"):
a policy that requires `full` governance is not applicable on a `light` project."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from policy import app as app_module
from policy import db
from policy.app import build_app
from policy.models import CheckOutcome
from policy.sandbox import SandboxResult

GOVERNANCE = {"light-proj": "light", "full-proj": "full"}


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (("init", "-q", "-b", "dev"), ("config", "user.email", "t@example.com"), ("config", "user.name", "T")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "VERSION").write_text("0.1.0\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, capture_output=True)
    return repo


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, tmp_path, checkout, monkeypatch):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    monkeypatch.setattr(app_module.registry_client, "list_active_project_ids", lambda paths, **kw: list(GOVERNANCE))
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: {
        "path": str(checkout), "name": project_id, "governance": GOVERNANCE[project_id]})
    monkeypatch.setattr(app_module.sandbox, "run_policy_check", lambda *a, **kw: SandboxResult(CheckOutcome.FAIL, "broken"))
    return TestClient(build_app(conn, paths))


def _activate(client, topic, *, requires=None):
    params = {
        "ears_text": f"The project shall satisfy {topic}.", "test_script": "def test_x(): pass\n", "topic": topic,
        "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
        "gate_points": ["branch_finish"], "approved": True,
    }
    if requires is not None:
        params["requires"] = requires
    return client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": params}).json()


# --- REQ-000000496: requirement work is asked only of full-governance projects -----------------

def test_a_policy_records_the_governance_it_requires(client, conn):
    policy = _activate(client, "marker-rule", requires="full")["result"]
    assert policy["requires"] == "full" and db.get_policy(conn, policy["id"]).requires == "full"
    edited = client.post("/v1/jobs/policy.activate", json={"job_id": "j", "params": {
        "policy_id": policy["id"], "ears_text": "The project shall x.", "test_script": "def test_x(): pass\n",
        "topic": "marker-rule", "target_pillar": "version", "scope_level": "all_projects", "created_by": "framework",
        "approved": True}}).json()
    assert edited["result"]["requires"] == "full"  # an edit that leaves it out keeps it
    refused = _activate(client, "other-rule", requires="light")
    assert refused["outcome"] == "failed" and "requires" in refused["error"]


def test_a_light_project_skips_the_policies_requiring_full_governance(client):
    _activate(client, "marker-rule", requires="full")
    _activate(client, "secrets-rule")
    light = client.post("/v1/policies/evaluate", json={"project_id": "light-proj", "gate_point": "branch_finish"}).json()
    full = client.post("/v1/policies/evaluate", json={"project_id": "full-proj", "gate_point": "branch_finish"}).json()
    assert [r["topic"] for r in light["results"]] == ["secrets-rule"]
    assert sorted(r["topic"] for r in full["results"]) == ["marker-rule", "secrets-rule"]
    gating = client.get("/v1/policies/gating-for-job", params={"project_id": "light-proj", "categories": "branch_finish"}).json()
    assert [p["topic"] for p in gating] == ["secrets-rule"]


def test_a_check_of_a_full_only_policy_on_a_light_project_is_not_applicable(client, conn):
    policy = _activate(client, "marker-rule", requires="full")["result"]
    answer = client.post("/v1/jobs/policy.check_project",
                         json={"job_id": "c", "project_id": "light-proj", "params": {"policy_id": policy["id"]}}).json()
    assert answer["outcome"] == "succeeded" and answer["result"]["outcome"] == "not_applicable"
    assert "light" in answer["result"]["detail"]
    assert db.latest_check_result(conn, policy_id=policy["id"], project_id="light-proj").outcome == CheckOutcome.NOT_APPLICABLE


def test_an_unknown_governance_runs_every_policy(client, monkeypatch):
    _activate(client, "marker-rule", requires="full")
    monkeypatch.setattr(app_module, "resolve_project", lambda paths, project_id, **kw: (_ for _ in ()).throw(
        app_module.ProjectResolutionError("registry down")))
    result = client.post("/v1/policies/evaluate", json={"project_id": "light-proj", "gate_point": "branch_finish"}).json()
    assert [r["topic"] for r in result["results"]] == ["marker-rule"]


def test_the_manifest_marks_the_requirement_policies_full_and_keeps_the_safety_ones_for_all():
    import json
    from pathlib import Path

    manifest = json.loads((Path(__file__).resolve().parents[2] / "tools/policy_scripts/policies.json").read_text())
    requires = {e["topic"]: e.get("requires") for e in manifest["policies"]}
    for topic in ("requirement-marker-registered", "requirement-marker-id-format", "requirement-marker-not-superseded",
                  "python-test-coverage"):
        assert requires[topic] == "full", topic
    for topic in ("no-committed-secrets", "ssh-remotes-no-embedded-credentials", "version-format",
                  "claude-config-present", "claude-instructions-valid"):
        assert requires[topic] is None, topic
    assert "commit-cites-requirement" not in requires
