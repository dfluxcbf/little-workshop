import pytest

from policy import db
from policy.models import CreatedBy, Policy, PolicyProject, ScopeLevel
from policy.precedence import gating_enabled_for, resolve_winning_policy


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "policy.db")
    yield connection
    connection.close()


def _add(conn, projects=("proj-a",), **overrides):
    defaults = dict(
        id="p1", topic="t", target_pillar="deployment", ears_text="shall x",
        test_script="def test_x(): pass", scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.USER,
    )
    defaults.update(overrides)
    policy = Policy(**defaults)
    db.insert_policy(conn, policy)
    db.set_policy_projects(conn, policy.id, [
        p if isinstance(p, PolicyProject) else PolicyProject(project_id=p) for p in projects
    ])
    return policy


def test_no_policies_on_topic_resolves_to_none(conn):
    assert resolve_winning_policy(conn, topic="t", project_id="p") is None


def test_a_policy_applies_only_to_the_projects_selected_for_it(conn):
    _add(conn, projects=["proj-a"])
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p1"
    assert resolve_winning_policy(conn, topic="t", project_id="proj-b") is None


def test_deselecting_a_project_stops_the_policy_applying_to_it(conn):
    _add(conn, projects=["proj-a", "proj-b"])
    db.set_policy_projects(conn, "p1", [PolicyProject(project_id="proj-a")])
    assert resolve_winning_policy(conn, topic="t", project_id="proj-b") is None


def test_narrower_authoring_scope_beats_broader_among_selected_policies(conn):
    _add(conn, id="p1", scope_level=ScopeLevel.ALL_PROJECTS)
    _add(conn, id="p2", scope_level=ScopeLevel.PROJECT_SET, scope_target="household")
    _add(conn, id="p3", scope_level=ScopeLevel.PROJECT, scope_target="proj-a")
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p3"

    db.set_policy_projects(conn, "p3", [])
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p2"

    db.set_policy_projects(conn, "p2", [])
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p1"


def test_framework_policy_always_wins_when_selected(conn):
    _add(conn, id="p1", scope_level=ScopeLevel.PROJECT, scope_target="proj-a", created_by=CreatedBy.USER)
    _add(conn, id="p2", scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.FRAMEWORK)
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p2"


def test_a_disabled_policy_is_skipped_and_falls_through(conn):
    _add(conn, id="p1", scope_level=ScopeLevel.ALL_PROJECTS)
    _add(conn, id="p2", scope_level=ScopeLevel.PROJECT, scope_target="proj-a")
    db.set_policy_configuration(conn, "p2", enabled=False)
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a").id == "p1"


def test_only_policies_on_the_requested_topic_are_considered(conn):
    _add(conn, id="p1", topic="other")
    assert resolve_winning_policy(conn, topic="t", project_id="proj-a") is None


def test_gating_is_per_project_and_independent_of_selection_order(conn):
    policy = _add(conn, projects=[PolicyProject(project_id="proj-a"), PolicyProject(project_id="proj-b", gating_enabled=False)])
    policy = db.get_policy(conn, policy.id)
    assert gating_enabled_for(policy, "proj-a") is True
    assert gating_enabled_for(policy, "proj-b") is False
    assert gating_enabled_for(policy, "proj-c") is False  # not selected: nothing to gate
    # a project with gating off still wins resolution — it is still checked
    assert resolve_winning_policy(conn, topic="t", project_id="proj-b").id == "p1"
