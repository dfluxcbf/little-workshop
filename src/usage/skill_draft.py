"""Draft as a skill: an automation candidate or a plugin proposal becomes a draft Claude Code skill in a project's
`.claude/skills/<name>/SKILL.md`, written by a fixed agent task (intent `skill`, an improvement branch, manual review)
that the owner reviews (docs/pillars/usage.md, "Draft as a skill"). Nothing here uses Claude itself."""

from __future__ import annotations

import re
from collections import Counter

from common.task_queue import run_task_queue_key

DESCRIPTION = """Draft a Claude Code skill named `{name}` in this project's `.claude/skills/{name}/SKILL.md`, so that its sessions do this work the same way every time with fewer turns. The Usage & Efficiency pillar found it in real sessions; the evidence is given, do not re-measure it.

What the sessions repeated: {what}
- Seen {occurrences} time(s) in {sessions} session(s); estimated saving about ${weekly:.2f} a week.

Steps, in the order they usually came:

{steps}

Examples:

{examples}
"""


def skill_name(text: str) -> str:
    """A skill folder name from a proposal name or a candidate signature (`mcp:workshop_x_y(a) → ...` -> `x-y`)."""
    first = re.sub(r"^(mcp:)?(workshop_)?", "", text.split(" → ")[0].split("(")[0].strip())
    slug = re.sub(r"[^a-z0-9]+", "-", first.lower()).strip("-")
    return (slug or "repeated-work")[:40].strip("-")


def _examples(rows: list[dict]) -> str:
    return "\n".join(f"- {e['project']}: " + " ; ".join(f"`{call}`" for call in e["calls"]) for e in rows) or "- (none kept)"


def project_of(row: dict, default: str) -> str:
    """Where the draft goes when the caller names no project: a proposal's one project, else the project most of
    the examples came from, else `default`."""
    scope = row.get("scope") or ""
    if scope.startswith("project:"):
        return scope.split(":", 1)[1]
    projects = Counter(e["project"] for e in row.get("examples") or [] if e.get("project"))
    return projects.most_common(1)[0][0] if projects else default


def draft_job(kind: str, row: dict, project_id: str) -> dict:
    """The `agent.run_task` Job template drafting `row` (a `candidate` or a `proposal`) as a skill of `project_id`."""
    if kind == "proposal":
        name = skill_name(row["name"])
        what = f"the `{row['family']}` request ({row.get('usage', {}).get('frequency', 'weekly')})"
        steps = "\n".join(f"{n}. `{s['step']}` ({round(s.get('share', 1) * 100)}% of the requests)"
                          for n, s in enumerate(row.get("steps") or [], 1)) or "1. (not kept)"
    else:
        name = skill_name(row["signature"])
        what = f"`{row['signature']}` ({row.get('class', 'unclassified')}; proposed form: {row.get('proposed_form', '-')})"
        steps = "\n".join(f"{n}. `{part}`" for n, part in enumerate(row["signature"].split(" → "), 1))
    description = DESCRIPTION.format(name=name, what=what, occurrences=row.get("occurrences", 0), sessions=row.get("sessions", 0),
                                     weekly=row.get("weekly_value") or 0.0, steps=steps, examples=_examples(row.get("examples") or []))
    return {"type": "agent.run_task", "target_pillar": "agent", "project_id": project_id, "queue_key": run_task_queue_key(project_id, {"title": f"Draft skill {name}"}),
            "params": {"title": f"Draft skill {name}", "intent": "skill", "review_mode": "manual", "description": description}}
