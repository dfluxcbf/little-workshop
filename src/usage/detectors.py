"""The detectors: deterministic code that reads the store and says where tokens go that need not
(docs/pillars/usage.md, "Findings"). No detector uses Claude.

Each detector measures every project over a period and returns one `Measurement` per project it has
data for: the metric's value, whether it crosses the threshold, the evidence, an estimate of what a
fix saves per week (weighted tokens) and the recommended action. `usage.analyze` turns flagged
measurements into findings; an experiment compares a metric's value before and after a change."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from usage import counterfactual
from usage.pricing import Price, weighted
from usage.rollup import LONG_CONTEXT_TOKENS

DEFAULT_THRESHOLDS = {
    "d1_long_context_share": 0.60, "d1_session_context": 500_000,
    "d2_startup_growth": 0.10, "d2_baseline_days": 30,
    "d3_miss_rate": 0.05,
    "d4_one_shot_sessions": 3,
    "d5_directories": 3,
    "d6_polling_share": 0.02, "d6_polls_per_episode": 10,
    "d7_result_bytes": 20_000, "d7_min_calls": 5,
    "d8_sessions": 5,
    "d9_routine_calls": 10,
    "d10_calls_per_session": 3,
    "d11_sessions": 5, "d11_days": 30,
    "d12_pressure_percent": 80,
}

POLL_SIGNATURES = ("mcp:workshop_job_status(", "mcp:workshop_job_list(", "Bash(sleep", "Bash(timeout")
ROUTINE_SIGNATURES = ("mcp:workshop_deployment_redeploy_pillar", "mcp:workshop_job_status(", "mcp:workshop_core_status(",
                      "mcp:workshop_version_status(", "Bash(python3 tools/sync_")
EXPENSIVE_MODELS = ("opus", "fable", "mythos")
# A raw call that a route replaces: (signature prefix, the route). Detector D10 watches that the route is used.
ADOPTION = (
    ("mcp:workshop_deployment_redeploy_pillar(", "workshop_deployment_redeploy_pillars (one call for several pillars)"),
    ("mcp:workshop_job_status(", "workshop_job_wait (one call that returns when the Job settles)"),
    ("Bash(sleep", "workshop_job_wait, or a job that notifies"),
)

ACTIONS = {
    "D1": "Clear between tasks; set a lower auto-compact window; hand verbose work to subagents; move recurring steps into jobs.",
    "D2": "Find what was added to the session start: an MCP server, a briefing, a rule without `paths`, a plugin.",
    "D3": "Keep model and effort fixed for a session and do not toggle MCP servers mid-task.",
    "D4": "Set CLAUDE_CODE_PROMPT_CACHE_TTL=5m for that kind of one-shot run.",
    "D5": "Run those one-shot calls in one stable working directory, so they share a prompt cache.",
    "D6": "Wait with workshop_job_wait (or have the job notify) instead of polling or sleeping.",
    "D7": "Filter the command's output with a PreToolUse hook, or run it in a subagent that returns only failures.",
    "D8": "Turn the document into a skill or a short digest that loads when needed.",
    "D9": "Use Sonnet or Haiku for that routine work: set the subagent's or the job type's model.",
    "D10": "Reword the route in the guard, the workshop-lifecycle skill or the briefing: Claude is not taking it.",
    "D11": "Disable the MCP server, or rely on deferred tool search.",
    "D12": "Move Claude-based scheduled jobs to quieter hours.",
}
# What fixes a finding. None is automation: findings are habits and configuration, and work a Job or a
# trigger could take over is mined separately (mining.py, the automation candidates).
FIXED_BY = {
    "D1": "a habit and a setting", "D2": "configuration", "D3": "a habit", "D4": "a setting", "D5": "a setting",
    "D6": "a route that exists (workshop_job_wait)", "D7": "a hook or a subagent", "D8": "a skill or a digest",
    "D9": "a setting (the model)", "D10": "instruction wording", "D11": "a setting (MCP servers)", "D12": "scheduling",
}
# How a finding's saving is known. `replayed`: the past occurrences replayed with the fix in place
# (counterfactual.py); `priced`: the recorded cost of the cache writes the fix removes; `rough estimate`: a
# share of a measured cost, since the saving depends on how habits change; None: not estimated.
SAVING_BASIS = {"D1": "rough estimate", "D3": "priced", "D4": "priced", "D5": "rough estimate", "D6": "replayed", "D7": "replayed"}
REPLAYED = ("D6", "D7")  # detectors that need the replay of the stored threads
WAIT_SECONDS = 120  # what one workshop_job_wait waits when Claude names no timeout (orchestration's default)
FILTERED_BYTES = 3_000  # what the test-output filter leaves of a run: about its last 40 lines and the failures

TITLES = {
    "D1": "Long sessions re-read a large context", "D2": "Startup context grew", "D3": "Prompt cache misses",
    "D4": "One-shot runs write a 1-hour cache", "D5": "One-shot runs spread over many directories",
    "D6": "Turns spent polling or waiting", "D7": "A command's output is large", "D8": "The same file is read in many sessions",
    "D9": "An expensive model does routine work", "D10": "A raw call is used where a route exists",
    "D11": "An MCP server is connected and never used", "D12": "Scheduled Claude jobs run under plan pressure",
}
METRICS = {
    "D1": "long-context share", "D2": "median startup context (tokens)", "D3": "cache miss rate", "D4": "one-shot sessions with a 1-hour cache write",
    "D5": "directories used by one-shot runs", "D6": "polling share", "D7": "largest average result (bytes)", "D8": "files read in many sessions",
    "D9": "routine calls on an expensive model", "D10": "raw calls where a route exists", "D11": "unused MCP servers",
    "D12": "days with job runs above the pressure threshold",
}


@dataclass
class Measurement:
    detector: str
    project: str
    value: float
    flagged: bool
    evidence: dict = field(default_factory=dict)
    saving: float = 0.0  # weighted tokens per week a fix is estimated to save

    def as_dict(self) -> dict:
        return {**asdict(self), "title": TITLES[self.detector], "metric": METRICS[self.detector], "action": ACTIONS[self.detector],
                **described(self.detector)}


def described(detector: str) -> dict:
    """What fixes a detector's finding, and how its saving is known."""
    return {"fixed_by": FIXED_BY.get(detector), "saving_basis": SAVING_BASIS.get(detector)}


@dataclass
class Period:
    start: date
    end: date

    @property
    def weeks(self) -> float:
        return max(((self.end - self.start).days + 1) / 7, 1 / 7)

    @property
    def bounds(self) -> tuple[str, str]:
        return self.start.isoformat(), self.end.isoformat()


def thresholds(config: dict | None) -> dict:
    return {**DEFAULT_THRESHOLDS, **((config or {}).get("thresholds") or {})}


def _requests(conn: sqlite3.Connection, period: Period) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT r.*, COALESCE(s.project, 'unattributed') AS project, COALESCE(s.origin, 'interactive') AS origin, s.cwd AS cwd
           FROM requests r LEFT JOIN sessions s ON s.id = r.session_id WHERE r.day BETWEEN ? AND ? ORDER BY r.ts""", period.bounds)]


def _tool_calls(conn: sqlite3.Connection, period: Period) -> list[dict]:
    return [dict(row) for row in conn.execute(
        """SELECT t.*, COALESCE(s.project, 'unattributed') AS project, s.model AS session_model
           FROM tool_calls t LEFT JOIN sessions s ON s.id = t.session_id
           WHERE substr(t.ts, 1, 10) BETWEEN ? AND ? ORDER BY t.ts""", period.bounds)]


def _by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out


def _context(request: dict) -> int:
    return request["input_tokens"] + request["cache_read_tokens"] + request["cache_write_tokens"]


def is_poll(signature: str) -> bool:
    return signature.startswith(POLL_SIGNATURES)


def poll_request_ids(conn: sqlite3.Connection) -> set[str]:
    """Requests whose every tool call polls or waits (known where the transcript tied calls to requests)."""
    calls: dict[str, list[bool]] = {}
    for row in conn.execute("SELECT request_id, signature FROM tool_calls WHERE request_id IS NOT NULL"):
        calls.setdefault(row["request_id"], []).append(is_poll(row["signature"]))
    return {request_id for request_id, polls in calls.items() if all(polls)}


# -- the detectors ----------------------------------------------------------------


def d1_long_context(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _by(requests, "project").items():
        total = sum(_context(r) for r in rows)
        long_rows = [r for r in rows if _context(r) > LONG_CONTEXT_TOKENS]
        share = sum(_context(r) for r in long_rows) / total if total else 0.0
        sessions: dict[str, int] = {}
        for r in rows:
            sessions[r["session_id"]] = max(sessions.get(r["session_id"], 0), _context(r))
        big = sorted(((size, sid) for sid, size in sessions.items() if size > t["d1_session_context"]), reverse=True)
        # Half of what the long requests re-read: a session cleared between tasks carries about half the context.
        saving = sum(weighted(r, prices)["cache_read"] for r in long_rows) * 0.5 / period.weeks
        out.append(Measurement("D1", project, share, share > t["d1_long_context_share"] or bool(big), {
            "long_context_share": share, "requests": len(rows), "requests_over_200k": len(long_rows),
            "sessions_over_500k": [{"session_id": sid, "largest_context": size} for size, sid in big[:5]],
        }, saving))
    return out


def d2_startup_growth(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    baseline_start = (period.start - timedelta(days=t["d2_baseline_days"])).isoformat()
    rows = conn.execute("""SELECT project, startup_context, substr(first_seen, 1, 10) AS day FROM sessions
                           WHERE startup_context IS NOT NULL AND first_seen IS NOT NULL""").fetchall()
    projects: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        bucket = "now" if period.start.isoformat() <= row["day"] <= period.end.isoformat() else (
            "before" if baseline_start <= row["day"] < period.start.isoformat() else None)
        if bucket:
            projects.setdefault(row["project"], {"now": [], "before": []})[bucket].append(row["startup_context"])
    for project, buckets in projects.items():
        if not buckets["now"]:
            continue
        now = median(buckets["now"])
        before = median(buckets["before"]) if buckets["before"] else None
        growth = (now - before) / before if before else 0.0
        out.append(Measurement("D2", project, float(now), bool(before) and growth > t["d2_startup_growth"], {
            "median_now": now, "median_before": before, "growth": growth, "sessions": len(buckets["now"]),
        }))
    return out


def d3_cache_misses(requests, calls, conn, period, t, prices) -> list[Measurement]:
    compactions = {(row["session_id"]) for row in conn.execute("SELECT DISTINCT session_id FROM session_events WHERE name LIKE '%Compact'")}
    out = []
    for project, rows in _by(requests, "project").items():
        seen: set[str] = set()
        misses = []
        main = [r for r in rows if r.get("query_source") != "subagent"]
        for r in main:
            first = r["session_id"] not in seen
            seen.add(r["session_id"])
            if not first and r["cache_read_tokens"] == 0 and r["cache_write_tokens"] > 0 and r["session_id"] not in compactions:
                misses.append(r)
        rate = len(misses) / len(main) if main else 0.0
        saving = sum(weighted(r, prices)["cache_write"] for r in misses) / period.weeks
        out.append(Measurement("D3", project, rate, rate > t["d3_miss_rate"], {
            "misses": len(misses), "requests": len(main), "sessions": sorted({r["session_id"] for r in misses})[:5],
        }, saving))
    return out


def _one_shots(requests: list[dict]) -> dict[str, list[dict]]:
    """project -> the single request of each session that made exactly one, started by a job or a query."""
    sessions = _by(requests, "session_id")
    out: dict[str, list[dict]] = {}
    for rows in sessions.values():
        if len(rows) == 1 and rows[0]["origin"] in ("job", "query"):
            out.setdefault(rows[0]["project"], []).append(rows[0])
    return out


def d4_one_shot_long_cache(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _one_shots(requests).items():
        wasteful = [r for r in rows if (r.get("cache_write_1h_tokens") or 0) > 0]
        # A 1-hour write costs 2x input and a 5-minute one 1.25x: the difference is what a 5-minute TTL saves.
        saving = sum(weighted(r, prices)["cache_write"] for r in wasteful) * (1 - 1.25 / 2.0) / period.weeks
        out.append(Measurement("D4", project, float(len(wasteful)), len(wasteful) >= t["d4_one_shot_sessions"], {
            "one_shot_sessions": len(rows), "with_one_hour_cache_write": len(wasteful),
        }, saving))
    return out


def d5_one_shot_directories(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _one_shots(requests).items():
        directories = {r["cwd"] for r in rows if r.get("cwd")}
        cold = [r for r in rows if r["cache_read_tokens"] < r["cache_write_tokens"]]
        saving = sum(weighted(r, prices)["cache_write"] for r in cold) * 0.5 / period.weeks
        out.append(Measurement("D5", project, float(len(directories)), len(directories) >= t["d5_directories"], {
            "one_shot_sessions": len(rows), "directories": len(directories), "examples": sorted(directories)[:3],
        }, saving if len(directories) >= t["d5_directories"] else 0.0))
    return out


def _poll_runs(polls: list[dict], calls: list[dict], replay: counterfactual.Replay) -> list[list[dict]]:
    """The polls as runs of back-to-back turns that only poll or wait, per thread: what one wait replaces."""
    other = {c["request_id"] for c in calls if c.get("request_id") and not is_poll(c["signature"])}
    located = sorted(((where, c) for c in polls if c.get("request_id") not in other for where in [replay.locate(c)] if where),
                     key=lambda item: (item[0], item[1]["ts"]))
    runs: list[list[dict]] = []
    last = None
    for (key, index), call in located:
        if last is not None and last[0] == key and index - last[1] <= 1:
            runs[-1].append(call)
        else:
            runs.append([call])
        last = (key, index)
    return runs


def _seconds(start: str, end: str) -> float:
    return max((datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds(), 0.0)


def d6_polling(requests, calls, conn, period, t, prices, replay, calibration) -> list[Measurement]:
    by_project_requests = _by(requests, "project")
    out = []
    for project, rows in _by(calls, "project").items():
        polls = [c for c in rows if is_poll(c["signature"])]
        project_requests = by_project_requests.get(project, [])
        share = len(polls) / len(project_requests) if project_requests else 0.0
        episodes: dict[tuple, int] = {}
        for c in polls:
            key = (c["session_id"], c.get("prompt_id") or "")
            episodes[key] = episodes.get(key, 0) + 1
        worst = max(episodes.values(), default=0)
        # Replayed: each run of polling turns becomes the waits it needs (one per WAIT_SECONDS it lasted),
        # each answering like the run's largest status read. A lone poll is already one turn: it saves nothing.
        saving, turns_saved = 0.0, 0
        for run in _poll_runs(polls, rows, replay):
            waits = max(1, math.ceil(_seconds(run[0]["ts"], run[-1]["ts"]) / WAIT_SECONDS))
            replayed = replay.occurrence(run, calibration, trigger_ok=False, kept_turns=waits,
                                         reply_bytes=max(c.get("result_bytes") or 0 for c in run))
            if replayed:
                saving += replayed["called"]
                turns_saved += max(replayed["turns"] - waits, 0)
        out.append(Measurement("D6", project, share, share > t["d6_polling_share"] or worst > t["d6_polls_per_episode"], {
            "polls": len(polls), "requests": len(project_requests), "most_polls_in_one_episode": worst,
            "by_signature": _counts(c["signature"] for c in polls), "turns_a_wait_saves": turns_saved,
        }, saving / period.weeks))
    return out


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1])[:8])


def d7_large_output(requests, calls, conn, period, t, prices, replay, calibration) -> list[Measurement]:
    out = []
    for project, rows in _by(calls, "project").items():
        heavy, saving = [], 0.0
        for signature, group in _by([c for c in rows if c.get("result_bytes")], "signature").items():
            average = sum(c["result_bytes"] for c in group) / len(group)
            if len(group) >= t["d7_min_calls"] and average > t["d7_result_bytes"]:
                # Replayed: each result cut to what a filter leaves, the rest no longer re-read by the later requests.
                saved = sum(replay.trimmed(c, calibration, FILTERED_BYTES) or 0.0 for c in group) / period.weeks
                heavy.append({"signature": signature, "calls": len(group), "average_bytes": round(average), "saving": round(saved, 2)})
                saving += saved
        heavy.sort(key=lambda item: -item["average_bytes"] * item["calls"])
        out.append(Measurement("D7", project, float(heavy[0]["average_bytes"]) if heavy else 0.0, bool(heavy), {"commands": heavy[:8]}, saving))
    return out


def d8_repeated_reads(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _by(calls, "project").items():
        files: dict[str, set[str]] = {}
        for c in rows:
            if c["tool"] == "Read" and c.get("detail"):
                files.setdefault(c["detail"], set()).add(c["session_id"])
        repeated = sorted(((len(sessions), path) for path, sessions in files.items() if len(sessions) >= t["d8_sessions"]), reverse=True)
        out.append(Measurement("D8", project, float(len(repeated)), bool(repeated), {
            "files": [{"path": path, "sessions": count} for count, path in repeated[:10]],
        }))
    return out


def d9_expensive_routine(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _by(calls, "project").items():
        routine = [c for c in rows if c["signature"].startswith(ROUTINE_SIGNATURES)
                   and any(name in (c.get("session_model") or "").lower() for name in EXPENSIVE_MODELS)]
        out.append(Measurement("D9", project, float(len(routine)), len(routine) >= t["d9_routine_calls"], {
            "routine_calls": len(routine), "by_signature": _counts(c["signature"] for c in routine),
        }))
    return out


def d10_adoption(requests, calls, conn, period, t, prices) -> list[Measurement]:
    out = []
    for project, rows in _by(calls, "project").items():
        found = []
        for prefix, route in ADOPTION:
            per_session = _counts(c["session_id"] for c in rows if c["signature"].startswith(prefix))
            repeated = {sid: n for sid, n in per_session.items() if n >= t["d10_calls_per_session"]}
            if repeated:
                found.append({"raw": prefix.rstrip("("), "route": route, "calls": sum(repeated.values()), "sessions": len(repeated)})
        out.append(Measurement("D10", project, float(sum(item["calls"] for item in found)), bool(found), {"routes_not_taken": found}))
    return out


def _mcp_prefix(server: str) -> str:
    return "mcp__" + re.sub(r"[^A-Za-z0-9_-]", "_", server) + "__"


def d11_unused_mcp(requests, calls, conn, period, t, prices) -> list[Measurement]:
    since = (period.end - timedelta(days=t["d11_days"])).isoformat()
    servers: dict[str, set[str]] = {}
    for row in conn.execute("SELECT session_id, data_json FROM session_events WHERE name = 'mcp_server' AND substr(ts, 1, 10) >= ?", (since,)):
        server = json.loads(row["data_json"]).get("server")
        if server:
            servers.setdefault(server, set()).add(row["session_id"])
    used = {row["tool"] for row in conn.execute("SELECT DISTINCT tool FROM tool_calls WHERE substr(ts, 1, 10) >= ?", (since,))}
    unused = sorted(server for server, sessions in servers.items()
                    if len(sessions) >= t["d11_sessions"] and not any(tool.startswith(_mcp_prefix(server)) for tool in used))
    if not servers:
        return []
    # Machine-wide: an MCP server is configured for the user, not for one project.
    return [Measurement("D11", "all", float(len(unused)), bool(unused), {"unused_servers": unused, "servers_seen": len(servers)})]


def d12_plan_pressure(requests, calls, conn, period, t, prices) -> list[Measurement]:
    hot = {row["day"]: row["peak"] for row in conn.execute(
        "SELECT day, MAX(five_hour_pct) AS peak FROM plan_pressure WHERE day BETWEEN ? AND ? GROUP BY day", period.bounds)
        if row["peak"] is not None and row["peak"] >= t["d12_pressure_percent"]}
    if not hot:
        return []
    out = []
    for project, rows in _by([r for r in requests if r["origin"] in ("job", "query")], "project").items():
        days = sorted({r["day"] for r in rows} & set(hot))
        out.append(Measurement("D12", project, float(len(days)), bool(days), {"days": [{"day": day, "five_hour_peak": hot[day]} for day in days]}))
    return out


DETECTORS = {
    "D1": d1_long_context, "D2": d2_startup_growth, "D3": d3_cache_misses, "D4": d4_one_shot_long_cache,
    "D5": d5_one_shot_directories, "D6": d6_polling, "D7": d7_large_output, "D8": d8_repeated_reads,
    "D9": d9_expensive_routine, "D10": d10_adoption, "D11": d11_unused_mcp, "D12": d12_plan_pressure,
}
# Lower is better for every metric, so an experiment succeeded when its metric fell.


def measure(conn: sqlite3.Connection, period: Period, prices: dict[str, Price], config: dict | None = None,
            only: tuple[str, ...] | None = None, *, replay: counterfactual.Replay | None = None,
            calibration: counterfactual.Calibration | None = None) -> list[Measurement]:
    """Every detector's measurement of every project over `period`. The replay the D6 and D7 savings rest
    on is built once, when one of them runs, unless the caller has one."""
    t = thresholds(config)
    requests, calls = _requests(conn, period), _tool_calls(conn, period)
    out: list[Measurement] = []
    for name, detector in DETECTORS.items():
        if only is not None and name not in only:
            continue
        if name in REPLAYED:
            if replay is None:
                replay, calibration = counterfactual.prepare(conn, prices)
            elif calibration is None:
                calibration = counterfactual.calibrate(conn, replay)
            out.extend(detector(requests, calls, conn, period, t, prices, replay, calibration))
        else:
            out.extend(detector(requests, calls, conn, period, t, prices))
    return out
