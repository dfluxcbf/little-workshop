"""No dedicated test file existed for confirm.py's rollback_guard before
this pass. Real sqlite throughout, via deployment.db exactly as the
production code path uses it."""

import pytest

from deployment import db
from deployment.confirm import rollback_guard


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


def test_rollback_guard_no_state_at_all(conn):
    state, reason = rollback_guard(conn, "ghost", "deploy-1")
    assert state is None
    assert "no confirm-gate state found" in reason


def test_rollback_guard_already_confirmed_is_a_noop(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    state, reason = rollback_guard(conn, "little-meals", "some-deploy-id")
    assert state is not None
    assert "already settled" in reason


def test_rollback_guard_stale_deploy_id_is_ignored(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    state, reason = rollback_guard(conn, "little-meals", "not-" + pending["deploy_id"])
    assert state is not None
    assert "stale watchdog" in reason


def test_rollback_guard_proceeds_for_the_current_pending_deploy(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    state, reason = rollback_guard(conn, "little-meals", pending["deploy_id"])
    assert reason is None
    assert state["deploy_id"] == pending["deploy_id"]
    assert state["confirmed_commit"] == "abc"


def test_rollback_guard_proceeds_regardless_of_deadline_value():
    """rollback_guard itself is purely status/deploy_id based -- the actual
    deadline comparison is the watchdog timer's job (a systemd --on-active
    timer that only fires once), not this function's -- so an already-
    expired deadline still proceeds as long as status/deploy_id match."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(Path(tmp) / "deployment.db")
        db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
        pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=-999)
        state, reason = rollback_guard(conn, "little-meals", pending["deploy_id"])
        assert reason is None
        conn.close()
