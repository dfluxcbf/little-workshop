"""The two reads the Dashboard made on every view of a page that cost the most: the Jobs page's count of what
each cleanup age would delete (every Job read in full, six times, holding Orchestration's lock: 1.1 s with
1,792 Jobs), and the attention banner's policy check results (their whole history, 1.4 MB, to find the 4
failing latest ones), with the policies' test scripts (118 KB of a 140 KB list)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from orchestration import db
from orchestration.app import build_app
from orchestration.models import Job, JobStatus

NOW = datetime.now(timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


# --- REQ-000000389: A cleanup count reads no Job's content ---


def test_the_cleanup_preview_counts_without_reading_any_jobs_params_or_result(conn, monkeypatch):
    old, older, waited_on = (Job(type="build.run", target_pillar="build", status=JobStatus.SUCCEEDED, result={"log": "x" * 10_000},
                                 created_at=NOW - timedelta(days=d), updated_at=NOW - timedelta(days=d)) for d in (3, 10, 10))
    waiting = Job(type="build.run", target_pillar="build", status=JobStatus.QUEUED, depends_on=[waited_on.id])
    for job in (old, older, waited_on, waiting):
        db.insert_job(conn, job)

    def no_full_reads(*args, **kwargs):
        raise AssertionError("a cleanup count read Jobs in full")

    monkeypatch.setattr(db, "list_jobs", no_full_reads)
    monkeypatch.setattr(db, "row_to_job", no_full_reads)
    client = TestClient(build_app(conn, socket_lookup=lambda pillar: None))
    # 2 days: the two old ones but not the one a queued Job waits on; 7 days: only the older one
    assert client.get("/v1/job-cleanup", params={"hours": "48,168,672"}).json() == {"prunable": {"48": 2, "168": 1, "672": 0}}
    assert db.count_prunable_jobs(conn, NOW - timedelta(days=2)) == 2


# --- REQ-000000390: The latest policy results are read, not their history ---


@pytest.fixture
def policy_store(tmp_path):
    from policy import db as policy_db

    connection = policy_db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


def _result(policy_id, project_id, outcome, minutes_ago, detail=""):
    from policy.models import CheckOutcome, PolicyCheckResult

    return PolicyCheckResult(id=f"{policy_id}-{project_id}-{minutes_ago}", policy_id=policy_id, project_id=project_id,
                             outcome=CheckOutcome(outcome), detail=detail, checked_at=NOW - timedelta(minutes=minutes_ago))


def test_the_policy_pillar_lists_only_the_latest_result_of_each_policy_and_project(policy_store, tmp_path):
    from policy import db as policy_db
    from policy.app import build_app as build_policy_app

    for result in (_result("p1", "meals", "fail", 50), _result("p1", "meals", "pass", 10),  # failed once, passes now
                   _result("p1", "home", "pass", 40), _result("p1", "home", "fail", 5),      # passed once, fails now
                   _result("p2", "meals", "fail", 30)):
        policy_db.insert_check_result(policy_store, result)
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    client = TestClient(build_policy_app(policy_store, paths))

    def ids(**params):
        return [r["id"] for r in client.get("/v1/policy_check_results", params=params).json()]

    assert ids() == ["p1-meals-50", "p1-home-40", "p2-meals-30", "p1-meals-10", "p1-home-5"]  # the history, oldest first
    assert ids(latest=True) == ["p2-meals-30", "p1-meals-10", "p1-home-5"]
    assert ids(latest=True, outcome="fail") == ["p2-meals-30", "p1-home-5"]  # failing now, not failed once
    assert ids(latest=True, policy_id="p1") == ["p1-meals-10", "p1-home-5"]
    assert ids(checked_since=(NOW - timedelta(minutes=20)).isoformat()) == ["p1-meals-10", "p1-home-5"]
    assert client.get("/v1/policy_check_results", params={"outcome": "maybe"}).status_code == 422


def test_policies_are_listed_without_their_test_scripts_when_asked(tmp_path, monkeypatch):
    from policy import app as policy_app
    from policy import db as policy_db

    conn = policy_db.connect(tmp_path / "policy.db")
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    script = {"id": "p1", "topic": "commit-bumps-version", "test_script": "def test():\n    pass\n" * 500}
    monkeypatch.setattr(policy_db, "list_policies", lambda conn, topic=None: [type("P", (), {"model_dump": lambda self, mode: dict(script)})()])
    client = TestClient(policy_app.build_app(conn, paths))
    (full,) = client.get("/v1/policies").json()
    (summary,) = client.get("/v1/policies", params={"view": "summary"}).json()
    assert len(full["test_script"]) > 10_000 and "test_script" not in summary and summary["topic"] == "commit-bumps-version"
    conn.close()


def test_the_banner_reads_the_latest_failing_results_and_policies_without_scripts(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client

    asked = {}
    policy = {"id": "p1", "topic": "commit-bumps-version", "enabled": True, "projects": [{"project_id": "meals"}]}
    failing = {"id": "r1", "policy_id": "p1", "project_id": "meals", "outcome": "fail", "detail": "VERSION not raised", "checked_at": NOW.isoformat()}
    monkeypatch.setattr(client, "policy_list", lambda paths, **kw: (asked.setdefault("policies", kw), [policy])[1])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: (asked.setdefault("results", kw), [failing])[1])
    monkeypatch.setattr(client, "list_jobs", lambda paths, **kw: [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    items = app_module.collect_attention(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"))
    assert [i["title"] for i in items] == ["commit-bumps-version is failing"]
    assert asked["policies"].get("summary") is True
    assert asked["results"].get("latest") is True and asked["results"].get("outcome") == "fail"


def test_a_history_from_an_older_policy_pillar_still_gives_the_latest_failing_only(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client

    history = [  # what a Policy pillar from before `latest` answers whatever it is asked: everything, oldest first
        {"id": "r1", "policy_id": "p1", "project_id": "meals", "outcome": "fail", "checked_at": "2026-10-05T06:00:00+00:00"},
        {"id": "r2", "policy_id": "p1", "project_id": "meals", "outcome": "pass", "checked_at": "2026-10-05T07:00:00+00:00"},
    ]
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: list(history))
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    assert app_module._latest_policy_results(paths, failing=True) == {}
    assert list(app_module._latest_policy_results(paths)) == [("p1", "meals")]


# --- REQ-000000391: Statistics read only new policy results ---


def test_statistics_read_only_the_policy_results_recorded_since_the_last_count(tmp_path):
    from dashboard import statistics

    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    assert statistics.to_read(paths, now) == {"jobs": None, "policy_results": None}  # never counted: everything
    policies = [{"id": "p1", "topic": "commit-bumps-version"}]
    first = [{"id": "r1", "policy_id": "p1", "project_id": "meals", "outcome": "pass", "checked_at": "2026-10-05T07:00:00+00:00"}]
    statistics.update(paths, projects=["meals"], jobs=[], policy_results=first, policies=policies, incidents=[],
                      since=statistics.to_read(paths, now), read_at=now)
    since = statistics.to_read(paths, now + timedelta(minutes=10))
    assert since == {"jobs": now - statistics.READ_MARGIN, "policy_results": now - statistics.READ_MARGIN}
    later = [{"id": "r2", "policy_id": "p1", "project_id": "meals", "outcome": "fail", "checked_at": "2026-10-05T08:05:00+00:00"}]
    statistics.update(paths, projects=["meals"], jobs=[], policy_results=later, policies=policies, incidents=[],
                      since=since, read_at=now + timedelta(minutes=10))  # a partial read forgets nothing
    rows = statistics.load(paths)["policies"]
    assert sum(r["passed"] for r in rows) == 1 and sum(r["failed"] for r in rows) == 1
    assert statistics.to_read(paths, now + timedelta(days=1, minutes=1)) == {"jobs": None, "policy_results": None}


def test_the_dashboards_statistics_update_asks_for_new_policy_results_after_its_first_count(tmp_path, monkeypatch):
    from dashboard import app as app_module
    from dashboard import client

    asked = []
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [{"name": "meals"}])
    monkeypatch.setattr(client, "list_jobs", lambda paths, **kw: [])
    monkeypatch.setattr(client, "policy_list", lambda paths, **kw: [])
    monkeypatch.setattr(client, "policy_check_results", lambda paths, **kw: asked.append(kw) or [])
    monkeypatch.setattr(client, "list_incidents", lambda paths, status=None: [])
    test = TestClient(app_module.build_app(Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    test.get("/statistics")
    test.get("/statistics")
    counted = [kw for kw in asked if not kw.get("latest")]  # the banner's own reads are the latest failing ones
    assert counted[0].get("checked_since") is None and counted[1].get("checked_since") is not None
