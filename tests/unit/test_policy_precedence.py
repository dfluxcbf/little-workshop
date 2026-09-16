import pytest

from policy import db
from policy.models import CreatedBy, Policy, ScopeLevel
from policy.precedence import resolve_winning_policy


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


def _policy(**overrides):
    defaults = dict(
        id="p1", topic="t", target_pillar="deployment", ears_text="shall x",
        test_script="def test_x(): pass", scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.USER,
    )
    defaults.update(overrides)
    return Policy(**defaults)


def test_no_policies_on_topic_resolves_to_none(conn):
    assert resolve_winning_policy(conn, topic="t", project_id="p", project_sets_for_project=lambda p: []) is None


def test_all_projects_policy_wins_when_nothing_more_specific(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.ALL_PROJECTS))
    winner = resolve_winning_policy(conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: [])
    assert winner.id == "p1"


def test_project_specific_beats_all_projects(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.ALL_PROJECTS))
    db.insert_policy(conn, _policy(id="p2", scope_level=ScopeLevel.PROJECT, scope_target="proj-a"))
    winner = resolve_winning_policy(conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: [])
    assert winner.id == "p2"
    # a different project still gets the all-projects one
    other = resolve_winning_policy(conn, topic="t", project_id="proj-b", project_sets_for_project=lambda p: [])
    assert other.id == "p1"


def test_project_set_beats_all_projects_but_loses_to_project_specific(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.ALL_PROJECTS))
    db.insert_policy(conn, _policy(id="p2", scope_level=ScopeLevel.PROJECT_SET, scope_target="household"))
    db.insert_policy(conn, _policy(id="p3", scope_level=ScopeLevel.PROJECT, scope_target="proj-a"))

    winner = resolve_winning_policy(conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: ["household"])
    assert winner.id == "p3"  # project-specific still wins even though it's also in the set

    winner_b = resolve_winning_policy(conn, topic="t", project_id="proj-b", project_sets_for_project=lambda p: ["household"])
    assert winner_b.id == "p2"  # no project-specific policy for proj-b, falls to the set


def test_framework_policy_always_wins(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.PROJECT, scope_target="proj-a", created_by=CreatedBy.USER))
    db.insert_policy(conn, _policy(id="p2", scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.FRAMEWORK))

    winner = resolve_winning_policy(conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: [])
    assert winner.id == "p2"


def test_project_not_in_any_matching_set_falls_through_to_all_projects(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.ALL_PROJECTS))
    db.insert_policy(conn, _policy(id="p2", scope_level=ScopeLevel.PROJECT_SET, scope_target="other-set"))

    winner = resolve_winning_policy(conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: ["household"])
    assert winner.id == "p1"


def test_multiple_matching_project_sets_resolve_deterministically(conn):
    db.insert_policy(conn, _policy(id="p1", scope_level=ScopeLevel.PROJECT_SET, scope_target="zzz-set"))
    db.insert_policy(conn, _policy(id="p2", scope_level=ScopeLevel.PROJECT_SET, scope_target="aaa-set"))

    winner = resolve_winning_policy(
        conn, topic="t", project_id="proj-a", project_sets_for_project=lambda p: ["zzz-set", "aaa-set"]
    )
    assert winner.id == "p2"  # lowest scope_target, deterministic


def test_different_topics_never_interfere(conn):
    db.insert_policy(conn, _policy(id="p1", topic="topic-a", scope_level=ScopeLevel.ALL_PROJECTS))
    winner = resolve_winning_policy(conn, topic="topic-b", project_id="proj-a", project_sets_for_project=lambda p: [])
    assert winner is None
