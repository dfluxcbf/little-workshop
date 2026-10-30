"""The Claude task that resolves a conflict between two requirements, as the user decided it on the Dashboard:
the older requirement wins, the newer one wins, or another approach the user describes. The conflict itself is
found by Requirements & Quality (`requirements/conflicts.py`); a later analysis that no longer finds it marks it
resolved, so the task only has to remove the conflict, not report on it."""

from __future__ import annotations

# Mirrors `requirements.app.CONFLICT_DECISIONS` (no shared import between two pillars' `src/` trees).
DECISIONS = {
    "older_wins": "The older requirement wins",
    "newer_wins": "The newer requirement wins",
    "other": "Another approach",
}

_KINDS = {
    "duplicate_key": "Two requirements carry the same key.",
    "contradiction": "One requirement requires what the other forbids.",
    "different_value": "Both requirements state the same rule with different values.",
}


def decision_error(decision: str, guidance: str) -> str | None:
    if decision not in DECISIONS:
        return f"decision must be one of {', '.join(DECISIONS)}"
    if decision == "other" and not guidance.strip():
        return "another approach needs guidance on what to do"
    return None


def _requirement(label: str, key: str, text: str, current: dict | None) -> str:
    if current is None:
        return f"{label} requirement {key} (deleted since; it said: {text})"
    return (f"{label} requirement {current['key']} — {current['title']} ({current['status']}, created "
            f"{current['created_at'][:10]}, id {current['id']}):\n{current['description'] or text}")


def conflict_task(conflict: dict, decision: str, guidance: str) -> tuple[str, str]:
    """(title, description) of the `agent.run_task` that resolves `conflict` (as `GET /v1/conflicts/{id}`
    answers it) the way the user decided."""
    older, newer = conflict.get("older"), conflict.get("newer")
    older_key, newer_key = conflict["older_key"], conflict["newer_key"]
    if decision == "older_wins":
        what = (f"The older requirement {older_key} wins: change or remove {newer_key} so that it no longer "
                f"conflicts with {older_key}, and bring the code, tests and documentation that implement "
                f"{newer_key}'s conflicting part in line with {older_key}.")
    elif decision == "newer_wins":
        what = (f"The newer requirement {newer_key} wins: change or remove {older_key} so that it no longer "
                f"conflicts with {newer_key}, and bring the code, tests and documentation that implement "
                f"{older_key}'s conflicting part in line with {newer_key}.")
    else:
        what = f"Resolve it this way, as the user asked:\n{guidance.strip()}"
    if conflict["kind"] == "duplicate_key":
        what += (" Two requirements can never share a key: whichever one stays keeps it, and one that stays "
                 "besides it gets the next free key, in the pillar and wherever the old key is cited.")
    description = "\n\n".join([
        f"Requirements & Quality found a conflict between two requirements of this project. "
        f"{_KINDS.get(conflict['kind'], '')} In short: {conflict['summary']}.",
        _requirement("Older", older_key, conflict["older_text"], older),
        f"In conflict with:\n{_requirement('Newer', newer_key, conflict['newer_text'], newer)}",
        f"The parts in conflict:\n- {older_key}: {conflict['older_text']}\n- {newer_key}: {conflict['newer_text']}",
        what,
        "Change requirements only through the Requirements & Quality pillar (the workshop_requirements_* tools "
        "or `lws requirements`), never its database: a draft can be edited, a published requirement is locked, "
        "so it is deleted and, where some of it still holds, replaced by a new requirement with the next free "
        "key. Keep the project's own requirements register (docs/requirements.md, where there is one) the same "
        "as the pillar. The conflict is resolved once Requirements & Quality's next analysis no longer finds it "
        f"(conflict {conflict['id']}).",
    ])
    return f"Resolve requirement conflict {older_key} / {newer_key}", description
