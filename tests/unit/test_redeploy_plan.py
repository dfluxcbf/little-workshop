"""Redeploying several pillars: the batches their dependencies allow, and the Jobs that run them."""

from __future__ import annotations

import pytest

from common import redeploy_plan
from common.redeploy_plan import REDEPLOY_AFTER, redeploy_batches, submit_redeploy_batches

ALL = ["orchestration", "policy", "registry", "requirements", "version", "build", "deployment", "agent", "dashboard"]


# --- REQ-000000314: Pillars are redeployed in batches ordered by their dependencies ---

def test_all_pillars_are_four_batches_in_the_proven_order():
    assert redeploy_batches(ALL) == [
        ["orchestration"],
        ["agent", "build", "dashboard"],
        ["policy", "registry", "requirements", "version"],
        ["deployment"],
    ]


def test_the_input_order_does_not_matter():
    assert redeploy_batches(reversed(ALL)) == redeploy_batches(ALL)


def test_pillars_with_no_rule_between_them_are_one_batch():
    assert redeploy_batches(["policy", "version"]) == [["policy", "version"]]
    assert redeploy_batches(["dashboard", "build", "agent"]) == [["agent", "build", "dashboard"]]
    assert redeploy_batches(["registry"]) == [["registry"]]


def test_two_pillars_with_a_rule_between_them_are_two_batches():
    assert redeploy_batches(["version", "dashboard"]) == [["dashboard"], ["version"]]
    assert redeploy_batches(["deployment", "orchestration"]) == [["orchestration"], ["deployment"]]


def test_a_pillar_that_is_not_chosen_adds_no_batch():
    # nothing between orchestration and policy is chosen, yet policy still follows orchestration
    assert redeploy_batches(["policy", "orchestration"]) == [["orchestration"], ["policy"]]
    assert redeploy_batches(["version", "deployment"]) == [["version"], ["deployment"]]


def test_core_goes_after_even_deployment_and_an_unknown_pillar_goes_with_the_third_group():
    assert redeploy_batches(["core", "deployment"]) == [["deployment"], ["core"]]
    assert redeploy_batches(["newcomer", "policy"]) == [["newcomer", "policy"]]
    assert redeploy_batches(["newcomer", "agent"]) == [["agent"], ["newcomer"]]


def test_duplicates_and_nothing_chosen():
    assert redeploy_batches(["policy", "policy"]) == [["policy"]]
    assert redeploy_batches([]) == []


def test_a_cycle_in_the_rules_is_reported(monkeypatch):
    monkeypatch.setitem(REDEPLOY_AFTER, "orchestration", frozenset({"policy"}))
    with pytest.raises(ValueError, match="cycle"):
        redeploy_batches(["orchestration", "policy"])


def test_every_rule_points_at_a_pillar_that_comes_earlier():
    for pillar, after in REDEPLOY_AFTER.items():
        assert pillar not in after
        for earlier in after:
            assert pillar not in REDEPLOY_AFTER.get(earlier, frozenset())


def _submit(pillars, **kwargs):
    created = []
    plan = submit_redeploy_batches(created.append, pillars, "abc123", created_by="test", **kwargs)
    return plan, {t["project_id"]: t for t in created}


def test_each_batch_waits_for_every_job_of_the_one_before():
    plan, jobs = _submit(ALL)
    ids = [[row["job_id"] for row in batch] for batch in plan]
    assert [[row["pillar"] for row in batch] for batch in plan] == redeploy_batches(ALL)
    assert jobs["orchestration"]["depends_on"] == []
    for pillar in ("agent", "build", "dashboard"):
        assert jobs[pillar]["depends_on"] == ids[0]
    for pillar in ("policy", "registry", "requirements", "version"):
        assert jobs[pillar]["depends_on"] == ids[1]
    assert jobs["deployment"]["depends_on"] == ids[2]


def test_the_jobs_are_redeploy_jobs_with_their_own_queue_and_chosen_id():
    plan, jobs = _submit(["policy", "version"])
    for row in plan[0]:
        job = jobs[row["pillar"]]
        assert job["id"] == row["job_id"] and job["type"] == "deployment.redeploy_pillar" and job["target_pillar"] == "deployment"
        assert job["params"] == {"pillar": row["pillar"], "commit": "abc123"} and job["queue_key"] == row["pillar"] and job["created_by"] == "test"
        assert job["depends_on"] == []


def test_the_first_batch_can_wait_for_another_job_and_a_confirm_timeout_is_passed_on():
    plan, jobs = _submit(["orchestration", "policy"], depends_on=["release-job"], confirm_timeout_seconds=90)
    assert jobs["orchestration"]["depends_on"] == ["release-job"]
    assert jobs["policy"]["depends_on"] == [plan[0][0]["job_id"]]
    assert jobs["policy"]["params"]["confirm_timeout_seconds"] == 90


def test_nothing_to_redeploy_creates_nothing():
    created = []
    assert submit_redeploy_batches(created.append, [], "abc", created_by="t") == [] and created == []


def test_a_batch_is_created_concurrently():
    import threading

    started, release = [], threading.Event()

    def create(template):
        started.append(template["project_id"])
        if len(started) == 3:
            release.set()
        assert release.wait(5), "the creation calls of one batch must not wait for each other"

    submit_redeploy_batches(create, ["agent", "build", "dashboard"], "abc", created_by="t")
    assert sorted(started) == ["agent", "build", "dashboard"]
