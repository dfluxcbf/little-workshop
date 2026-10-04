"""From requests to what the pages show: per-day rollups, the named metrics and the week views
(docs/pillars/usage.md, "Metrics"). A day's rollup is rebuilt from the requests still stored for it;
a day whose requests were pruned keeps the rollup it had."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from usage.pricing import Price, weighted

LONG_CONTEXT_TOKENS = 200_000  # the statusline's `exceeds_200k_tokens` threshold
WORKSHOP_PROJECT = "little-workshop"
SUMS = ("requests", "sessions", "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "weighted_input",
        "weighted_cache_read", "weighted_cache_write", "weighted_output", "context_tokens", "long_context_tokens", "poll_requests")


def rebuild(conn: sqlite3.Connection, prices: dict[str, Price], *, polls: set[str] | None = None) -> int:
    """Rebuild the rollup of every day that still has requests, and each session's startup context.
    `polls` is the request ids that only polled or waited (the findings phase supplies it)."""
    polls = polls or set()
    rows: dict[tuple, dict] = {}
    seen_sessions: dict[tuple, set] = {}
    query = """SELECT r.*, s.project AS project, s.origin AS origin FROM requests r
               LEFT JOIN sessions s ON s.id = r.session_id"""
    for row in conn.execute(query):
        request = dict(row)
        origin = "subagent" if request.get("query_source") == "subagent" else (request.get("origin") or "interactive")
        key = (request["day"], request.get("project") or "unattributed", origin, request["model"])
        out = rows.setdefault(key, dict.fromkeys(SUMS, 0))
        seen_sessions.setdefault(key, set()).add(request["session_id"])
        context = request["input_tokens"] + request["cache_read_tokens"] + request["cache_write_tokens"]
        parts = weighted(request, prices)
        out["requests"] += 1
        for name in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"):
            out[name] += request[name]
        out["weighted_input"] += parts["input"]
        out["weighted_cache_read"] += parts["cache_read"]
        out["weighted_cache_write"] += parts["cache_write"]
        out["weighted_output"] += parts["output"]
        out["context_tokens"] += context
        out["long_context_tokens"] += context if context > LONG_CONTEXT_TOKENS else 0
        out["poll_requests"] += 1 if request["request_id"] in polls else 0
    days = sorted({key[0] for key in rows})
    for day in days:
        conn.execute("DELETE FROM day_rollups WHERE day = ?", (day,))
    for key, out in rows.items():
        out["sessions"] = len(seen_sessions[key])
        conn.execute(
            f"INSERT INTO day_rollups (day, project, origin, model, {', '.join(SUMS)}) VALUES (?, ?, ?, ?, {', '.join('?' * len(SUMS))})",
            (*key, *(out[name] for name in SUMS)),
        )
    # Startup context: what a session's first main-thread request carried.
    conn.execute("""
        UPDATE sessions SET startup_context = (
            SELECT r.input_tokens + r.cache_read_tokens + r.cache_write_tokens FROM requests r
            WHERE r.session_id = sessions.id AND COALESCE(r.query_source, 'main') != 'subagent'
            ORDER BY r.ts LIMIT 1)
        WHERE EXISTS (SELECT 1 FROM requests r WHERE r.session_id = sessions.id)""")
    conn.commit()
    return len(days)


# -- reading ------------------------------------------------------------------


def monday_of(day: date) -> date:
    """A week starts on Monday, when the weekly allowance resets."""
    return day - timedelta(days=day.weekday())


def _filters(project: str | None, origin: str | None) -> tuple[str, list]:
    where, values = [], []
    if project:
        where.append("project = ?")
        values.append(project)
    if origin:
        where.append("origin = ?")
        values.append(origin)
    return (" AND " + " AND ".join(where)) if where else "", values


def _metrics(total: dict) -> dict:
    context = total["input_tokens"] + total["cache_read_tokens"] + total["cache_write_tokens"]
    weighted_total = total["weighted_input"] + total["weighted_cache_read"] + total["weighted_cache_write"] + total["weighted_output"]
    return {
        **total,
        "weighted": weighted_total,
        "cache_hit_ratio": total["cache_read_tokens"] / context if context else None,
        "average_context": total["context_tokens"] / total["requests"] if total["requests"] else None,
        "long_context_share": total["long_context_tokens"] / total["context_tokens"] if total["context_tokens"] else None,
        "polling_share": total["poll_requests"] / total["requests"] if total["requests"] else None,
    }


def days(conn: sqlite3.Connection, start: date, end: date, *, project: str | None = None, origin: str | None = None) -> list[dict]:
    """One row per day from `start` to `end` inclusive (days without data included, as zeros)."""
    extra, values = _filters(project, origin)
    sums = ", ".join(f"SUM({name}) AS {name}" for name in SUMS)
    found = {row["day"]: dict(row) for row in conn.execute(
        f"SELECT day, {sums} FROM day_rollups WHERE day BETWEEN ? AND ?{extra} GROUP BY day",
        (start.isoformat(), end.isoformat(), *values))}
    out = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        total = {name: (found.get(day) or {}).get(name) or 0 for name in SUMS}
        out.append({"day": day, **_metrics(total)})
    return out


def total(rows: list[dict]) -> dict:
    return _metrics({name: sum(row[name] for row in rows) for name in SUMS})


def weeks_available(conn: sqlite3.Connection) -> list[str]:
    """Every Monday that starts a week with data, oldest first."""
    found = {monday_of(date.fromisoformat(row["day"])).isoformat() for row in conn.execute("SELECT DISTINCT day FROM day_rollups")}
    return sorted(found)


def startup_p50(conn: sqlite3.Connection, start: date, end: date, *, project: str | None = None) -> int | None:
    extra, values = (" AND project = ?", [project]) if project else ("", [])
    contexts = sorted(row[0] for row in conn.execute(
        f"""SELECT startup_context FROM sessions WHERE startup_context IS NOT NULL
            AND substr(first_seen, 1, 10) BETWEEN ? AND ?{extra}""", (start.isoformat(), end.isoformat(), *values)))
    return contexts[len(contexts) // 2] if contexts else None


def by_project(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    sums = ", ".join(f"SUM({name}) AS {name}" for name in SUMS)
    out = []
    for row in conn.execute(f"SELECT project, {sums} FROM day_rollups WHERE day BETWEEN ? AND ? GROUP BY project",
                            (start.isoformat(), end.isoformat())):
        metrics = _metrics({name: row[name] or 0 for name in SUMS})
        out.append({"project": row["project"], **metrics, "startup_context_p50": startup_p50(conn, start, end, project=row["project"])})
    return sorted(out, key=lambda item: -item["weighted"])


def top_sessions(conn: sqlite3.Connection, start: date, end: date, prices: dict[str, Price], *, project: str | None = None, limit: int = 10) -> list[dict]:
    extra, values = (" AND s.project = ?", [project]) if project else ("", [])
    totals: dict[str, dict] = {}
    for row in conn.execute(
        f"""SELECT r.*, s.project AS project, s.origin AS origin, s.cwd AS cwd FROM requests r JOIN sessions s ON s.id = r.session_id
            WHERE r.day BETWEEN ? AND ?{extra}""", (start.isoformat(), end.isoformat(), *values)):
        request = dict(row)
        entry = totals.setdefault(request["session_id"], {
            "session_id": request["session_id"], "project": request["project"], "origin": request["origin"], "cwd": request["cwd"],
            "requests": 0, "weighted": 0.0, "max_context": 0, "first": request["ts"]})
        entry["requests"] += 1
        entry["weighted"] += sum(weighted(request, prices).values())
        entry["max_context"] = max(entry["max_context"], request["input_tokens"] + request["cache_read_tokens"] + request["cache_write_tokens"])
        entry["first"] = min(entry["first"], request["ts"])
    return sorted(totals.values(), key=lambda item: -item["weighted"])[:limit]


def pays_for_itself(conn: sqlite3.Connection) -> list[dict]:
    """Per week: weighted tokens spent building little-workshop against every other project."""
    weeks: dict[str, dict] = {}
    for row in conn.execute("""SELECT day, project, SUM(weighted_input + weighted_cache_read + weighted_cache_write + weighted_output) AS w
                               FROM day_rollups GROUP BY day, project"""):
        week = weeks.setdefault(monday_of(date.fromisoformat(row["day"])).isoformat(), {"workshop": 0.0, "others": 0.0})
        week["workshop" if row["project"] == WORKSHOP_PROJECT else "others"] += row["w"] or 0.0
    return [{"week": week, **values} for week, values in sorted(weeks.items())]


def plan_pressure(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """The highest 5-hour and 7-day usage percentage seen on each day."""
    return [dict(row) for row in conn.execute(
        """SELECT day, MAX(five_hour_pct) AS five_hour, MAX(seven_day_pct) AS seven_day FROM plan_pressure
           WHERE day BETWEEN ? AND ? GROUP BY day ORDER BY day""", (start.isoformat(), end.isoformat()))]


def summary(conn: sqlite3.Connection, prices: dict[str, Price], *, week: str | None = None, project: str | None = None,
            origin: str | None = None, today: date | None = None) -> dict:
    """One week (its Monday as `week`, default the current one) with what it is compared to: the week
    before, the week after and the average of the four weeks before, each only when it has data."""
    today = today or date.today()
    start = monday_of(date.fromisoformat(week)) if week else monday_of(today)
    end = start + timedelta(days=6)
    available = weeks_available(conn)

    def week_days(monday: date) -> list[dict] | None:
        if monday.isoformat() not in available:
            return None
        return days(conn, monday, monday + timedelta(days=6), project=project, origin=origin)

    this_week = days(conn, start, end, project=project, origin=origin)
    earlier = [week_days(start - timedelta(weeks=n)) for n in range(1, 5)]
    earlier = [rows for rows in earlier if rows]
    average = None
    if earlier:
        average = [{"weighted": sum(rows[i]["weighted"] for rows in earlier) / len(earlier),
                    "requests": sum(rows[i]["requests"] for rows in earlier) / len(earlier)} for i in range(7)]
    return {
        "week": start.isoformat(), "week_end": end.isoformat(), "weeks": available, "project": project, "origin": origin,
        "complete": end < today,
        "days": this_week, "total": total(this_week),
        "previous_week": week_days(start - timedelta(weeks=1)), "next_week": week_days(start + timedelta(weeks=1)),
        "four_week_average": average,
        "startup_context_p50": startup_p50(conn, start, end, project=project),
        "projects": by_project(conn, start, end),
        "top_sessions": top_sessions(conn, start, end, prices, project=project),
        "plan_pressure": plan_pressure(conn, start, end),
        "pays_for_itself": pays_for_itself(conn),
    }
