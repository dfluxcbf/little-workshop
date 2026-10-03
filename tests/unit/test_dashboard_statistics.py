"""The Dashboard's Statistics page and the per-day store behind it (`dashboard.statistics`),
with `dashboard.client` monkeypatched: what is counted, on which day and for which project and pillar,
and what the page is handed to plot."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from dashboard import app as app_module
from dashboard import client, statistics


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    """Days are local dates; pin the zone so a timestamp's day does not depend on the machine."""
    import time

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def _job(job_id, type="version.improvement_finish", project="little-widget", status="succeeded", day="2026-10-01", seconds=30, **extra):
    return {"id": job_id, "type": type, "target_pillar": type.split(".")[0], "project_id": project, "status": status,
            "created_at": f"{day}T12:00:00Z", "updated_at": f"{day}T12:00:{seconds:02d}Z", "result": None, **extra}


def _update(paths, jobs=(), policy_results=(), policies=(), incidents=(), projects=("little-widget",)):
    statistics.update(paths, projects=list(projects), jobs=list(jobs), policy_results=list(policy_results), policies=list(policies),
                      incidents=list(incidents))
    return statistics.load(paths)


def _only(rows, **match):
    found = [row for row in rows if all(row[k] == v for k, v in match.items())]
    assert len(found) == 1, found
    return found[0]


# --- REQ-000000316: Operation statistics are kept per day, apart from the pillars ---

def test_jobs_are_counted_per_day_type_and_project(paths):
    stats = _update(paths, jobs=[
        _job("a", seconds=10), _job("b", seconds=30), _job("c", status="failed", seconds=20),
        _job("d", day="2026-10-02"), _job("e", type="deployment.deploy"),
    ])
    row = _only(stats["jobs"], day="2026-10-01", job="version.improvement_finish", project="little-widget", pillar="version")
    assert (row["triggered"], row["succeeded"], row["failed"]) == (3, 2, 1)
    assert (row["duration_seconds"], row["duration_count"]) == (60.0, 3)
    assert _only(stats["jobs"], day="2026-10-02")["triggered"] == 1
    assert _only(stats["jobs"], job="deployment.deploy")["triggered"] == 1
    assert stats["updated_at"]


def test_a_job_without_a_project_is_counted_under_its_pillar(paths):
    row = _only(_update(paths, jobs=[_job("a", type="policy.activate", project=None)])["jobs"])
    assert (row["project"], row["pillar"]) == ("", "policy")


def test_a_pillar_redeploy_names_no_project(paths):
    row = _only(_update(paths, jobs=[_job("a", type="deployment.redeploy_pillar", project="dashboard")])["jobs"])
    assert (row["project"], row["pillar"]) == ("", "deployment")


def test_only_a_registered_project_is_a_project(paths):
    stats = _update(paths, jobs=[_job("a", project="workspace", type="agent.session_start"), _job("b", project="improvement/typo"),
                                 _job("c")])
    assert sorted(row["project"] for row in stats["jobs"]) == ["", "", "little-widget"]


def test_nothing_is_counted_while_the_projects_cannot_be_read(paths):
    statistics.update(paths, projects=None, jobs=[_job("a")], policy_results=[], policies=[], incidents=[])
    assert statistics.load(paths)["jobs"] == []
    assert _only(_update(paths, jobs=[_job("a")])["jobs"])["project"] == "little-widget"


def test_a_store_from_before_project_and_pillar_were_apart_is_recounted(paths):
    import sqlite3

    target = paths.state_home / "dashboard" / "statistics.db"
    target.parent.mkdir(parents=True)
    old = sqlite3.connect(target)
    old.executescript(
        "CREATE TABLE seen (kind TEXT, id TEXT, settled INTEGER, PRIMARY KEY (kind, id));"
        "CREATE TABLE job_days (day TEXT, job TEXT, scope TEXT, triggered INTEGER);"
        "INSERT INTO seen VALUES ('job', 'a', 1); INSERT INTO job_days VALUES ('2026-10-01', 'x', 'little-widget', 5);"
    )
    old.commit()
    old.close()
    row = _only(_update(paths, jobs=[_job("a")])["jobs"])
    assert (row["triggered"], row["project"], row["pillar"]) == (1, "little-widget", "version")


def test_updating_twice_counts_nothing_twice(paths):
    jobs = [_job("a"), _job("b", status="failed")]
    results = [{"id": "r1", "policy_id": "p1", "project_id": "little-widget", "outcome": "fail", "checked_at": "2026-10-01T09:00:00Z"}]
    incidents = [{"id": "i1", "created_at": "2026-10-01T09:00:00Z"}]
    first = _update(paths, jobs, results, [{"id": "p1", "topic": "coverage"}], incidents)
    second = _update(paths, jobs, results, [{"id": "p1", "topic": "coverage"}], incidents)
    for table in ("jobs", "policies", "incidents", "tests"):
        assert first[table] == second[table]


def test_a_running_job_is_triggered_now_and_settled_once_it_ends(paths):
    stats = _update(paths, jobs=[_job("a", status="running")])
    row = _only(stats["jobs"])
    assert (row["triggered"], row["succeeded"], row["failed"], row["duration_count"]) == (1, 0, 0, 0)
    row = _only(_update(paths, jobs=[_job("a", status="succeeded", seconds=40)])["jobs"])
    assert (row["triggered"], row["succeeded"], row["duration_seconds"]) == (1, 1, 40.0)


def test_a_cancelled_job_is_triggered_but_neither_succeeded_nor_failed(paths):
    row = _only(_update(paths, jobs=[_job("a", status="cancelled")])["jobs"])
    assert (row["triggered"], row["succeeded"], row["failed"], row["duration_count"]) == (1, 0, 0, 0)


def test_counts_stay_when_the_jobs_behind_them_are_deleted(paths):
    _update(paths, jobs=[_job("a"), _job("b")])
    stats = _update(paths, jobs=[_job("c", day="2026-10-02")])  # a and b were cleaned up
    assert _only(stats["jobs"], day="2026-10-01")["triggered"] == 2
    assert _only(stats["jobs"], day="2026-10-02")["triggered"] == 1


def test_the_day_is_the_local_date(paths, monkeypatch):
    import time

    monkeypatch.setenv("TZ", "Etc/GMT-2")  # two hours ahead of UTC
    time.tzset()
    stats = _update(paths, jobs=[_job("a", created_at="2026-10-01T23:30:00Z", updated_at="2026-10-01T23:30:05Z")])
    assert _only(stats["jobs"])["day"] == "2026-10-02"


def test_policy_checks_are_counted_per_policy_and_project(paths):
    policies = [{"id": "p1", "topic": "coverage"}]
    results = [
        {"id": "r1", "policy_id": "p1", "project_id": "little-widget", "outcome": "pass", "checked_at": "2026-10-01T09:00:00Z"},
        {"id": "r2", "policy_id": "p1", "project_id": "little-widget", "outcome": "fail", "checked_at": "2026-10-01T10:00:00Z"},
        {"id": "r3", "policy_id": "p1", "project_id": "little-widget", "outcome": "not_applicable", "checked_at": "2026-10-01T11:00:00Z"},
        {"id": "r4", "policy_id": "p1", "project_id": "", "outcome": "fail", "checked_at": "2026-10-01T11:00:00Z"},
        {"id": "r5", "policy_id": "gone", "project_id": "little-widget", "outcome": "pass", "checked_at": "2026-10-01T11:00:00Z"},
    ]
    stats = _update(paths, policy_results=results, policies=policies)
    row = _only(stats["policies"], policy="coverage", project="little-widget")
    assert row["pillar"] == "policy"
    assert (row["passed"], row["failed"], row["not_applicable"]) == (1, 1, 1)
    assert _only(stats["policies"], policy="coverage", project="")["failed"] == 1
    assert _only(stats["policies"], policy="gone")["passed"] == 1  # a removed policy keeps its id as its name


def test_incidents_are_counted_per_day_project_and_pillar(paths):
    def incident(incident_id, day, project="little-widget", pillar="agent"):
        return {"id": incident_id, "created_at": f"{day}T09:00:00Z", "project_id": project, "pillar": pillar}

    stats = _update(paths, incidents=[incident("i1", "2026-10-01"), incident("i2", "2026-10-01"), incident("i3", "2026-10-03"),
                                      incident("i4", "2026-10-03", project="dashboard", pillar="dashboard"),
                                      incident("i5", "2026-10-04", pillar="manual-check")])
    assert _only(stats["incidents"], day="2026-10-01")["incidents"] == 2
    assert _only(stats["incidents"], day="2026-10-03", project="little-widget", pillar="agent")["incidents"] == 1
    assert _only(stats["incidents"], project="", pillar="dashboard")["incidents"] == 1
    assert _only(stats["incidents"], day="2026-10-04")["pillar"] == "unknown"  # not one of the pillars


def test_a_test_execution_is_one_job_and_fails_when_any_test_does(paths):
    run = "requirements.run_tests"
    stats = _update(paths, jobs=[
        _job("a", type=run, result={"ran": 2, "results": [{"verdict": "passed"}, {"verdict": "skipped"}]}),
        _job("b", type=run, result={"ran": 2, "results": [{"verdict": "passed"}, {"verdict": "failed"}]}),
        _job("c", type=run, result={"suites": [{"verdicts": {"passed": 9}}, {"verdicts": {"passed": 3, "error": 1}}]}),
        _job("d", type=run, result={"suites": [{"verdicts": {"passed": 9, "failed": 0}}]}),
        _job("e", type=run, status="failed"),
        _job("f", type="requirements.import_junit", result={"ran": 1, "results": [{"verdict": "passed"}]}),
        _job("g", type=run, status="cancelled"),
        _job("h"),
    ])
    row = _only(stats["tests"])
    assert (row["day"], row["project"], row["pillar"], row["succeeded"], row["failed"]) == ("2026-10-01", "little-widget", "requirements", 3, 3)


def test_an_unreachable_source_is_left_as_it_was(paths):
    _update(paths, jobs=[_job("a")], incidents=[{"id": "i1", "created_at": "2026-10-01T09:00:00Z"}])
    statistics.update(paths, projects=["little-widget"], jobs=None, policy_results=None, policies=None, incidents=None)
    stats = _update(paths, jobs=[_job("a")], incidents=[{"id": "i1", "created_at": "2026-10-01T09:00:00Z"}])
    assert _only(stats["jobs"])["triggered"] == 1 and _only(stats["incidents"])["incidents"] == 1


# --- REQ-000000317: The Statistics page plots the operations per day ---

@pytest.fixture
def test_client(paths, monkeypatch):
    monkeypatch.setattr(app_module, "collect_attention", lambda paths: [])
    return TestClient(app_module.build_app(paths))


def _stub_sources(monkeypatch, jobs=(), down=()):
    def source(name, value):
        def read(paths, **kwargs):
            if name in down:
                raise client.PillarUnavailable(f"{name} daemon unreachable")
            return list(value)
        return read

    monkeypatch.setattr(client, "list_jobs", source("orchestration", jobs))
    monkeypatch.setattr(client, "list_projects", source("registry", [{"name": "little-widget"}]))
    monkeypatch.setattr(client, "policy_check_results", source("policy", []))
    monkeypatch.setattr(client, "policy_list", source("policy", []))
    monkeypatch.setattr(client, "list_incidents", source("agent", []))


def _embedded(text):
    return json.loads(re.search(r'<script type="application/json" id="stats-data">(.*?)</script>', text, re.S).group(1))


def test_every_visit_counts_what_is_new(test_client, monkeypatch):
    _stub_sources(monkeypatch, jobs=[_job("a"), _job("b", project=None)])
    stats = _embedded(test_client.get("/statistics").text)
    assert sorted((row["project"], row["pillar"]) for row in stats["jobs"]) == [("", "version"), ("little-widget", "version")]
    _stub_sources(monkeypatch, jobs=[_job("a"), _job("b", project=None), _job("c")])
    assert sum(row["triggered"] for row in _embedded(test_client.get("/statistics").text)["jobs"]) == 3


def test_an_unreachable_pillar_is_named_and_its_numbers_kept(test_client, monkeypatch):
    _stub_sources(monkeypatch, jobs=[_job("a")])
    test_client.get("/statistics")
    _stub_sources(monkeypatch, down=("orchestration",))
    text = test_client.get("/statistics").text
    assert "Could not read Orchestration" in text
    assert sum(row["triggered"] for row in _embedded(text)["jobs"]) == 1


def test_the_running_dashboard_counts_without_a_visit(paths, monkeypatch):
    import time

    _stub_sources(monkeypatch, jobs=[_job("a")])
    monkeypatch.setattr(app_module, "collect_attention", lambda paths: [])
    with TestClient(app_module.build_app(paths)):  # entering runs the startup handlers, as the real server does
        deadline = time.monotonic() + 5
        while not statistics.load(paths)["jobs"] and time.monotonic() < deadline:
            time.sleep(0.05)
    assert _only(statistics.load(paths)["jobs"])["triggered"] == 1


def test_deleting_old_jobs_counts_them_first_and_keeps_the_statistics(test_client, monkeypatch):
    held = [_job("a"), _job("b", status="failed")]
    _stub_sources(monkeypatch, jobs=held)
    monkeypatch.setattr(client, "list_jobs", lambda paths, **kwargs: list(held))

    def cleanup(paths, hours):
        held.clear()
        return {"deleted": 2}

    monkeypatch.setattr(client, "job_cleanup", cleanup)
    # The statistics were never updated before the cleanup: the jobs are counted on the way out.
    assert test_client.post("/jobs/cleanup", data={"hours": "168"}, follow_redirects=False).status_code == 303
    row = _only(_embedded(test_client.get("/statistics").text)["jobs"])
    assert (row["triggered"], row["succeeded"], row["failed"]) == (2, 1, 1)


def test_the_page_has_the_calendar_and_the_six_plots(test_client, monkeypatch):
    _stub_sources(monkeypatch)
    text = test_client.get("/statistics").text
    assert 'id="stats-calendar"' in text and 'id="stats-project"' in text and 'id="stats-pillar"' in text
    for plot in ("job-count", "job-success", "policy-fail", "job-duration", "incidents", "test-success"):
        assert f'data-plot="{plot}"' in text
    assert "/static/statistics.js?v=" in text
    assert json.loads(re.search(r'id="stats-pillars">(.*?)</script>', text, re.S).group(1))["policy"] == app_module.PILLAR_NAMES["policy"]
    assert 'class="subnav-link is-active"' in text and 'href="/statistics"' in text


# --- REQ-000000315: Documentation groups Review and Report in the toolbar ---

def test_documentation_groups_review_and_report(test_client, monkeypatch):
    _stub_sources(monkeypatch)
    text = test_client.get("/statistics").text
    bar = text[text.index('<nav class="tabbar"'):]
    row = re.search(r'data-view="documentation">(.*?)</div>', bar, re.S).group(1)
    assert row.index('href="/docs"') < row.index('href="/report"')
    assert "<span>Review</span>" in row and "<span>Report</span>" in row
    main_row = re.search(r'data-view="main"(.*?)</div>', bar, re.S).group(1)
    assert "<span>Documentation</span>" in main_row and "<span>Report</span>" not in main_row
    actions_row = re.search(r'data-view="actions">(.*?)</div>', bar, re.S).group(1)
    assert 'href="/docs"' not in actions_row and "Docs" not in actions_row


def test_review_and_report_open_the_documentation_group(test_client, monkeypatch):
    monkeypatch.setattr(client, "list_projects", lambda paths, status=None: [])
    for page in ("/docs", "/report"):
        text = test_client.get(page).text
        assert re.search(r'class="tabbar-row tabbar-row-active" data-view="documentation"', text), page
        assert '<nav class="subnav" aria-label="Documentation">' in text, page
