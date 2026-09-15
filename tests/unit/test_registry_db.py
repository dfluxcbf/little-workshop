import pytest

from registry import db
from registry.models import Project, ProjectSet, ProjectStatus, Ticket, TicketStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    yield connection
    connection.close()


def test_insert_and_get_project_round_trips(conn):
    project = Project(name="little-widget", path="/tmp/little-widget", repo_url=None)
    db.insert_project(conn, project)

    fetched = db.get_project(conn, "little-widget")
    assert fetched.path == "/tmp/little-widget"
    assert fetched.status == ProjectStatus.ACTIVE


def test_list_projects_filters_by_status(conn):
    db.insert_project(conn, Project(name="a", path="/a"))
    db.insert_project(conn, Project(name="b", path="/b", status=ProjectStatus.REMOVED))

    active = db.list_projects(conn, status=ProjectStatus.ACTIVE)
    assert [p.name for p in active] == ["a"]


def test_delete_project_cascades_sets_and_tickets(conn):
    db.insert_project(conn, Project(name="a", path="/a"))
    db.create_project_set(conn, ProjectSet(name="household", project_names=["a"]))
    db.insert_ticket(conn, Ticket(id="t1", project_name="a", title="fix the thing"))

    db.delete_project(conn, "a")

    assert db.get_project(conn, "a") is None
    assert db.get_project_set(conn, "household").project_names == []
    assert db.list_tickets(conn, project_name="a") == []


def test_project_set_membership_add_and_remove(conn):
    db.insert_project(conn, Project(name="a", path="/a"))
    db.insert_project(conn, Project(name="b", path="/b"))
    db.create_project_set(conn, ProjectSet(name="household", project_names=["a"]))

    db.add_to_project_set(conn, "household", "b")
    assert set(db.get_project_set(conn, "household").project_names) == {"a", "b"}

    db.remove_from_project_set(conn, "household", "a")
    assert db.get_project_set(conn, "household").project_names == ["b"]


def test_list_project_sets(conn):
    db.create_project_set(conn, ProjectSet(name="household"))
    db.create_project_set(conn, ProjectSet(name="work"))
    names = [s.name for s in db.list_project_sets(conn)]
    assert names == ["household", "work"]


def test_ticket_status_update(conn):
    db.insert_project(conn, Project(name="a", path="/a"))
    ticket_id = db.new_ticket_id()
    db.insert_ticket(conn, Ticket(id=ticket_id, project_name="a", title="fix it"))

    db.update_ticket_status(conn, ticket_id, TicketStatus.CLOSED)
    assert db.get_ticket(conn, ticket_id).status == TicketStatus.CLOSED
