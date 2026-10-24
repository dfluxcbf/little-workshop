"""Plugin proposals: whole user requests Claude handles the same way, mostly without judgment, proposed as a
Claude Code plugin of hooks and scripts that calls Claude only where judgment was needed (docs/pillars/usage.md,
"Plugin proposals").

Automation mining (`mining.py`) looks at short runs of calls; this looks at whole **requests**. One prompt can
hold several (a long prompt may start a branch, write code, finish it and start the next), so a request is
carved around each **purposeful action**, a state-changing call that is not housekeeping (a loop, a wait, a
`git add`): the action, the steps code could take that come right before it (preparing: tests, markers, a
commit) and the reads and waits right after it. A judgment call ends the request, unless it lies between two
runs of the same step, as a fix between a failing test run and the next one: that is a **fallback point**.

Requests are grouped by the **family** of their action, with the variants of one action taken as one
(`workshop_version_feature_finish`, `workshop_version_hotfix_finish` and `lws version improvement-finish` are all
`version_finish`), so that one plugin covers every variant. A group is proposed when judgment was needed in at
most `MAX_FALLBACK_RATE` of its requests. Nothing here uses Claude, and nothing is built from it: a person
decides."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import date, timedelta
from statistics import median

from usage import counterfactual, mining, signatures
from usage.pricing import Price

MAX_FALLBACK_RATE = 0.25  # Claude needed in at most one request in four: "only in rare occasions"
CORE_SHARE = 0.25  # a step in at least this share of a group's requests is the plugin's; rarer ones are listed apart
MAX_FALLBACK_CALLS = 8  # the longest run of judgment calls taken for a fallback inside a request
MARKETPLACE = "little-workshop"  # the local marketplace the plugins are installed from
SCOPE_ALL = "all-projects"
# Calls a plugin makes no use of: loading a tool's schema, a session's to-do list. They are neither steps nor judgment.
NOISE = ("ToolSearch(", "TodoWrite(")
# Shell reads a script makes as well as Claude (the state of the checkout); other reads look at content, which is judgment.
SCRIPTABLE_LOOKS = ("git status", "git diff", "git log", "git show", "git branch", "git rev-parse", "git ls-files",
                    "ls", "pwd", "which", "wc", "echo", "cd")
# Commands that run another one: the family is the command they run.
WRAPPERS = {"timeout", "nohup", "env", "time", "nice", "stdbuf", "command", "exec", "xargs"}
# Steps no request exists for: loops, waits, stopping processes, git housekeeping. They can be steps, never the purpose.
NOT_A_PURPOSE = re.compile(r"^(for|while|until|if|sleep|kill|pkill|killall|true|test|\[|mkdir|rm|cp|mv|touch|chmod|"
                           r"git (add|stash|checkout|restore|rm|mv|reset|fetch)|Monitor|[A-Z]\w*)\b")
# A family ending in one of these words reads or waits, as a pillar's read calls do (`mining._READ`).
READS = re.compile(r"(^|[_ ])(status|list|get|report|summary|findings|candidates|results|winning|evaluate|branches|"
                   r"history|wait|config|audit|help|show|log|diff|grep)$")
# A script Claude wrote for the occasion and ran from a scratch directory: its text is content, as an inline one's is.
SCRATCH = ("/tmp/", "$TMPDIR", "/var/tmp/")
LOOPS = ("for", "while", "until")
# Commands that only print or pause before the one that does the work (`date; lws job list`).
PREAMBLE = {"date", "echo", "printf", "sleep", "true", "clear"}
# Shell keywords that are not commands (`for …; do …; done`).
KEYWORDS = {"do", "done", "then", "else", "elif", "fi", "break", "continue", "[", "[[", "]", "]]", "!", "{", "}"}
SHELLS = ("bash", "sh")
MIN_CALLS = 2  # a request of one call is a single command, which the automation candidates already cover
# A commit's message is content Claude writes, but a script writes it from rules (the subject the commit policies
# want); every other here-document is content only Claude writes.
SCRIPTED_CONTENT = ("git commit",)
# The words that make variants of one action: removed from a tool's name to form its family.
VARIANT_WORDS = {"feature", "improvement", "hotfix", "changed"}
# A plain name for the families the user knows by another one.
NAMES = {"version_finish": "finish-branch"}
FREQUENT_DAYS_SHARE = 0.6


def _reduce(name: str) -> str:
    """A pillar call's name without the words that make its variants: `version_improvement_finish` -> `version_finish`."""
    name = name[len("workshop_"):] if name.startswith("workshop_") else name
    return "_".join(word[:-1] if word == "pillars" else word for word in name.replace("-", "_").split("_")
                    if word and word not in VARIANT_WORDS)


def _command_words(command: str) -> list[str]:
    """The words of the first simple command that does work, past any `cd`/`export` and wrapper (`timeout 300`),
    without redirections (`2>&1`, `>/dev/null`)."""
    text = command.split("<<", 1)[0]
    segments = [segment for segment in signatures._SEPARATOR.split(text) if segment.strip()]
    for index, segment in enumerate(segments):
        words = [word.lstrip("(").strip("'\"") for word in signatures._words(segment) if not re.match(r"^\d*[<>]", word)]
        words = [word for word in words if word]
        while words and (words[0] in WRAPPERS or words[0].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[0])):
            words = words[1:]
        while words and words[0] in KEYWORDS:
            words = words[1:]
        if not words or words[0] in signatures._SETUP_WORDS or (words[0] in PREAMBLE and index < len(segments) - 1):
            continue
        if words[0] == "git":  # `git -C <dir> status` is `git status`
            rest, kept = words[1:], ["git"]
            while rest:
                word, rest = rest[0], rest[1:]
                if word in ("-C", "-c") and rest:
                    rest = rest[1:]
                elif not word.startswith("-"):
                    kept.append(word)
            words = kept
        return words
    return []


def _shell_script(command: str) -> str | None:
    """The command line `bash -lc 'cd x && command'` runs, or None when it is not such a call."""
    if "<<" in command:
        return None
    words = _command_words(command)
    if words[:1] and words[0] in SHELLS:
        quoted = next((word for word in words[1:] if " " in word), None)
        if quoted is not None:
            return quoted
        # A command line too long to be read whole, or with quotes inside quotes: what follows the opening quote.
        found = re.search(r"\b(?:bash|sh)\s+(?:-[-\w]+\s+)*['\"](.+)", command, re.S)
        return found.group(1).rstrip("'\"") if found else None
    return None


def shell_family(command: str) -> str:
    """`timeout 900 lws version improvement-finish --project-id x` -> `version_finish`; `python3 -m pytest tests/unit`
    -> `python3 -m pytest`; `git push origin dev` -> `git push`."""
    words = _command_words(command)
    if not words:
        return ""
    if words[0] in LOOPS and re.search(r"\bdo\b", command):
        return shell_family(re.split(r"\bdo\b", command, 1)[1])  # a loop is named after what it repeats
    head = words[0].rsplit("/", 1)[-1] if words[0].endswith("/lws") else words[0]
    script = _shell_script(command)
    if script is not None:
        return shell_family(script)  # bash -lc 'cd x && command': the command it runs
    rest = [word for word in words[1:] if not word.startswith("-")]
    if head == "lws":
        return _reduce("_".join(rest[:2])) if rest else "lws"
    if head in signatures._INTERPRETERS:
        if "-c" in words[1:3] or "<<" in command:
            return f"{head} <inline>"
        if words[1:2] == ["-m"] and len(words) > 2:
            return f"{head} -m {words[2]}"
        return f"{head} {rest[0]}" if rest else head
    return " ".join([head, *rest[:1]]) if rest and not rest[0].startswith(("<", "/", ".", "~", "$")) else head


def family(call: dict) -> str:
    """The action a call stands for, with its variants taken as one."""
    signature = call["signature"]
    if signature.startswith("mcp:"):
        return _reduce(signature[len("mcp:"):].split("(", 1)[0])
    if signature.startswith("Bash("):
        return shell_family(call.get("detail") or signature[len("Bash("):-1])
    return signature.split("(", 1)[0]


def is_noise(call: dict) -> bool:
    return call["signature"].startswith(NOISE)


def is_step(call: dict) -> bool:
    """A call code could make in Claude's place: an MCP call, a shell command that does work, or one that reads the
    checkout's state. A here-document is content Claude wrote, unless a script writes it from rules (a commit)."""
    signature = call["signature"]
    if is_noise(call):
        return False
    command = call.get("detail") or ""
    script = _shell_script(command) if signature.startswith("Bash(") else None
    if script is not None:
        return is_step({"signature": signatures.bash_signature(script), "detail": script})
    if signature.startswith("Bash(") and "<<" in command and not any(word in command for word in SCRIPTED_CONTENT):
        return False
    words = _command_words(command) if signature.startswith("Bash(") else []
    if words[:1] and words[0] in signatures._INTERPRETERS and any(word.startswith(SCRATCH) for word in words[1:3]):
        return False
    if mining.actionable(signature):
        return True
    inner = signature[len("Bash("):-1] if signature.startswith("Bash(") else None
    return inner is not None and any(inner == word or inner.startswith(word + " ") for word in SCRIPTABLE_LOOKS)


def is_judgment(call: dict) -> bool:
    return not is_noise(call) and not is_step(call)


def is_purpose(call: dict) -> bool:
    """A step a request exists for: it changes something, and it is not housekeeping."""
    if not is_step(call) or not mining.actionable(call["signature"]) or mining._READ.search(call["signature"]):
        return False
    name = family(call)
    looks = name.split(" ", 1)[0] in mining.LOOKING_SHELL
    return bool(name) and not looks and not READS.search(name) and not NOT_A_PURPOSE.match(name)


def _extend(calls: list[dict], index: int, step: int, key: str) -> int:
    """How far the request around `calls[index]` reaches in one direction (`step` -1 or +1): over steps (after the
    action, only reads and waits, or the action again), and over a run of judgment calls lying between two runs
    of the same step."""
    edge, j, n = index, index + step, len(calls)
    while 0 <= j < n:
        call = calls[j]
        if is_step(call):
            if step > 0 and is_purpose(call) and family(call) != key:
                break  # the next request's action
            edge, j = j, j + step
            continue
        k = j
        while 0 <= k < n and is_judgment(calls[k]):
            k += step
        run = abs(k - j)
        if run > MAX_FALLBACK_CALLS or not 0 <= k < n or family(calls[k]) != family(calls[j - step]):
            break
        edge, j = k, k + step
    return edge


def requests_of(calls: list[dict]) -> list[tuple[str, list[dict]]]:
    """The requests in one prompt's calls: (family, calls) for each purposeful action, an action repeated inside
    its own request (a retry, a `--continue`) being one request."""
    calls = [call for call in calls if not is_noise(call)]
    found, covered = [], {}
    for index, call in enumerate(calls):
        if not is_purpose(call):
            continue
        key = family(call)
        if index <= covered.get(key, -1):
            continue
        start, end = _extend(calls, index, -1, key), _extend(calls, index, 1, key)
        found.append((key, calls[start:end + 1]))
        covered[key] = end
    return found


def fallbacks(segment: list[dict]) -> list[dict]:
    """The runs of judgment calls in a request, each with the step before it and whether that step failed."""
    runs, current, before = [], None, None
    for call in segment:
        if is_noise(call):
            continue
        if is_judgment(call):
            if current is None:
                current = {"after": family(before) if before else "start",
                           "after_failed": bool(before) and before.get("success") == 0, "calls": []}
                runs.append(current)
            current["calls"].append(call)
        else:
            current, before = None, call
    return runs


def plugin_name(key: str) -> str:
    return NAMES.get(key) or re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


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


def _steps(key: str, requests: list[dict]) -> tuple[list[dict], list[dict]]:
    """The steps a group's requests took, in the order they usually come: the core ones (the action, and those in at
    least `CORE_SHARE` of the requests) and the rare ones."""
    counts: Counter = Counter()
    positions: dict[str, list[float]] = {}
    variants: dict[str, set] = {}
    for request in requests:
        steps = [call for call in request["calls"] if is_step(call)]
        for index, call in enumerate(steps):
            name = family(call)
            positions.setdefault(name, []).append(index / max(len(steps) - 1, 1))
            variants.setdefault(name, set()).add(call["signature"])
        counts.update({family(call) for call in steps})
    rows = [{"step": name, "share": round(count / len(requests), 3), "occurrences": count, "variants": sorted(variants[name])[:5]}
            for name, count in counts.items()]
    rows.sort(key=lambda row: (median(positions[row["step"]]), -row["occurrences"], row["step"]))
    core = [row for row in rows if row["step"] == key or row["share"] >= CORE_SHARE]
    return core, [row for row in rows if row not in core]


def propose(conn: sqlite3.Connection, prices: dict[str, Price], *, today: date | None = None, days: int = mining.PERIOD_DAYS,
            replay: "counterfactual.Replay | None" = None, calibration: "counterfactual.Calibration | None" = None) -> dict:
    """The plugin proposals of the last `days`, ranked by the net saving per week, and the groups left out because
    Claude's judgment was needed too often: {"proposals": [...], "too_much_judgment": [...]}."""
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    weeks = days / 7
    replay = replay or counterfactual.Replay(conn, prices)
    calibration = calibration or counterfactual.calibrate(conn, replay)
    # The plugin's command is in every session's starting context, as a new tool's name is.
    fixed_weekly = mining.TOOL_NAME_TOKENS * replay.requests_between(start, end) / weeks * replay.machine_read_price
    groups: dict[str, list[dict]] = {}
    for episode in mining.episodes(conn, start, end):
        for key, segment in requests_of(episode["calls"]):
            groups.setdefault(key, []).append({**episode, "calls": segment})
    proposals, too_much = [], []
    for key, requests in groups.items():
        sessions = {request["session_id"] for request in requests}
        if len(sessions) < mining.MIN_SESSIONS and len(requests) < mining.MIN_OCCURRENCES:
            continue
        runs = {id(request): fallbacks(request["calls"]) for request in requests}
        rate = sum(1 for request in requests if runs[id(request)]) / len(requests)
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
            calls_per.append(len(request["calls"]))
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
        if not turns or median(calls_per) < MIN_CALLS:
            continue
        net = (today_cost - with_cost) / weeks - fixed_weekly
        if net < mining.MIN_WEEKLY_SAVING:
            continue
        core, rare = _steps(key, requests)
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
            "steps": core, "rare_steps": rare,
            "fallbacks": sorted(({"after": p["after"], "occurrences": p["occurrences"], "rate": round(p["occurrences"] / len(requests), 3),
                                  "after_failed": p["after_failed"], "tools": dict(p["tools"].most_common()),
                                  "calls_median": median(p["calls"]), "weekly_cost": sum(p["costs"]) / weeks}
                                 for p in points.values()), key=lambda p: -p["occurrences"]),
            "projects": dict(projects.most_common()),
            "install": install(name, scope),
            "examples": [{"session_id": request["session_id"], "project": request["project"],
                          "calls": [(call["detail"] or call["signature"])[:200] for call in request["calls"]]}
                         for request in requests[:2]],
        })
    proposals.sort(key=lambda proposal: (-proposal["weekly_value"], -proposal["occurrences"], proposal["name"]))
    return {"proposals": proposals, "too_much_judgment": sorted(too_much, key=lambda group: -group["occurrences"])}


def absorbed(proposal: dict, candidates: list[dict]) -> list[str]:
    """The automation candidates the plugin would take over: every call of the candidate is one of its steps."""
    families = {step["step"] for step in proposal["steps"]}
    return [candidate["id"] for candidate in candidates
            if all(family({"signature": part}) in families for part in candidate["signature"].split(" → "))]
