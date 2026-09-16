import pytest

from policy import db
from policy.models import CheckOutcome, CreatedBy, Policy, PolicyCheckResult, ScopeLevel


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


def _policy(**overrides):
    defaults = dict(
        id="p1", topic="t", target_pillar="deployment", ears_text="the system shall x",
        test_script="def test_x():\n    assert True\n", scope_level=ScopeLevel.ALL_PROJECTS,
        created_by=CreatedBy.USER,
    )
    defaults.update(overrides)
    return Policy(**defaults)


def test_insert_and_get_policy_round_trips(conn):
    db.insert_policy(conn, _policy())
    fetched = db.get_policy(conn, "p1")
    assert fetched.topic == "t"
    assert fetched.scope_level == ScopeLevel.ALL_PROJECTS


def test_get_policy_returns_none_for_unknown_id(conn):
    assert db.get_policy(conn, "ghost") is None


def test_list_policies_filters_by_topic(conn):
    db.insert_policy(conn, _policy(id="p1", topic="a"))
    db.insert_policy(conn, _policy(id="p2", topic="b"))
    assert [p.id for p in db.list_policies(conn, topic="a")] == ["p1"]
    assert len(db.list_policies(conn)) == 2


def test_replace_policy_updates_in_place_and_bumps_amended_at(conn):
    original = _policy()
    db.insert_policy(conn, original)
    original_amended_at = original.amended_at

    edited = _policy(ears_text="the system shall y")
    db.replace_policy(conn, edited)

    fetched = db.get_policy(conn, "p1")
    assert fetched.ears_text == "the system shall y"
    assert fetched.amended_at >= original_amended_at


def test_find_by_scope_exact_match(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", scope_level=ScopeLevel.PROJECT, scope_target="proj-a"))
    found = db.find_by_scope(conn, topic="t", scope_level=ScopeLevel.PROJECT, scope_target="proj-a")
    assert found.id == "p1"


def test_find_by_scope_no_match_for_different_project(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", scope_level=ScopeLevel.PROJECT, scope_target="proj-a"))
    assert db.find_by_scope(conn, topic="t", scope_level=ScopeLevel.PROJECT, scope_target="proj-b") is None


def test_find_by_scope_matches_null_scope_target_for_all_projects(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", scope_level=ScopeLevel.ALL_PROJECTS, scope_target=None))
    found = db.find_by_scope(conn, topic="t", scope_level=ScopeLevel.ALL_PROJECTS, scope_target=None)
    assert found.id == "p1"


def test_find_by_scope_excludes_given_id(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", scope_level=ScopeLevel.ALL_PROJECTS))
    assert db.find_by_scope(conn, topic="t", scope_level=ScopeLevel.ALL_PROJECTS, scope_target=None, exclude_id="p1") is None


def test_find_topic_owner_conflict_detects_a_different_target_pillar(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", target_pillar="deployment"))
    conflict = db.find_topic_owner_conflict(conn, topic="t", target_pillar="agent")
    assert conflict.id == "p1"


def test_find_topic_owner_conflict_none_when_same_target_pillar(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", target_pillar="deployment"))
    assert db.find_topic_owner_conflict(conn, topic="t", target_pillar="deployment") is None


def test_find_topic_owner_conflict_excludes_given_id(conn):
    db.insert_policy(conn, _policy(id="p1", topic="t", target_pillar="deployment"))
    assert db.find_topic_owner_conflict(conn, topic="t", target_pillar="agent", exclude_id="p1") is None


def test_delete_policy_cascades_check_results(conn):
    db.insert_policy(conn, _policy())
    db.insert_check_result(conn, PolicyCheckResult(id="r1", policy_id="p1", project_id="proj-a", outcome=CheckOutcome.PASS))

    db.delete_policy(conn, "p1")

    assert db.get_policy(conn, "p1") is None
    assert db.list_check_results(conn, policy_id="p1") == []


def test_insert_and_list_check_results(conn):
    db.insert_policy(conn, _policy())
    db.insert_check_result(conn, PolicyCheckResult(id="r1", policy_id="p1", project_id="proj-a", outcome=CheckOutcome.PASS))
    db.insert_check_result(conn, PolicyCheckResult(id="r2", policy_id="p1", project_id="proj-b", outcome=CheckOutcome.FAIL))

    assert len(db.list_check_results(conn, policy_id="p1")) == 2
    assert [r.id for r in db.list_check_results(conn, project_id="proj-a")] == ["r1"]


def test_latest_check_result_returns_none_when_no_results(conn):
    assert db.latest_check_result(conn, policy_id="p1", project_id="proj-a") is None


def test_latest_check_result_returns_the_most_recent(conn):
    from datetime import datetime, timezone

    db.insert_policy(conn, _policy())
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new = datetime(2020, 1, 2, tzinfo=timezone.utc)
    db.insert_check_result(conn, PolicyCheckResult(id="r1", policy_id="p1", project_id="proj-a", outcome=CheckOutcome.FAIL, checked_at=old))
    db.insert_check_result(conn, PolicyCheckResult(id="r2", policy_id="p1", project_id="proj-a", outcome=CheckOutcome.PASS, checked_at=new))

    latest = db.latest_check_result(conn, policy_id="p1", project_id="proj-a")
    assert latest.id == "r2"


def test_new_id_is_unique(conn):
    assert db.new_id() != db.new_id()
