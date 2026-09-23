"""The confirm-gate watchdog's staleness check — ported from
`little-runner`'s `deploy_confirm.rollback_guard`. Factored out so the
Job handler can short-circuit before touching git/systemd at all.
"""

from __future__ import annotations

import sqlite3

from deployment import db


def rollback_guard(conn: sqlite3.Connection, project_id: str, deploy_id: str) -> tuple[dict | None, str | None]:
    """Returns (state, None) when the rollback should proceed, or
    (state-or-None, reason) when it's a no-op: no state at all, already
    settled (confirmed — including the "user clicked confirm seconds
    before the timer fired" race), or a stale deploy_id (a later deploy
    or rollback already superseded this pending one)."""
    state = db.get_confirmation(conn, project_id)
    if state is None:
        return None, f"{project_id}: no confirm-gate state found; nothing to roll back"
    if state["status"] != db.PENDING:
        return state, f"{project_id}: deploy already settled (status={state['status']}); nothing to roll back"
    if state["deploy_id"] != deploy_id:
        return state, f"{project_id}: stale watchdog for deploy {deploy_id} (current pending deploy is {state['deploy_id']}); ignoring"
    return state, None
