"""Automation mining: finding the work Claude repeats with the same shape and few judgment calls,
so that code can do it for zero tokens (docs/pillars/usage.md, "Automation mining").

An **episode** is the tool calls of one prompt. Each call is already a **signature** (`signatures.py`).
A **candidate** is a signature, or a run of two or three consecutive signatures, that recurs. Each
candidate gets a determinism score from five checks, a class, the weighted tokens its occurrences
cost per week and a proposed form. Nothing here uses Claude, and nothing is implemented from it: a
person chooses "Make it a task" or "Dismiss"."""

from __future__ import annotations

import sqlite3
import re
from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median

from usage import counterfactual, signatures
from usage.pricing import Price

MIN_SESSIONS, MIN_OCCURRENCES, PERIOD_DAYS = 3, 10, 14
MAX_SEQUENCE = 3
CLASS_DETERMINISTIC, CLASS_HYBRID, CLASS_HEURISTIC = "deterministic", "hybrid", "heuristic"
CHECKS = ("argument_derivability", "output_stability", "checkability", "no_content_creation", "bounded_branching")

# Tools whose arguments are text Claude wrote, or whose outcome only a person can judge.
FREE_TEXT_TOOLS = ("Agent", "Task", "WebSearch", "WebFetch", "AskUserQuestion", "Skill")
CONTENT_TOOLS = ("Edit(", "Write(", "MultiEdit(", "NotebookEdit(")
# What no code could do in Claude's place: looking around (exploration is judgment by nature),
# writing content, a script written on the command line, a shell command that only moves about or
# could not be read. A candidate needs at least one call that is none of these.
EXPLORATION = ("Read(", "Grep(", "Glob(", "LS(", "ToolSearch(", "TodoWrite(")
LOOKING_SHELL = ("cat", "ls", "head", "tail", "grep", "rg", "find", "sed", "awk", "wc", "echo", "pwd", "cd", "which", "tree", "stat",
                 "git status", "git diff", "git log", "git show", "git branch", "git rev-parse", "git ls-files")


def actionable(signature: str) -> bool:
    """Whether code could make this call: an MCP call, or a shell command that does work."""
    if not signature or signature.startswith(EXPLORATION) or signature.startswith(CONTENT_TOOLS) or signature.startswith(FREE_TEXT_TOOLS):
        return False
    if signature.startswith("Bash("):
        inner = signature[len("Bash("):-1]
        if inner in ("", "?") or signatures.INLINE in inner:
            return False
        return not any(inner == word or inner.startswith(word + " ") for word in LOOKING_SHELL)
    return True
NIGHT_START = (2, 0)  # recommended runs start at 02:00 and follow one another
NIGHT_STEP_MINUTES = 30
WEEKDAYS = 7


def episodes(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """[{"session_id", "prompt_id", "project", "calls": [tool-call rows in order]}], one per prompt."""
    rows = conn.execute(
        """SELECT t.*, COALESCE(s.project, 'unattributed') AS project FROM tool_calls t LEFT JOIN sessions s ON s.id = t.session_id
           WHERE substr(t.ts, 1, 10) BETWEEN ? AND ? ORDER BY t.session_id, t.ts, t.id""", (start.isoformat(), end.isoformat())).fetchall()
    out: dict[tuple, dict] = {}
    for row in rows:
        key = (row["session_id"], row["prompt_id"] or "")
        episode = out.setdefault(key, {"session_id": row["session_id"], "prompt_id": row["prompt_id"], "project": row["project"], "calls": []})
        episode["calls"].append(dict(row))
    return list(out.values())


# The net-saving estimate (docs/pillars/usage.md, "What automating would save"): each occurrence is
# replayed against the session it happened in (`counterfactual.Replay`). A replacement saves only what
# Claude would no longer spend: turns, and what they added to the conversation for every later request.
TOOL_NAME_TOKENS = 30  # a new tool's name in every session's starting context, re-read by every request
PILLAR_CALL = "mcp:workshop_"  # work a pillar does: the only kind a trigger or a schedule can run without Claude
# Pillar calls no trigger can take over: reads, which Claude makes to learn something it then uses, and
# the decisions a trigger may not make (Orchestration refuses a trigger that finishes, confirms, releases,
# removes or deletes; publishing a requirement or a package is the owner's call too).
_READ = re.compile(r"_(status|list|get|report|summary|findings|candidates|results|winning|evaluate|branches|history|wait|config|audit)\(")
_NOT_FOR_TRIGGERS = re.compile(r"_(finish|confirm|remove|delete|release|stabilize|answer|stop|publish)\b|_release_|_publish_|policy_confirm")
# Below this a candidate is not listed: the task that builds the replacement costs several dollars of
# weighted tokens, which a few cents a week would not pay back within a year.
MIN_WEEKLY_SAVING = 0.10


def trigger_can_run(signature: str) -> bool:
    return signature.startswith(PILLAR_CALL) and not _READ.search(signature) and not _NOT_FOR_TRIGGERS.search(signature)


def _is_free_text(signature: str) -> bool:
    return signature.startswith(FREE_TEXT_TOOLS) or signatures.INLINE in signature


def score(key: tuple[str, ...], occurrences: list[dict]) -> dict[str, bool]:
    """The five checks for a candidate. `occurrences` are {"session_id", "calls": [...], "next": signature | None}."""
    calls = [call for occurrence in occurrences for call in occurrence["calls"]]
    judged = [call for call in calls if call.get("success") is not None]
    recorded = bool(judged) and len(judged) >= 0.9 * len(calls)
    return {
        # Every argument comes from repository or pillar state: a shell command or an MCP call, not prose.
        "argument_derivability": not any(_is_free_text(signature) for signature in key),
        # The same shape in several sessions, not one session's habit.
        "output_stability": len({occurrence["session_id"] for occurrence in occurrences}) >= MIN_SESSIONS,
        # The outcome is a pillar state or an exit status that code can read.
        "checkability": recorded and all(signature.startswith(("mcp:", "Bash(")) for signature in key),
        "no_content_creation": not any(signature.startswith(CONTENT_TOOLS) for signature in key),
        # Each call ends in one of a few known outcomes (succeeded or failed), not something open-ended
        # that only a reader can make sense of. What Claude did next is the session's business, not the
        # call's: judging that ranked no real candidate deterministic (2026-10-04).
        "bounded_branching": recorded and not any(_is_free_text(signature) for signature in key),
    }


def classify(checks: dict[str, bool]) -> str:
    if all(checks.values()):
        return CLASS_DETERMINISTIC
    if checks["no_content_creation"] and sum(checks.values()) >= 3:
        return CLASS_HYBRID
    return CLASS_HEURISTIC


def proposed_form(key: tuple[str, ...], kind: str) -> str:
    first = key[0]
    if any(signature.startswith(("mcp:workshop_job_status(", "Bash(sleep", "Bash(timeout")) for signature in key):
        return "A blocking wait: workshop_job_wait, or a job that notifies when it is done"
    if kind == CLASS_HEURISTIC:
        return "Stays with Claude: optimize its context (a skill instead of exploration, a subagent, the right model)"
    if all(signature.startswith(("Read(", "Grep(")) for signature in key):
        return "A skill or a short digest that loads when needed"
    if any("pytest" in signature or "bazel test" in signature or "npm test" in signature for signature in key):
        return "The test-output filter hook, or a subagent that returns only the failures"
    if kind == CLASS_DETERMINISTIC and len(key) > 1:
        return "One job (a JobSpec in the catalog) or MCP tool that performs the whole sequence, or a trigger that runs it after the event that precedes it"
    if kind == CLASS_DETERMINISTIC and first.startswith("mcp:"):
        return "A trigger that runs it after the event that precedes it, or one tool that takes the whole batch"
    if kind == CLASS_DETERMINISTIC:
        return "A job (a JobSpec in the catalog) or an MCP tool"
    return "Code around Claude: a preprocessing hook, a context digest written before the agent starts, or a subagent for the verbose part"


def recommend_schedule(timestamps: list[str], slot: int) -> dict:
    """How often to run it once automated, from how often it happened: daily when it happened on most
    days, else weekly on the weekdays it happened most. Runs are recommended for the middle of the
    night, one after another (`slot` is this candidate's place in the sequence)."""
    days = sorted({stamp[:10] for stamp in timestamps})
    span = max((date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1, 1) if days else 1
    weekdays = Counter(date.fromisoformat(day).weekday() for day in days)
    if len(days) >= 0.6 * min(span, PERIOD_DAYS) and len(days) >= 4:
        chosen, frequency = list(range(WEEKDAYS)), "daily"
    else:
        most = max(weekdays.values(), default=0)
        chosen = sorted(day for day, count in weekdays.items() if count == most)[:2] or [0]
        frequency = "weekly"
    minutes = NIGHT_START[0] * 60 + NIGHT_START[1] + slot * NIGHT_STEP_MINUTES
    return {"frequency": frequency, "kind": "recurring", "days_of_week": chosen,
            "time_of_day": f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}",
            "note": "in the middle of the night, one task after another, so it does not compete with your own sessions"}


def resign(conn: sqlite3.Connection) -> int:
    """Sign every stored shell call again from the command kept with it, so calls stored under an
    older normalization compare with new ones. Returns how many signatures changed."""
    changed = 0
    for row in conn.execute("SELECT id, signature, detail FROM tool_calls WHERE tool = 'Bash'").fetchall():
        fresh = signatures.bash_signature(row["detail"])
        if fresh != row["signature"]:
            conn.execute("UPDATE tool_calls SET signature = ? WHERE id = ?", (fresh, row["id"]))
            changed += 1
    return changed


def mine(conn: sqlite3.Connection, prices: dict[str, Price], *, today: date | None = None, days: int = PERIOD_DAYS,
         replay: "counterfactual.Replay | None" = None, calibration: "counterfactual.Calibration | None" = None) -> list[dict]:
    """The candidates of the last `days` that automating would save tokens on, ranked by the estimated net
    saving per week. A candidate whose estimate is under `MIN_WEEKLY_SAVING` is left out: automating it
    would not make Claude cheaper, whatever else it is good for."""
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    weeks = days / 7
    replay = replay or counterfactual.Replay(conn, prices)
    calibration = calibration or counterfactual.calibrate(conn, replay)
    # A tool Claude calls instead has a cost of its own: its name in every session's starting context.
    fixed_weekly = TOOL_NAME_TOKENS * replay.requests_between(start, end) / weeks * replay.machine_read_price
    found: dict[tuple[str, ...], list[dict]] = {}
    for episode in episodes(conn, start, end):
        calls = episode["calls"]
        signatures = [call["signature"] for call in calls]
        for length in range(1, MAX_SEQUENCE + 1):
            for index in range(len(calls) - length + 1):
                key = tuple(signatures[index:index + length])
                if length > 1 and len(set(key)) == 1:
                    continue  # a signature repeated is the single signature's candidate
                if not any(actionable(signature) for signature in key):
                    continue
                found.setdefault(key, []).append({
                    "session_id": episode["session_id"], "project": episode["project"], "calls": calls[index:index + length],
                    "next": signatures[index + length] if index + length < len(signatures) else None,
                })
    candidates = []
    for key, occurrences in found.items():
        sessions = {occurrence["session_id"] for occurrence in occurrences}
        if len(sessions) < MIN_SESSIONS and len(occurrences) < MIN_OCCURRENCES:
            continue
        pillar_runnable = all(trigger_can_run(signature) for signature in key)
        savings = [s for s in (replay.occurrence(o["calls"], calibration, trigger_ok=pillar_runnable) for o in occurrences) if s]
        if not savings:
            continue
        net_called = sum(s["called"] for s in savings) / weeks - fixed_weekly
        net_triggered = sum(s["triggered"] for s in savings) / weeks if pillar_runnable else None
        best = max(net_called, net_triggered if net_triggered is not None else net_called)
        if best < MIN_WEEKLY_SAVING:
            continue
        checks = score(key, occurrences)
        kind = classify(checks)
        examples = []
        for occurrence in occurrences[:3]:
            examples.append({"session_id": occurrence["session_id"], "project": occurrence["project"],
                             "calls": [call["detail"] or call["signature"] for call in occurrence["calls"]]})
        candidates.append({
            "signature": " → ".join(key), "class": kind, "score": sum(checks.values()), "checks": checks,
            "occurrences": len(occurrences), "sessions": len(sessions), "weekly_value": best,
            # What one run of the replacement saves when Claude calls it: what "tokens avoided" counts.
            "median_cost": median(s["called"] for s in savings), "proposed_form": proposed_form(key, kind), "examples": examples,
            "estimate": {"net_if_called": net_called, "net_if_triggered": net_triggered, "fixed_weekly_cost": fixed_weekly,
                         "net_if_called_low": sum(s["called_low"] for s in savings) / weeks - fixed_weekly,
                         "net_if_triggered_low": sum(s["triggered_low"] for s in savings) / weeks if pillar_runnable else None,
                         "occurrences_replayed": len(savings),
                         "turns_per_occurrence": round(median(s["turns"] for s in savings), 1),
                         "added_tokens_per_occurrence": int(median(s["added_tokens"] for s in savings)),
                         "later_requests_per_occurrence": int(median(s["later_requests"] for s in savings)),
                         "output_bytes_per_occurrence": int(median(s["output_bytes"] for s in savings))},
            "projects": dict(Counter(occurrence["project"] for occurrence in occurrences).most_common(5)),
            "timestamps": [occurrence["calls"][0]["ts"] for occurrence in occurrences],
        })
    candidates.sort(key=lambda candidate: (-candidate["weekly_value"], -candidate["occurrences"], candidate["signature"]))
    for slot, candidate in enumerate(candidates):
        candidate["schedule"] = recommend_schedule(candidate.pop("timestamps"), slot)
    return candidates


AUTOMATE_TITLE = "Automate repeated Claude work"
AUTOMATE_DESCRIPTION = """Turn work that Claude sessions repeat by hand into code in little-workshop, so that every later run of it costs no tokens. The Usage & Efficiency pillar mined the candidate below from real sessions; the evidence is given, do not re-measure it.

Candidate: `{signature}`

- Class: {kind} (determinism score {score} of 5: {checks})
- Seen {occurrences} time(s) in {sessions} session(s) in the last 14 days
- Estimated net saving, in weighted tokens per week, from replaying its occurrences against the sessions they happened in: about ${net_if_called:.2f} if Claude calls the replacement (one turn instead of {turns}, and a short answer instead of the {added} tokens each occurrence added to the conversation, re-read by a median of {later} later requests); {if_triggered}. If the replacement makes Claude check on it once more each time, about ${net_low:.2f}.
- Proposed form: {proposed_form}
- Recommended schedule once automated: {frequency}, at {time_of_day} ({note})

Examples:

{examples}

What to build:

- Implement the proposed form in little-workshop: a job a user can start is a typed `JobSpec` in `src/dashboard/job_catalog.py`, never a free-form job; an MCP tool is a `ToolSpec` in `src/mcp_server/tools.py` over a Pillar Interface call of the pillar that owns the work; a trigger is an Orchestration trigger. The code must do the whole thing deterministically, with no Claude call in it.
- Announce the new route where Claude looks: `ROUTES` and the `workshop-lifecycle` skill in `src/registry/claude_templates.py`, and the tool's own description.
- Requirements first, then tests tagged with their ids, then the code, as this repository's rules say.
- If, looking at the code, the work turns out to need judgment after all, build nothing and say why in your report.
"""


def automate_job(candidate: dict, project: str) -> dict:
    """The fixed improvement task "Make it a task" starts on little-workshop for one candidate."""
    checks = ", ".join(f"{name.replace('_', ' ')} {'yes' if ok else 'no'}" for name, ok in (candidate.get("checks") or {}).items())
    examples = "\n".join(f"- {example['project']}: " + " ; ".join(f"`{call}`" for call in example["calls"])
                         for example in candidate.get("examples") or []) or "- (none kept)"
    schedule = candidate.get("schedule") or {}
    estimate = candidate.get("estimate") or {}
    description = AUTOMATE_DESCRIPTION.format(
        signature=candidate["signature"], kind=candidate["class"], score=candidate["score"], checks=checks,
        occurrences=candidate["occurrences"], sessions=candidate["sessions"],
        net_if_called=estimate.get("net_if_called", 0.0), turns=estimate.get("turns_per_occurrence", "?"),
        added=estimate.get("added_tokens_per_occurrence", "?"), later=estimate.get("later_requests_per_occurrence", "?"),
        net_low=estimate.get("net_if_called_low", 0.0),
        if_triggered=("about ${:.2f} if a trigger or a schedule runs it and Claude spends nothing".format(estimate["net_if_triggered"])
                      if estimate.get("net_if_triggered") is not None else "no trigger can run it: it is not work a pillar does"),
        proposed_form=candidate["proposed_form"], frequency=schedule.get("frequency", "weekly"),
        time_of_day=schedule.get("time_of_day", "02:00"), note=schedule.get("note", ""), examples=examples)
    return {"type": "agent.run_task", "target_pillar": "agent", "project_id": project, "queue_key": project,
            "params": {"title": AUTOMATE_TITLE, "intent": "improvement", "review_mode": "manual", "description": description}}


def tokens_avoided(conn: sqlite3.Connection, candidate: dict) -> float:
    """Times the replacement ran (calls stored whose signature starts with `replacement`) × what one run
    saves when Claude calls it (`median_cost`, the net-saving estimate): weighted tokens not spent. A run
    a trigger or a schedule makes is a Job, not a call, and is not counted here."""
    replacement = candidate.get("replacement")
    if not replacement:
        return 0.0
    escaped = replacement.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    runs = conn.execute("SELECT COUNT(*) FROM tool_calls WHERE signature LIKE ? ESCAPE '\\'", (escaped + "%",)).fetchone()[0]
    return runs * (candidate.get("median_cost") or 0.0)
