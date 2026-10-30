"""The policy bypass (`docs/pillars/policy_compliance.md`'s "Policy bypass"): a flag only the user can raise,
from `sudo lws bypass_policies true`, that lapses by itself. While it holds, a failing check requested from the
user's own terminal CLI does not gate; it is still run, recorded and shown. Every other origin keeps gating.
The flag is a row with an expiry, not a timer: it is TRUE exactly while `expires_at` is in the future, so a
restart or a missed tick never leaves it on.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

from common import origin as origins

DEFAULT_MINUTES = 2
MAX_MINUTES = 30

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BypassRefused(Exception):
    """The request may not turn the flag on; the message says why and what to do."""


def validate_minutes(minutes: int | None) -> int:
    value = DEFAULT_MINUTES if minutes is None else minutes
    if value < 1 or value > MAX_MINUTES:
        raise BypassRefused(f"--time must be between 1 and {MAX_MINUTES} minutes (default {DEFAULT_MINUTES}), got {value}")
    return value


def _log(conn: sqlite3.Connection, now: datetime, action: str, actor: str, origin: str, detail: str = "") -> None:
    conn.execute("INSERT INTO policy_bypass_log (at, action, actor, origin, detail) VALUES (?, ?, ?, ?, ?)",
                 (now.isoformat(), action, actor, origin, detail))


def status(conn: sqlite3.Connection, now: Clock = _utc_now) -> dict:
    """`{"bypass_policies": bool, "expires_at", "remaining_seconds", "enabled_by"}`; a lapsed flag reads FALSE."""
    current = now()
    row = conn.execute("SELECT expires_at, enabled_by FROM policy_bypass WHERE id = 1").fetchone()
    if row is not None:
        expires = datetime.fromisoformat(row["expires_at"])
        if expires > current:
            return {"bypass_policies": True, "expires_at": expires.isoformat(),
                    "remaining_seconds": int((expires - current).total_seconds()), "enabled_by": row["enabled_by"]}
        conn.execute("DELETE FROM policy_bypass WHERE id = 1")
        _log(conn, current, "expired", "system", origins.UNKNOWN)
        conn.commit()
    return {"bypass_policies": False, "expires_at": None, "remaining_seconds": 0, "enabled_by": None}


def enable(conn: sqlite3.Connection, *, minutes: int | None, origin: str, actor: str, now: Clock = _utc_now) -> dict:
    """Raise the flag for `minutes`, starting now: asked again while TRUE, the timer restarts (it never
    extends past `MAX_MINUTES` from the request). Only the user's terminal may ask."""
    if origin != origins.USER_CLI:
        raise BypassRefused(
            f"the policy bypass can only be turned on by the user's own terminal (origin {origin!r} refused); "
            "run `sudo lws bypass_policies true` yourself")
    if not actor:
        raise BypassRefused("the policy bypass needs the super user: run it with sudo")
    value = validate_minutes(minutes)
    current = now()
    expires = current + timedelta(minutes=value)
    conn.execute("INSERT INTO policy_bypass (id, expires_at, enabled_by, enabled_at) VALUES (1, ?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET expires_at = excluded.expires_at, enabled_by = excluded.enabled_by, "
                 "enabled_at = excluded.enabled_at", (expires.isoformat(), actor, current.isoformat()))
    _log(conn, current, "enabled", actor, origin, f"{value} minute(s)")
    conn.commit()
    return status(conn, now)


def disable(conn: sqlite3.Connection, *, origin: str, actor: str, now: Clock = _utc_now) -> dict:
    """Back to FALSE at once. Open to every origin: turning it off only makes policies stricter."""
    current = now()
    was_on = status(conn, now)["bypass_policies"]
    conn.execute("DELETE FROM policy_bypass WHERE id = 1")
    if was_on:
        _log(conn, current, "disabled", actor, origin)
    conn.commit()
    return status(conn, now)


def bypasses(conn: sqlite3.Connection, origin: str, now: Clock = _utc_now) -> bool:
    """Whether a failing check from this origin may skip gating right now."""
    return origin == origins.USER_CLI and status(conn, now)["bypass_policies"]


def record_violation(conn: sqlite3.Connection, *, origin: str, detail: str, now: Clock = _utc_now) -> None:
    _log(conn, now(), "violation_bypassed", "user", origin, detail)
    conn.commit()


def log_entries(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT at, action, actor, origin, detail FROM policy_bypass_log ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]
