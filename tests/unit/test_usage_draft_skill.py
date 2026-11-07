"""Draft as a skill: a candidate or a plugin proposal becomes a fixed agent task writing a draft skill."""

from __future__ import annotations

from usage import db, skill_draft
from test_usage_automation import REDEPLOY, conn, episode, paths, usage  # noqa: F401 - fixtures


# --- REQ-000000508: Draft as a skill starts a fixed agent task ---


def test_drafting_a_candidate_starts_one_skill_task_and_accepts_it(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY], project="little-meals")
    usage.mine()
    candidate = db.list_candidates(conn)[0]
    answer = usage.post("/v1/jobs/usage.draft_skill", json={"job_id": "j1", "params": {"candidate_id": candidate["id"]}}).json()
    assert answer["outcome"] == "succeeded" and answer["result"]["project_id"] == "little-meals"
    (job,) = usage.jobs
    assert (job["type"], job["target_pillar"], job["project_id"]) == ("agent.run_task", "agent", "little-meals")
    params = job["params"]
    assert (params["intent"], params["review_mode"]) == ("skill", "manual")
    assert params["title"] == "Draft skill deployment-redeploy-pillar"
    assert ".claude/skills/deployment-redeploy-pillar/SKILL.md" in params["description"]
    assert "mcp:workshop_deployment_redeploy_pillar" in params["description"]
    assert db.get_candidate(conn, candidate["id"])["state"] == "accepted"


def test_the_job_names_the_project_the_draft_goes_to(usage, conn):
    for i in range(3):
        episode(conn, f"s{i}", "p1", [REDEPLOY], project="little-meals")
    usage.mine()
    candidate = db.list_candidates(conn)[0]
    usage.post("/v1/jobs/usage.draft_skill", json={"job_id": "j1", "project_id": "little-home",
                                                     "params": {"candidate_id": candidate["id"]}})
    assert usage.jobs[0]["project_id"] == "little-home"


def test_a_closed_or_unknown_row_starts_nothing(usage):
    answer = usage.post("/v1/jobs/usage.draft_skill", json={"job_id": "j1", "params": {"proposal_id": "nope"}}).json()
    assert answer["outcome"] == "failed" and "no open proposal" in answer["error"] and usage.jobs == []


def test_a_proposals_draft_lists_its_steps_and_goes_to_its_project():
    proposal = {"name": "finish-branch", "family": "version_finish", "scope": "project:little-meals", "occurrences": 4,
                "sessions": 2, "weekly_value": 1.5, "usage": {"frequency": "weekly"},
                "steps": [{"step": "requirements_run_tests", "share": 0.75}, {"step": "version_finish", "share": 1.0}],
                "examples": [{"project": "little-meals", "calls": ["lws version improvement-finish"]}]}
    assert skill_draft.project_of(proposal, "little-workshop") == "little-meals"
    job = skill_draft.draft_job("proposal", proposal, "little-meals")
    assert job["params"]["title"] == "Draft skill finish-branch" and job["params"]["intent"] == "skill"
    assert "1. `requirements_run_tests` (75% of the requests)" in job["params"]["description"]
    assert skill_draft.project_of({"scope": "all-projects"}, "little-workshop") == "little-workshop"
