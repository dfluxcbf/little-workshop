"""The data explorer (docs/pillars/usage.md, "Data explorer"): data the store keeps before it has a use,
compiled into one plot per card, at the bottom of the Claude usage page.

A card is one function in `CARDS`: it reads the store over a run of days and returns what the page
draws: a title, what the card shows and where its data comes from, a kind (`stack`: bars stacked per
day; `line`), a unit, one series per name, and a note summing up the period. A series may set
`axis: "right"` and its own `unit` to be drawn as a line on a second scale. Adding a card is adding a
function here; the page draws whatever it is given."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from statistics import median

from usage.db import local_day

DEFAULT_DAYS = 28
MAX_DAYS = 120
TOP_NAMES = 5  # a card stacking names shows the largest few; the rest add up as "other"
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _card(card_id: str, title: str, about: str, kind: str, unit: str, series: list[dict], note: str | None = None) -> dict:
    return {"id": card_id, "title": title, "about": about, "kind": kind, "unit": unit, "series": series, "note": note}


def _by_name(rows, labels: list[str], *, order: tuple[str, ...] = (), top: int = TOP_NAMES) -> list[dict]:
    """(day, name, value) rows as one series per name: the `top` largest by total (or the names in
    `order` first, in that order), the rest added up as "other"."""
    per: dict[str, dict[str, float]] = {}
    for day, name, value in rows:
        per.setdefault(name, {})
        per[name][day] = per[name].get(day, 0) + (value or 0)
    totals = {name: sum(days.values()) for name, days in per.items()}
    names = [n for n in order if n in per] + sorted((n for n in per if n not in order), key=lambda n: -totals[n])
    shown, rest = names[:top], names[top:]
    series = [{"name": name, "values": [per[name].get(day, 0) for day in labels]} for name in shown]
    if rest:
        series.append({"name": "other", "values": [sum(per[name].get(day, 0) for name in rest) for day in labels]})
    return series


def _events(conn: sqlite3.Connection, kind: str, labels: list[str]) -> list[sqlite3.Row]:
    return conn.execute("SELECT day, name, value, data_json FROM transcript_events WHERE kind = ? AND day BETWEEN ? AND ?",
                        (kind, labels[0], labels[-1])).fetchall()


def _share(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def _quantile(values: list[float], q: float) -> float:
    """The nearest-rank quantile: the smallest value at least `q` of the values are at or under."""
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)) - 1, 0)]


# -- the cards --------------------------------------------------------------------


def thinking(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = conn.execute(
        """SELECT day, COALESCE(effort, 'not recorded') AS effort, SUM(thinking_tokens) AS thinking, SUM(output_tokens) AS output
           FROM requests WHERE day BETWEEN ? AND ? AND thinking_tokens IS NOT NULL GROUP BY day, effort""", (labels[0], labels[-1])).fetchall()
    thought, output = sum(r["thinking"] or 0 for r in rows), sum(r["output"] or 0 for r in rows)
    by_effort: dict[str, list[float]] = {}
    for r in rows:
        totals = by_effort.setdefault(r["effort"], [0, 0])
        totals[0] += r["thinking"] or 0
        totals[1] += r["output"] or 0
    parts = ", ".join(f"{effort} {_share(*by_effort[effort])}" for effort in [*EFFORTS, "not recorded"] if effort in by_effort)
    note = f"Thinking is {_share(thought, output)} of output tokens ({parts})." if output else None
    return _card("thinking", "Thinking tokens, by effort",
                 "Output tokens spent thinking each day, by the effort the turn ran at. From each request's usage in the transcripts.",
                 "stack", "tokens", _by_name([(r["day"], r["effort"], r["thinking"]) for r in rows], labels,
                                               order=(*EFFORTS, "not recorded"), top=len(EFFORTS) + 1), note)


def compactions(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = _events(conn, "compaction", labels)
    sizes = [json.loads(r["data_json"]) for r in rows]
    pre = [s["pre_tokens"] for s in sizes if s.get("pre_tokens") is not None]
    post = [s["post_tokens"] for s in sizes if s.get("post_tokens") is not None]
    note = (f"{len(rows)} compactions; the median one went from {median(pre) / 1000:,.0f}k to {median(post) / 1000:,.0f}k tokens."
            if pre and post else None)
    return _card("compactions", "Compactions, by trigger",
                 "Conversations compacted each day: automatically when the context filled, or by /compact. From the transcripts' compaction records.",
                 "stack", "count", _by_name([(r["day"], r["name"], 1) for r in rows], labels, order=("auto", "manual")), note)


def turn_time(conn: sqlite3.Connection, labels: list[str]) -> dict:
    per_day: dict[str, list[float]] = {}
    for r in _events(conn, "turn", labels):
        per_day.setdefault(r["day"], []).append((r["value"] or 0) / 60_000)
    every = [v for values in per_day.values() for v in values]
    series = [{"name": name, "values": [round(_quantile(per_day[day], q), 2) if day in per_day else None for day in labels]}
              for name, q in (("median turn", 0.5), ("slowest 10%", 0.9))]
    note = f"{len(every)} turns; median {median(every):.1f} min, the slowest 10% over {_quantile(every, 0.9):.1f} min." if every else None
    return _card("turn_time", "How long a turn takes",
                 "From a prompt to Claude's answer, each day's median and the time the slowest tenth of turns exceeded. "
                 "From the transcripts' turn-duration records, which give the total only.", "line", "minutes", series, note)


def insertions(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = _events(conn, "insertion", labels)
    total = sum(r["value"] or 0 for r in rows)
    days = len({r["day"] for r in rows})
    note = f"{total / 1000:,.0f} kB in {len(rows):,} insertions, about {total / days / 1000:,.0f} kB a day." if days else None
    return _card("insertions", "What the harness adds to the conversation",
                 "Bytes Claude Code renders into the conversation between turns, by kind: reminders, the environment, files read or "
                 "edited, queued messages. From the transcripts' attachments; a tool list or a prompt snapshot is not rendered and not counted.",
                 "stack", "bytes", _by_name([(r["day"], r["name"], r["value"]) for r in rows], labels), note)


def prompt_sources(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = _events(conn, "prompt", labels)
    queued = sum(1 for r in rows if r["name"] == "queued")
    note = f"{len(rows)} prompts with a recorded source; {_share(queued, len(rows))} were queued while Claude was working." if rows else None
    return _card("prompt_sources", "How prompts arrive",
                 "Prompts each day by how they arrived: typed, queued while Claude was working, sent by the system or by an SDK "
                 "client. From the transcripts; older Claude Code versions do not record it.",
                 "stack", "count", _by_name([(r["day"], r["name"], 1) for r in rows], labels, order=("typed", "queued")), note)


def denials(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = _events(conn, "denial", labels)
    note = f"{len(rows)} tool calls denied; each one is a turn that did nothing." if rows else None
    return _card("denials", "Tool calls denied, by kind",
                 "Tool calls refused each day: by a permission rule, by auto mode, or by you. From the transcripts' denial records.",
                 "stack", "count", _by_name([(r["day"], r["name"], 1) for r in rows], labels), note)


def rate_limits(conn: sqlite3.Connection, labels: list[str]) -> dict:
    rows = _events(conn, "api_error", labels)
    peaks = {r["day"]: r["peak"] for r in conn.execute(
        "SELECT day, MAX(five_hour_pct) AS peak FROM plan_pressure WHERE day BETWEEN ? AND ? GROUP BY day", (labels[0], labels[-1]))}
    series = _by_name([(r["day"], f"API error {r['name']}", 1) for r in rows], labels)
    series.append({"name": "5-hour window, highest", "values": [peaks.get(day) for day in labels], "axis": "right", "unit": "percent"})
    limited = sum(1 for r in rows if r["name"] == "429")
    note = f"{len(rows)} API errors, {limited} of them rate limits (429)." if rows else None
    return _card("rate_limits", "API errors and plan pressure",
                 "API errors each day (429 is a rate limit), beside the highest the 5-hour plan window reached. "
                 "From the transcripts' error records and the statusline.", "stack", "count", series, note)


def test_runs(conn: sqlite3.Connection, labels: list[str]) -> dict:
    first, last = labels[0], (date.fromisoformat(labels[-1]) + timedelta(days=1)).isoformat()
    counts: dict[tuple[str, str], int] = {}
    for r in conn.execute("""SELECT ts, outcome FROM tool_calls WHERE (tests_passed IS NOT NULL OR tests_failed IS NOT NULL)
                             AND substr(ts, 1, 10) BETWEEN ? AND ?""", (first, last)):
        day = local_day(r["ts"])
        if first <= day <= labels[-1]:
            name = "failed" if r["outcome"] == "failed" else "passed"
            counts[(day, name)] = counts.get((day, name), 0) + 1
    runs, failed = sum(counts.values()), sum(n for (_, name), n in counts.items() if name == "failed")
    note = f"{runs} test runs, {failed} failed ({_share(failed, runs)})." if runs else None
    return _card("test_runs", "Test runs, passed and failed",
                 "Test commands each day by how they ended. From the summary each run printed (pytest, Bazel, Vitest), "
                 "so a run piped into tail still counts as failed.", "stack", "count",
                 _by_name([(day, name, n) for (day, name), n in counts.items()], labels, order=("passed", "failed")), note)


CARDS = (thinking, compactions, turn_time, insertions, prompt_sources, denials, rate_limits, test_runs)


def build(conn: sqlite3.Connection, *, days: int = DEFAULT_DAYS, today: date | None = None) -> dict:
    """Every card over the `days` ending `today`."""
    end = today or date.today()
    days = max(1, min(days, MAX_DAYS))
    labels = [(end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    return {"days": labels, "cards": [card(conn, labels) for card in CARDS]}
