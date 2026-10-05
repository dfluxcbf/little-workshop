""""Make it a task": the Usage pillar hands New task the candidate's task to fill in, and is told when it
starts; and the cheap version a live Claude usage page watches."""

from __future__ import annotations

from usage import db
from test_usage_automation import REDEPLOY, conn, episode, paths, usage  # noqa: F401 - fixtures


# --- REQ-000000373: "Make it a task" opens New task ---


def test_a_candidates_task_is_served_without_starting_anything(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
    usage.mine()
    candidate = db.list_candidates(conn)[0]
    task = usage.get(f"/v1/candidates/{candidate['id']}/task").json()
    assert (task["project_id"], task["title"], task["intent"], task["review_mode"]) == (
        "little-workshop", "Automate repeated Claude work", "task", "manual")
    assert "mcp:workshop_deployment_redeploy_pillar(commit, pillar)" in task["description"]
    assert usage.jobs == [] and db.get_candidate(conn, candidate["id"])["state"] == "open"
    assert usage.get("/v1/candidates/nope/task").status_code == 404

    assert usage.post(f"/v1/candidates/{candidate['id']}/task_started", json={"job_id": "j9"}).json() == {"ok": True, "job_id": "j9"}
    assert db.get_candidate(conn, candidate["id"])["state"] == "accepted"
    usage.post(f"/v1/candidates/{candidate['id']}", json={"state": "dismissed"})
    assert usage.get(f"/v1/candidates/{candidate['id']}/task").status_code == 404
    assert usage.post(f"/v1/candidates/{candidate['id']}/task_started", json={}).json()["ok"] is False


# --- REQ-000000379: Pages update themselves ---


def test_the_usage_version_changes_when_a_request_or_a_candidate_does(usage, conn):
    before = usage.get("/v1/version").json()["version"]
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY])
    after_requests = usage.get("/v1/version").json()["version"]
    assert after_requests != before
    usage.mine()
    assert usage.get("/v1/version").json()["version"] != after_requests
