"""Requirement conflict detection (requirements/conflicts.py) and its record in the pillar."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from common.paths import Paths
from requirements import conflicts, db
from requirements.app import build_app
from requirements.models import Requirement

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _req(key, description, *, n=0, project="p1", title=None):
    return Requirement(id=f"id-{key}-{n}", project_id=project, key=key, title=title or key, description=description,
                       created_at=T0 + timedelta(days=n), updated_at=T0 + timedelta(days=n))


# --- REQ-000000473: Requirement conflicts are found by code once a project's requirements have settled, and resolved by a Claude task the user directs ---

def test_statements_read_conditions_system_polarity_and_each_and_the_clause():
    found = conflicts.statements(
        "When the user taps Redeploy, the Dashboard shall submit one job, and the pillar shall not restart. "
        "Not a requirement sentence.")
    assert [(s.system, s.negative, s.response) for s in found] == [
        (("dashboard",), False, ("submit", "one", "job")), (("pillar",), True, ("restart",))]
    assert found[0].conditions == found[1].conditions == frozenset({("when", ("user", "taps", "redeploy"))})


def test_a_requirement_forbidding_what_another_requires_is_a_contradiction():
    older = _req("REQ-1", "When a job fails, the Dashboard shall show a banner.", n=0)
    newer = _req("REQ-2", "When a job fails, the Dashboard shall not show a banner.", n=1)
    [c] = conflicts.find_conflicts([newer, older])
    assert c.kind == conflicts.CONTRADICTION and (c.older.key, c.newer.key) == ("REQ-1", "REQ-2")
    assert c.older_text == "When a job fails, the Dashboard shall show a banner"


def test_the_same_rule_with_different_numbers_is_a_different_value():
    a = _req("REQ-1", "The pillar shall answer within 5 seconds.", n=0)
    b = _req("REQ-2", "The pillar shall answer within 10 seconds.", n=1)
    [c] = conflicts.find_conflicts([a, b])
    assert c.kind == conflicts.DIFFERENT_VALUE and "5 against 10" in c.summary


def test_each_later_copy_of_a_key_conflicts_with_the_first():
    copies = [_req("REQ-1", "The pillar shall start.", n=i) for i in range(3)]
    found = conflicts.find_conflicts(copies)
    assert [(c.kind, c.older.id, c.newer.id) for c in found] == [
        (conflicts.DUPLICATE_KEY, "id-REQ-1-0", "id-REQ-1-1"), (conflicts.DUPLICATE_KEY, "id-REQ-1-0", "id-REQ-1-2")]


@pytest.mark.parametrize("a, b", [
    ("When a job fails, the Dashboard shall show a banner.", "When a job succeeds, the Dashboard shall not show a banner."),
    ("The Dashboard shall show a banner.", "The CLI shall not show a banner."),
    ("The pillar shall answer within 5 seconds.", "The pillar shall answer within 5 seconds and log it."),
    ("The pillar shall answer within 5 seconds.", "The pillar shall answer within 5 seconds."),
    ("Prose that is not EARS.", "More prose that is not EARS."),
])
def test_different_conditions_systems_or_responses_do_not_conflict(a, b):
    assert conflicts.find_conflicts([_req("REQ-1", a, n=0), _req("REQ-2", b, n=1)]) == []


def test_projects_are_never_compared_with_each_other():
    assert conflicts.find_conflicts([
        _req("REQ-1", "The Dashboard shall show a banner.", project="a"),
        _req("REQ-1", "The Dashboard shall not show a banner.", project="b", n=1),
    ]) == []


def test_the_analysis_is_deterministic():
    reqs = [_req("REQ-1", "The Dashboard shall show a banner.", n=0), _req("REQ-2", "The Dashboard shall never show a banner.", n=1),
            _req("REQ-3", "The pillar shall wait 3 seconds.", n=2), _req("REQ-4", "The pillar shall wait 4 seconds.", n=3)]
    first = conflicts.find_conflicts(reqs)
    assert len(first) == 2 and conflicts.find_conflicts(list(reversed(reqs))) == first


@pytest.fixture
def client(tmp_path):
    conn = db.connect(tmp_path / "requirements.db")
    yield TestClient(build_app(conn, Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")))
    conn.close()


def _job(client, job_type, params, project_id="p1"):
    return client.post(f"/v1/jobs/{job_type}", json={"job_id": "j", "project_id": project_id, "params": params}).json()


def _create(client, key, description):
    return _job(client, "requirements.create_requirement", {"key": key, "title": key, "description": description})["result"]


def test_an_analysis_records_open_conflicts_and_resolves_those_no_longer_found(client):
    _create(client, "REQ-1", "The Dashboard shall show a banner.")
    newer = _create(client, "REQ-2", "The Dashboard shall not show a banner.")
    analysis = _job(client, "requirements.analyze_conflicts", {})
    assert analysis["result"]["projects"]["p1"] == {"found": 1, "new": 1, "reopened": 0, "resolved": 0}
    [c] = client.get("/v1/conflicts", params={"status": "open"}).json()
    detail = client.get(f"/v1/conflicts/{c['id']}").json()
    assert detail["older"]["key"] == "REQ-1" and detail["newer"]["key"] == "REQ-2" and detail["kind"] == "contradiction"

    version = client.get("/v1/conflicts/version").json()["version"]
    assert _job(client, "requirements.analyze_conflicts", {})["result"]["projects"]["p1"]["new"] == 0  # same id again

    _job(client, "requirements.delete_requirement", {"requirement_id": newer["id"]})
    assert _job(client, "requirements.analyze_conflicts", {})["result"]["projects"]["p1"]["resolved"] == 1
    assert client.get(f"/v1/conflicts/{c['id']}").json()["status"] == "resolved"
    assert client.get("/v1/conflicts/version").json()["version"] != version


def test_a_decision_marks_the_conflict_resolving_and_needs_guidance_for_another_approach(client):
    _create(client, "REQ-1", "The pillar shall wait 3 seconds.")
    _create(client, "REQ-2", "The pillar shall wait 4 seconds.")
    _job(client, "requirements.analyze_conflicts", {}, project_id=None)
    [c] = client.get("/v1/conflicts").json()
    refused = _job(client, "requirements.decide_conflict", {"conflict_id": c["id"], "decision": "other", "guidance": " "})
    assert refused["outcome"] == "failed" and "guidance" in refused["error"]
    assert _job(client, "requirements.decide_conflict", {"conflict_id": c["id"], "decision": "maybe"})["outcome"] == "failed"
    decided = _job(client, "requirements.decide_conflict", {"conflict_id": c["id"], "decision": "newer_wins", "job_id": "fix"})
    assert decided["result"]["status"] == "resolving" and decided["result"]["job_id"] == "fix"
    _job(client, "requirements.analyze_conflicts", {})
    assert client.get(f"/v1/conflicts/{c['id']}").json()["status"] == "resolving"  # still found, still being fixed


def test_unknown_conflicts_and_statuses_are_refused(client):
    assert client.get("/v1/conflicts/ghost").status_code == 404
    assert client.get("/v1/conflicts", params={"status": "nope"}).status_code == 422


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def settled(tmp_path, monkeypatch):
    """A database whose writes are stamped by a clock the test moves, and the analyses it records."""
    clock = _Clock(T0)
    monkeypatch.setattr(db, "_clock", clock)
    conn = db.connect(tmp_path / "requirements.db")
    analyzed = []
    from requirements import app as requirements_app
    real = requirements_app.analyze_conflicts
    monkeypatch.setattr(requirements_app, "analyze_conflicts",
                        lambda c, lock, project_id=None, only=None: analyzed.append(project_id) or real(c, lock, project_id))

    def run():
        import threading
        return requirements_app.analyze_settled(conn, threading.Lock(), now=clock.now)
    return conn, clock, analyzed, run


def test_a_burst_of_edits_is_analyzed_once_fifteen_minutes_after_the_last(settled):
    conn, clock, analyzed, run = settled
    db.insert_requirement(conn, _req("REQ-1", "The Dashboard shall show a banner."))
    clock.now = T0 + timedelta(minutes=5)
    db.insert_requirement(conn, _req("REQ-2", "The Dashboard shall not show a banner.", n=1))
    for minutes in (15, 19):  # 15 min after the first edit, 14 after the second: not settled yet
        clock.now = T0 + timedelta(minutes=minutes)
        assert run() == []
    clock.now = T0 + timedelta(minutes=20)
    assert run() == ["p1"] and analyzed == ["p1"]
    assert len(db.list_conflicts(conn, project_id="p1")) == 1
    clock.now = T0 + timedelta(hours=2)
    assert run() == [] and analyzed == ["p1"]


def test_no_change_means_no_analysis(settled):
    conn, clock, analyzed, run = settled
    clock.now = T0 + timedelta(days=1)
    assert run() == [] and analyzed == []


def test_an_edit_during_the_analysis_keeps_the_project_due(settled):
    conn, clock, analyzed, run = settled
    db.insert_requirement(conn, _req("REQ-1", "The Dashboard shall show a banner."))
    [(project, stamp)] = db.conflict_scans_due(conn, settled_before=T0)
    clock.now = T0 + timedelta(minutes=1)
    db.update_requirement(conn, db.get_requirement(conn, "id-REQ-1-0"))
    assert not db.clear_conflict_scan_due(conn, project, stamp)
    assert db.conflict_scans_due(conn, settled_before=clock.now) == [("p1", clock.now.isoformat())]


def test_deleting_a_requirement_makes_its_project_due_and_the_stamp_survives_a_restart(settled, tmp_path):
    conn, clock, analyzed, run = settled
    db.insert_requirement(conn, _req("REQ-1", "The Dashboard shall show a banner."))
    db.clear_conflict_scan_due(conn, "p1", T0.isoformat())
    db.delete_requirement(conn, "id-REQ-1-0")
    conn.close()
    reopened = db.connect(tmp_path / "requirements.db")
    assert db.conflict_scans_due(reopened, settled_before=T0) == [("p1", T0.isoformat())]


def test_an_import_makes_its_project_due_and_removing_the_project_clears_it(settled):
    from requirements import transfer
    conn, clock, analyzed, run = settled
    db.insert_requirement(conn, _req("REQ-1", "The Dashboard shall show a banner."))
    backup = transfer.export_project(conn, "p1")
    transfer.import_project(conn, backup, project_id="p2")
    assert [p for p, _ in db.conflict_scans_due(conn, settled_before=T0)] == ["p1", "p2"]
    transfer.delete_project(conn, "p2")
    assert [p for p, _ in db.conflict_scans_due(conn, settled_before=T0)] == ["p1"]


def test_a_project_no_longer_registered_is_cleared_without_an_analysis(settled):
    import threading
    from requirements import app as requirements_app
    conn, clock, analyzed, run = settled
    db.insert_requirement(conn, _req("REQ-1", "The Dashboard shall show a banner."))
    clock.now = T0 + timedelta(minutes=15)
    assert requirements_app.analyze_settled(conn, threading.Lock(), now=clock.now, registered={"other"}) == []
    assert analyzed == [] and db.conflict_scans_due(conn, settled_before=clock.now) == []
