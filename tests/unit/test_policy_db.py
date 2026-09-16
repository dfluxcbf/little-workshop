import pytest

from policy import db
from policy.models import CheckOutcome, CreatedBy, Policy, PolicyCheckResult, PolicyWaiver, ScopeLevel


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


def test_new_policy_defaults_to_enabled_with_no_gate_points(conn):
    db.insert_policy(conn, _policy())
    fetched = db.get_policy(conn, "p1")
    assert fetched.enabled is True
    assert fetched.gate_points == []


def test_insert_policy_persists_gate_points_and_disabled(conn):
    from policy.models import GatePoint

    db.insert_policy(conn, _policy(enabled=False, gate_points=[GatePoint.SERVER_LAUNCH, GatePoint.RELEASE]))
    fetched = db.get_policy(conn, "p1")
    assert fetched.enabled is False
    assert set(fetched.gate_points) == {GatePoint.SERVER_LAUNCH, GatePoint.RELEASE}


def test_set_policy_configuration_updates_enabled_only(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_configuration(conn, "p1", enabled=False)
    fetched = db.get_policy(conn, "p1")
    assert fetched.enabled is False
    assert fetched.gate_points == []  # untouched


def test_set_policy_configuration_updates_gate_points_only(conn):
    from policy.models import GatePoint

    db.insert_policy(conn, _policy())
    db.set_policy_configuration(conn, "p1", gate_points=[GatePoint.BUILD_INSTALL])
    fetched = db.get_policy(conn, "p1")
    assert fetched.enabled is True  # untouched
    assert fetched.gate_points == [GatePoint.BUILD_INSTALL]


def test_set_policy_configuration_never_touches_ears_text_or_script(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_configuration(conn, "p1", enabled=False)
    fetched = db.get_policy(conn, "p1")
    assert fetched.ears_text == "the system shall x"
    assert fetched.test_script == "def test_x():\n    assert True\n"


def test_set_policy_configuration_is_a_noop_for_unknown_policy(conn):
    db.set_policy_configuration(conn, "ghost", enabled=False)  # must not raise


def test_insert_and_get_waiver_round_trips(conn):
    db.insert_policy(conn, _policy())
    db.insert_waiver(conn, PolicyWaiver(id="w1", policy_id="p1", project_id="proj-a", reason="can't comply"))
    fetched = db.get_waiver(conn, "w1")
    assert fetched.policy_id == "p1"
    assert fetched.project_id == "proj-a"
    assert fetched.reason == "can't comply"


def test_get_waiver_returns_none_for_unknown_id(conn):
    assert db.get_waiver(conn, "ghost") is None


def test_find_waiver_matches_policy_and_project(conn):
    db.insert_policy(conn, _policy())
    db.insert_waiver(conn, PolicyWaiver(id="w1", policy_id="p1", project_id="proj-a", reason="x"))
    assert db.find_waiver(conn, policy_id="p1", project_id="proj-a").id == "w1"
    assert db.find_waiver(conn, policy_id="p1", project_id="proj-b") is None


def test_list_waivers_filters_by_policy_and_project(conn):
    db.insert_policy(conn, _policy(id="p1"))
    db.insert_policy(conn, _policy(id="p2"))
    db.insert_waiver(conn, PolicyWaiver(id="w1", policy_id="p1", project_id="proj-a", reason="x"))
    db.insert_waiver(conn, PolicyWaiver(id="w2", policy_id="p2", project_id="proj-a", reason="y"))
    db.insert_waiver(conn, PolicyWaiver(id="w3", policy_id="p1", project_id="proj-b", reason="z"))

    assert {w.id for w in db.list_waivers(conn, policy_id="p1")} == {"w1", "w3"}
    assert {w.id for w in db.list_waivers(conn, project_id="proj-a")} == {"w1", "w2"}
    assert len(db.list_waivers(conn)) == 3


def test_delete_waiver_removes_it(conn):
    db.insert_policy(conn, _policy())
    db.insert_waiver(conn, PolicyWaiver(id="w1", policy_id="p1", project_id="proj-a", reason="x"))
    db.delete_waiver(conn, "w1")
    assert db.get_waiver(conn, "w1") is None


def test_delete_policy_cascades_waivers_too(conn):
    db.insert_policy(conn, _policy())
    db.insert_waiver(conn, PolicyWaiver(id="w1", policy_id="p1", project_id="proj-a", reason="x"))
    db.delete_policy(conn, "p1")
    assert db.list_waivers(conn, policy_id="p1") == []
