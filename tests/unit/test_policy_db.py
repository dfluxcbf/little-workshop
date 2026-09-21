import pytest

from policy import db
from policy.models import CheckOutcome, CreatedBy, Policy, PolicyCheckResult, PolicyProject, ScopeLevel


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


def test_gate_point_has_all_ten_2026_09_16_categories():
    from policy.models import GatePoint

    assert {p.value for p in GatePoint} == {
        "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
        "release", "package_publish", "branch_finish", "project_removal", "project_registration",
    }


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


def test_a_new_policy_has_no_projects_and_is_unseeded(conn):
    db.insert_policy(conn, _policy())
    assert db.get_policy(conn, "p1").projects == []
    assert [p.id for p in db.unseeded_policies(conn)] == ["p1"]


def test_set_policy_projects_round_trips_with_per_project_gating(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-b", gating_enabled=False), PolicyProject(project_id="proj-a")])
    projects = db.get_policy(conn, "p1").projects
    assert [(p.project_id, p.gating_enabled) for p in projects] == [("proj-a", True), ("proj-b", False)]
    assert db.unseeded_policies(conn) == []


def test_set_policy_projects_replaces_the_whole_selection(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-a"), PolicyProject(project_id="proj-b")])
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-b", gating_enabled=False)])
    assert [(p.project_id, p.gating_enabled) for p in db.get_policy(conn, "p1").projects] == [("proj-b", False)]


def test_deselecting_every_project_is_a_real_choice_not_unseeded(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-a")])
    db.set_policy_projects(conn, "p1", [])
    assert db.get_policy(conn, "p1").projects == []
    assert db.unseeded_policies(conn) == []


def test_find_and_list_policy_projects_filter_by_policy_and_project(conn):
    db.insert_policy(conn, _policy(id="p1"))
    db.insert_policy(conn, _policy(id="p2"))
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-a"), PolicyProject(project_id="proj-b", gating_enabled=False)])
    db.set_policy_projects(conn, "p2", [PolicyProject(project_id="proj-a")])

    assert db.find_policy_project(conn, policy_id="p1", project_id="proj-b").gating_enabled is False
    assert db.find_policy_project(conn, policy_id="p1", project_id="proj-c") is None
    assert {(r["policy_id"], r["project_id"]) for r in db.list_policy_projects(conn, project_id="proj-a")} == {("p1", "proj-a"), ("p2", "proj-a")}
    assert len(db.list_policy_projects(conn, policy_id="p1")) == 2
    assert len(db.list_policy_projects(conn)) == 3


def test_delete_policy_removes_its_project_selection_too(conn):
    db.insert_policy(conn, _policy())
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-a")])
    db.delete_policy(conn, "p1")
    assert db.list_policy_projects(conn, policy_id="p1") == []
    assert conn.execute("SELECT COUNT(*) FROM policy_seeded").fetchone()[0] == 0


def test_connect_drops_an_empty_legacy_waivers_table_but_keeps_one_with_rows(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE policy_waivers (id TEXT, policy_id TEXT, project_id TEXT, reason TEXT, created_at TEXT)")
    raw.commit(); raw.close()
    conn = db.connect(path)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='policy_waivers'").fetchone() is None
    conn.close()

    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE policy_waivers (id TEXT, policy_id TEXT, project_id TEXT, reason TEXT, created_at TEXT)")
    raw.execute("INSERT INTO policy_waivers VALUES ('w1','p1','proj-a','recorded','t')")
    raw.commit(); raw.close()
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM policy_waivers").fetchone()[0] == 1
    conn.close()


# -- drafts ---------------------------------------------------------------------


def _draft(**overrides):
    from policy.models import PolicyDraft

    defaults = dict(id="d1", topic="version-format", target_pillar="version", ears_text="x shall y", test_script="def test_x(): pass\n")
    defaults.update(overrides)
    return PolicyDraft(**defaults)


def test_insert_and_get_draft_round_trips(conn):
    from policy.models import GatePoint

    db.insert_draft(conn, _draft(source="little-versions", notes="n", concerns=True, ears_refined=True, gate_points=[GatePoint.RELEASE]))
    fetched = db.get_draft(conn, "d1")
    assert fetched.topic == "version-format" and fetched.source == "little-versions" and fetched.notes == "n"
    assert fetched.concerns is True and fetched.ears_refined is True and fetched.gate_points == [GatePoint.RELEASE]
    assert fetched.status.value == "pending" and fetched.decided_at is None and fetched.policy_id is None
    assert db.get_draft(conn, "ghost") is None


def test_list_drafts_filters_by_status(conn):
    from policy.models import DraftStatus

    db.insert_draft(conn, _draft(id="d1", topic="a"))
    db.insert_draft(conn, _draft(id="d2", topic="b"))
    db.set_draft_decision(conn, "d2", status=DraftStatus.REJECTED, reason="no")
    assert [d.id for d in db.list_drafts(conn)] == ["d1", "d2"]
    assert [d.id for d in db.list_drafts(conn, status=DraftStatus.PENDING)] == ["d1"]
    rejected = db.list_drafts(conn, status=DraftStatus.REJECTED)[0]
    assert rejected.reject_reason == "no" and rejected.decided_at is not None


def test_a_decision_records_the_policy_and_restoring_clears_the_decision_time(conn):
    from policy.models import DraftStatus

    db.insert_draft(conn, _draft())
    db.set_draft_decision(conn, "d1", status=DraftStatus.APPROVED, policy_id="pol9")
    assert (db.get_draft(conn, "d1").status, db.get_draft(conn, "d1").policy_id) == (DraftStatus.APPROVED, "pol9")
    db.set_draft_decision(conn, "d1", status=DraftStatus.PENDING)
    restored = db.get_draft(conn, "d1")
    assert restored.status == DraftStatus.PENDING and restored.decided_at is None


def test_find_open_draft_by_topic_ignores_decided_drafts_and_replace_keeps_status(conn):
    from policy.models import DraftStatus

    db.insert_draft(conn, _draft(id="old", topic="t"))
    db.set_draft_decision(conn, "old", status=DraftStatus.REJECTED)
    assert db.find_open_draft_by_topic(conn, "t") is None
    db.insert_draft(conn, _draft(id="new", topic="t"))
    assert db.find_open_draft_by_topic(conn, "t").id == "new"

    db.replace_draft_content(conn, _draft(id="new", topic="t", ears_text="reworded", notes="fresh"))
    refreshed = db.get_draft(conn, "new")
    assert refreshed.ears_text == "reworded" and refreshed.notes == "fresh" and refreshed.status == DraftStatus.PENDING
