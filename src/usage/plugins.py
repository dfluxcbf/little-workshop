"""Plugin proposals: whole user requests Claude handles the same way, mostly without judgment, proposed as a
Claude Code plugin of hooks and scripts that calls Claude only where judgment was needed (docs/pillars/usage.md,
"Plugin proposals").

Automation mining (`mining.py`) looks at short runs of calls; this looks at the whole **request**: one prompt's
calls from the first one code could make to the last one that is not a judgment call (what came before is the
user's own work, such as writing code). Requests are grouped by the **family** of their last state-changing
call, the action they exist for, with the variants of one action taken as one (`workshop_version_feature_finish`
and `workshop_version_hotfix_finish` are both `version_finish`), so that one plugin covers every variant.

Within a request each call is a **step** code can take (an MCP call, a shell command that does work or reads
state) or a **judgment** call (reading or writing files, free text, an inline script, a skill): only judgment
needs Claude. A run of judgment calls is a **fallback point**, named after the step before it. A group is
proposed when judgment was needed in at most `MAX_FALLBACK_RATE` of its requests. Nothing here uses Claude, and
nothing is built from it: a person decides."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from statistics import median

from usage import counterfactual, mining
from usage.pricing import Price

MAX_FALLBACK_RATE = 0.25  # Claude needed in at most one request in four: "only in rare occasions"
MARKETPLACE = "little-workshop"  # the local marketplace the plugins are installed from
SCOPE_ALL = "all-projects"
# Calls a plugin makes no use of: loading a tool's schema, a session's to-do list. They are neither steps nor judgment.
NOISE = ("ToolSearch(", "TodoWrite(")
# Shell reads a script makes as well as Claude (the state of the checkout); other reads look at content, which is judgment.
SCRIPTABLE_LOOKS = ("git status", "git diff", "git log", "git show", "git branch", "git rev-parse", "git ls-files",
                    "ls", "pwd", "which", "wc", "echo", "cd")
# The words that make variants of one action: removed from an MCP tool's name to form its family.
VARIANT_WORDS = {"feature", "improvement", "hotfix", "changed"}
# A plain name for the families the user knows by another one.
NAMES = {"version_finish": "finish-branch"}
FREQUENT_DAYS_SHARE = 0.6


def _shell(signature: str) -> str | None:
    return signature[len("Bash("):-1] if signature.startswith("Bash(") else None


def is_noise(signature: str) -> bool:
    return signature.startswith(NOISE)


def is_step(signature: str) -> bool:
    """A call code could make in Claude's place: an MCP call, a shell command that does work, or one that reads the checkout's state."""
    if is_noise(signature):
        return False
    if mining.actionable(signature):
        return True
    inner = _shell(signature)
    return inner is not None and any(inner == word or inner.startswith(word + " ") for word in SCRIPTABLE_LOOKS)


def is_judgment(signature: str) -> bool:
    return not is_noise(signature) and not is_step(signature)


def changes_state(signature: str) -> bool:
    """A step that does something, not one that reads: what a request exists for."""
    return mining.actionable(signature) and not mining._READ.search(signature)


def family(signature: str) -> str:
    """The action a call stands for, with its variants taken as one: `mcp:workshop_version_hotfix_finish(project_id)` ->
    `version_finish`, `Bash(python3 -m pytest <path>)` -> `python3 -m pytest`, `Bash(git push origin)` -> `git push`."""
    if signature.startswith("mcp:"):
        name = signature[len("mcp:"):].split("(", 1)[0]
        name = name[len("workshop_"):] if name.startswith("workshop_") else name
        words = [word[:-1] if word == "pillars" else word for word in name.split("_") if word not in VARIANT_WORDS]
        return "_".join(words)
    inner = _shell(signature)
    if inner is None:
        return signature.split("(", 1)[0]
    kept, named = [], 0
    for word in inner.split():
        if word.startswith("<"):
            break
        kept.append(word)
        if not word.startswith("-"):
            named += 1
        if named == 2:
            break
    return " ".join(kept)


def plugin_name(key: str) -> str:
    return NAMES.get(key) or re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def request_of(calls: list[dict]) -> list[dict] | None:
    """The request in one prompt's calls: from the first step code could make to the last call that is not
    judgment. None when the prompt changed nothing (it only looked around or wrote content)."""
    first = next((i for i, call in enumerate(calls) if mining.actionable(call["signature"])), None)
    if first is None:
        return None
    last = max(i for i, call in enumerate(calls) if is_step(call["signature"]))
    segment = calls[first:last + 1]
    return segment if any(changes_state(call["signature"]) for call in segment) else None


def fallbacks(segment: list[dict]) -> list[dict]:
    """The runs of judgment calls in a request, each with the step before it ("start" when there is none) and
    whether that step failed."""
    runs, current, before = [], None, None
    for call in segment:
        signature = call["signature"]
        if is_noise(signature):
            continue
        if is_judgment(signature):
            if current is None:
                current = {"after": family(before["signature"]) if before else "start",
                           "after_failed": bool(before) and before.get("success") == 0, "calls": []}
                runs.append(current)
            current["calls"].append(call)
        else:
            current, before = None, call
    return runs


def _tokens(replay: counterfactual.Replay, calls: list[dict]) -> int | None:
    """The tokens the request's own turns processed: each turn's context plus what it wrote."""
    first, last = replay.locate(calls[0]), replay.locate(calls[-1])
    if first is None or last is None or first[0] != last[0]:
        return None
    thread = replay.threads[first[0]]
    i1, ik = first[1], max(first[1], last[1])
    return int(sum(thread["ctx"][i1:ik + 1]) + sum(thread["out"][i1:ik + 1]))


def frequency(days_seen: set[str], period_days: int, occurrences: int) -> str:
    if len(days_seen) >= 4 and len(days_seen) >= FREQUENT_DAYS_SHARE * period_days:
        return "daily"
    return "weekly" if occurrences / (period_days / 7) >= 1 else "occasional"


def install(name: str, scope: str) -> dict:
    """How the plugin is installed once built, with Claude Code's own plugin commands, from the local marketplace."""
    everywhere = scope == SCOPE_ALL
    return {
        "marketplace": MARKETPLACE,
        "scope": "user" if everywhere else "project",
        "where": "every project (user scope)" if everywhere else f"the {scope.split(':', 1)[1]} checkout (project scope)",
        "command": f"claude plugin install {name}@{MARKETPLACE} --scope {'user' if everywhere else 'project'}",
        "list_command": "claude plugin list",
    }


def propose(conn: sqlite3.Connection, prices: dict[str, Price], *, today: date | None = None, days: int = mining.PERIOD_DAYS,
            replay: "counterfactual.Replay | None" = None, calibration: "counterfactual.Calibration | None" = None) -> dict:
    """The plugin proposals of the last `days`, ranked by the net saving per week, and how many groups were
    left out because Claude's judgment was needed too often: {"proposals": [...], "too_much_judgment": [...]}."""
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    weeks = days / 7
    replay = replay or counterfactual.Replay(conn, prices)
    calibration = calibration or counterfactual.calibrate(conn, replay)
    # The plugin's command is in every session's starting context, as a new tool's name is.
    fixed_weekly = mining.TOOL_NAME_TOKENS * replay.requests_between(start, end) / weeks * replay.machine_read_price
    groups: dict[str, list[dict]] = {}
    for episode in mining.episodes(conn, start, end):
        segment = request_of(episode["calls"])
        if segment is None:
            continue
        anchor = [call for call in segment if changes_state(call["signature"])][-1]
        groups.setdefault(family(anchor["signature"]), []).append({**episode, "calls": segment})
    proposals, too_much = [], []
    for key, requests in groups.items():
        sessions = {request["session_id"] for request in requests}
        if len(sessions) < mining.MIN_SESSIONS and len(requests) < mining.MIN_OCCURRENCES:
            continue
        runs = {id(request): fallbacks(request["calls"]) for request in requests}
        judged = sum(1 for request in requests if runs[id(request)])
        rate = judged / len(requests)
        if rate > MAX_FALLBACK_RATE:
            too_much.append({"family": key, "occurrences": len(requests), "fallback_rate": round(rate, 3)})
            continue
        today_cost, with_cost, tokens, turns, calls_per = 0.0, 0.0, [], [], []
        points: dict[str, dict] = {}
        for request in requests:
            replayed = replay.occurrence(request["calls"], calibration, trigger_ok=True)
            if replayed is None:
                continue
            today_cost += replayed["triggered"]
            turns.append(replayed["turns"])
            calls_per.append(sum(1 for call in request["calls"] if not is_noise(call["signature"])))
            counted = _tokens(replay, request["calls"])
            if counted is not None:
                tokens.append(counted)
            seen_here = set()
            for run in runs[id(request)]:
                cost = (replay.occurrence(run["calls"], calibration, trigger_ok=True) or {}).get("triggered") or 0.0
                with_cost += cost
                point = points.setdefault(run["after"], {"after": run["after"], "occurrences": 0, "after_failed": 0,
                                                         "tools": Counter(), "calls": [], "costs": []})
                if run["after"] not in seen_here:
                    point["occurrences"] += 1
                    seen_here.add(run["after"])
                point["after_failed"] += int(run["after_failed"])
                point["tools"].update(call["tool"] for call in run["calls"])
                point["calls"].append(len(run["calls"]))
                point["costs"].append(cost)
        if not turns:
            continue
        net = (today_cost - with_cost) / weeks - fixed_weekly
        if net < mining.MIN_WEEKLY_SAVING:
            continue
        step_counts: Counter = Counter()
        positions: dict[str, list[float]] = {}
        variants: dict[str, set] = {}
        for request in requests:
            steps = [call for call in request["calls"] if is_step(call["signature"])]
            for index, call in enumerate(steps):
                name = family(call["signature"])
                positions.setdefault(name, []).append(index / max(len(steps) - 1, 1))
                variants.setdefault(name, set()).add(call["signature"])
            step_counts.update({family(call["signature"]) for call in steps})
        steps = [{"step": name, "share": round(count / len(requests), 3), "occurrences": count, "variants": sorted(variants[name])[:5]}
                 for name, count in step_counts.items()]
        steps.sort(key=lambda step: (median(positions[step["step"]]), -step["occurrences"], step["step"]))
        projects = Counter(request["project"] for request in requests if request["project"] != "unattributed")
        scope = SCOPE_ALL if len(projects) >= 2 else f"project:{next(iter(projects), 'unattributed')}"
        name = plugin_name(key)
        days_seen = {request["calls"][0]["ts"][:10] for request in requests}
        proposals.append({
            "name": name, "family": key, "scope": scope, "weekly_value": net,
            "occurrences": len(requests), "sessions": len(sessions),
            "usage": {
                "per_week": round(len(requests) / weeks, 2), "frequency": frequency(days_seen, days, len(requests)),
                "calls_per_occurrence": median(calls_per), "turns_per_occurrence": median(turns),
                "tokens_per_occurrence": int(median(tokens)) if tokens else None,
                "today_weekly": today_cost / weeks, "with_plugin_weekly": with_cost / weeks + fixed_weekly,
                "fixed_weekly": fixed_weekly, "fallback_rate": round(rate, 3),
            },
            "steps": steps,
            "fallbacks": sorted(({"after": p["after"], "occurrences": p["occurrences"], "rate": round(p["occurrences"] / len(requests), 3),
                                  "after_failed": p["after_failed"], "tools": dict(p["tools"].most_common()),
                                  "calls_median": median(p["calls"]), "weekly_cost": sum(p["costs"]) / weeks}
                                 for p in points.values()), key=lambda p: -p["occurrences"]),
            "projects": dict(projects.most_common()),
            "install": install(name, scope),
            "examples": [{"session_id": request["session_id"], "project": request["project"],
                          "calls": [call["detail"] or call["signature"] for call in request["calls"] if not is_noise(call["signature"])]}
                         for request in requests[:2]],
        })
    proposals.sort(key=lambda proposal: (-proposal["weekly_value"], -proposal["occurrences"], proposal["name"]))
    return {"proposals": proposals, "too_much_judgment": sorted(too_much, key=lambda group: -group["occurrences"])}


def absorbed(proposal: dict, candidates: list[dict]) -> list[str]:
    """The automation candidates the plugin would take over: every call of the candidate is one of its steps."""
    families = {step["step"] for step in proposal["steps"]}
    return [candidate["id"] for candidate in candidates
            if all(family(part) in families for part in candidate["signature"].split(" → "))]
