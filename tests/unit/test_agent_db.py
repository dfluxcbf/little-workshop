"""agent.db — the agent_runs/remote_control_sessions/poll_cursors schema.
Real sqlite throughout, matching every other pillar's own db tests."""

import pytest

from agent import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


def test_update_run_with_no_fields_is_a_noop(conn):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    before = db.get_run(conn, run_id)
    db.update_run(conn, run_id)  # no kwargs
    after = db.get_run(conn, run_id)
    assert before == after


def test_list_runs_without_status_filter_returns_all(conn):
    r1, r2 = db.new_run_id(), db.new_run_id()
    db.create_run(conn, r1, job_id=None, project_id="p", intent="task", review_mode="manual", title="a", source={})
    db.create_run(conn, r2, job_id=None, project_id="p", intent="task", review_mode="manual", title="b", source={})
    db.update_run(conn, r2, status=db.FINISHED)
    runs = db.list_runs(conn, "p")
    assert {r["id"] for r in runs} == {r1, r2}


def test_list_runs_with_status_filter(conn):
    r1, r2 = db.new_run_id(), db.new_run_id()
    db.create_run(conn, r1, job_id=None, project_id="p", intent="task", review_mode="manual", title="a", source={})
    db.create_run(conn, r2, job_id=None, project_id="p", intent="task", review_mode="manual", title="b", source={})
    db.update_run(conn, r2, status=db.FINISHED)
    runs = db.list_runs(conn, "p", status=db.FINISHED)
    assert [r["id"] for r in runs] == [r2]


def test_list_runs_only_returns_matching_project(conn):
    r1 = db.new_run_id()
    db.create_run(conn, r1, job_id=None, project_id="p1", intent="task", review_mode="manual", title="a", source={})
    db.create_run(conn, db.new_run_id(), job_id=None, project_id="p2", intent="task", review_mode="manual", title="b", source={})
    runs = db.list_runs(conn, "p1")
    assert [r["id"] for r in runs] == [r1]


def test_record_session_start_returns_session_id_and_persists_row(conn):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    session_id = db.record_session_start(conn, run_id, "p", "lws-p-run-abcd1234")
    row = conn.execute("SELECT * FROM remote_control_sessions WHERE id = ?", (session_id,)).fetchone()
    assert row["session_name"] == "lws-p-run-abcd1234"
    assert row["stopped_at"] is None


def test_record_session_stop_sets_stopped_at(conn):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    session_id = db.record_session_start(conn, run_id, "p", "lws-p-run-abcd1234")
    db.record_session_stop(conn, session_id)
    row = conn.execute("SELECT * FROM remote_control_sessions WHERE id = ?", (session_id,)).fetchone()
    assert row["stopped_at"] is not None


def test_get_poll_cursor_defaults_when_no_row(conn):
    cursor = db.get_poll_cursor(conn, "p")
    assert cursor == {"labeled": [], "assigned": [], "comment_cursor": {}}


def test_save_and_get_poll_cursor_round_trips(conn):
    db.save_poll_cursor(conn, "p", labeled=[1, 2], assigned=[3], comment_cursor={"1": "2026-01-01"})
    cursor = db.get_poll_cursor(conn, "p")
    assert cursor == {"labeled": [1, 2], "assigned": [3], "comment_cursor": {"1": "2026-01-01"}}


def test_save_poll_cursor_upserts_existing_row(conn):
    db.save_poll_cursor(conn, "p", labeled=[1], assigned=[], comment_cursor={})
    db.save_poll_cursor(conn, "p", labeled=[1, 2], assigned=[5], comment_cursor={"9": "x"})
    cursor = db.get_poll_cursor(conn, "p")
    assert cursor["labeled"] == [1, 2]
    assert cursor["assigned"] == [5]
    rows = conn.execute("SELECT COUNT(*) as c FROM poll_cursors WHERE project_id = ?", ("p",)).fetchone()
    assert rows["c"] == 1


def test_get_run_returns_none_for_unknown_id(conn):
    assert db.get_run(conn, "nonexistent") is None


def test_create_run_stores_source_json(conn):
    run_id = db.new_run_id()
    db.create_run(
        conn, run_id, job_id="j1", project_id="p", intent="bug", review_mode="auto_approve", title="t",
        source={"type": "github_issue", "number": 3},
    )
    run = db.get_run(conn, run_id)
    assert run["status"] == db.RUNNING
    assert run["source_json"] == '{"type": "github_issue", "number": 3}'


# -- incidents --------------------------------------------------------------------------


def test_create_and_get_incident_round_trips(conn):
    incident_id = db.new_incident_id()
    db.create_incident(
        conn, incident_id, project_id="p", pillar="agent", context="POST /x",
        exception_type="ValueError", message="boom", traceback="tb...",
    )
    incident = db.get_incident(conn, incident_id)
    assert incident["status"] == "open" and incident["project_id"] == "p" and incident["run_id"] is None


def test_list_incidents_filters_and_orders_newest_first(conn):
    first = db.new_incident_id()
    db.create_incident(conn, first, project_id="p", pillar="agent", context="", exception_type="A", message="", traceback="")
    second = db.new_incident_id()
    db.create_incident(conn, second, project_id="p", pillar="agent", context="", exception_type="B", message="", traceback="")
    db.decide_incident(conn, first, status="dismissed")
    assert [i["id"] for i in db.list_incidents(conn)] == [second, first]
    assert [i["id"] for i in db.list_incidents(conn, status="open")] == [second]
    assert [i["id"] for i in db.list_incidents(conn, status="dismissed")] == [first]


def test_decide_incident_records_the_linked_run(conn):
    incident_id = db.new_incident_id()
    db.create_incident(conn, incident_id, project_id="p", pillar="agent", context="", exception_type="A", message="", traceback="")
    db.decide_incident(conn, incident_id, status="confirmed", run_id="run-1")
    incident = db.get_incident(conn, incident_id)
    assert incident["status"] == "confirmed" and incident["run_id"] == "run-1" and incident["decided_at"] is not None


def test_a_run_keeps_its_description_and_an_old_database_gains_the_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, job_id TEXT, project_id TEXT NOT NULL, status TEXT NOT NULL, "
                "intent TEXT NOT NULL, branch TEXT, branch_type TEXT, review_mode TEXT NOT NULL, completion_status TEXT, "
                "title TEXT NOT NULL, summary TEXT, report TEXT, pr_url TEXT, source_json TEXT NOT NULL DEFAULT '{}', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.commit(); old.close()
    conn = db.connect(path)
    db.create_run(conn, "r1", job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={}, description="write it")
    assert db.get_run(conn, "r1")["description"] == "write it"
