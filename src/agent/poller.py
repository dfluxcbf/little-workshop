"""Cursor-file-based edge detection — ported from `little-runner`'s
`poller.py`/`poller_state.py`, now backed by this pillar's own
`poll_cursors` table instead of a loose JSON file. A poll only ever
sees a snapshot; the new-since-last-time edges a webhook would have
delivered are reconstructed by diffing each tick's snapshot against the
previous one. This is the mechanism behind "no inbound ports, no stored
token" — outbound-only polling can still behave like event delivery.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from agent import db
from agent.github_adapter import GitHubAdapter, GitHubError, actor_for_event


@dataclass
class TriggerEvent:
    number: int
    action: str  # "labeled" | "assigned"
    match: str  # the label name or login that matched
    sender: str | None  # the real actor, from the issue timeline


def detect_issue_triggers(
    issues: list[dict], cursor: dict, trigger_label: str, claude_logins: list[str]
) -> tuple[list[TriggerEvent], dict]:
    """Pure: given the current open-issues snapshot and the previous
    cursor, returns (new triggers, updated cursor) without touching the
    network or dispatching anything. `sender` is left None here — the
    caller resolves it via `actor_for_event` only for issues that are
    actually new triggers, so a quiet tick makes zero extra API calls."""
    labeled_now = {i["number"] for i in issues if any(l.get("name") == trigger_label for l in i.get("labels", []))}
    assigned_now: set[int] = set()
    if claude_logins:
        for issue in issues:
            if any(a.get("login") in claude_logins for a in issue.get("assignees", [])):
                assigned_now.add(issue["number"])

    triggers: list[TriggerEvent] = []
    newly_labeled = labeled_now - set(cursor["labeled"])
    for number in sorted(newly_labeled):
        triggers.append(TriggerEvent(number, "labeled", trigger_label, None))
    for number in sorted(assigned_now - set(cursor["assigned"]) - newly_labeled):
        issue = next(i for i in issues if i["number"] == number)
        login = next(a["login"] for a in issue.get("assignees", []) if a.get("login") in claude_logins)
        triggers.append(TriggerEvent(number, "assigned", login, None))

    new_cursor = {**cursor, "labeled": sorted(labeled_now), "assigned": sorted(assigned_now)}
    return triggers, new_cursor


def poll_tick(
    conn: sqlite3.Connection, adapter: GitHubAdapter, project_id: str, repo: str,
    *, trigger_label: str, claude_logins: list[str],
) -> list[TriggerEvent]:
    """One polling tick for one project: fetch the current snapshot,
    diff it against the saved cursor, resolve the real actor for each
    new trigger via the issue timeline, and persist the new cursor.
    Returns the triggers a caller should dispatch as `agent.run_task`
    Jobs — this function never dispatches anything itself."""
    try:
        issues = adapter.list_open_issues(repo)
    except GitHubError:
        return []

    cursor = db.get_poll_cursor(conn, project_id)
    triggers, new_cursor = detect_issue_triggers(issues, cursor, trigger_label, claude_logins)

    resolved: list[TriggerEvent] = []
    for trigger in triggers:
        sender = actor_for_event(adapter, repo, trigger.number, trigger.action, trigger.match)
        resolved.append(TriggerEvent(trigger.number, trigger.action, trigger.match, sender))

    db.save_poll_cursor(
        conn, project_id, labeled=new_cursor["labeled"], assigned=new_cursor["assigned"],
        comment_cursor=cursor["comment_cursor"],
    )
    return resolved
