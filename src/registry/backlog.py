"""A project's backlog: entries the owner wants built or fixed, each refined with Claude's help into a
task (docs/pillars/registry_portfolio.md, "Backlog").

An entry holds four things: the primary entry (what the owner first wrote, as written), the owner's
refinement notes, one refined entry and one task prompt. Refining replaces the refined entry, and
generating replaces the task prompt: there is only ever one of each. Deleting an entry deletes all of
it, whoever deletes it (the Dashboard, `lws`, an MCP tool, or Claude at the end of the task).

Claude writes the refined entry from the primary entry, the current refined entry and the notes, and
the task prompt from the refined entry alone; the instruction to delete the entry once the task is
done is appended here, word for word, rather than left to Claude to remember."""

from __future__ import annotations

import re

TITLE_LENGTH = 80

REFINE_PROMPT = """You refine one entry of the backlog of the software project "{project}". A backlog entry records something the project's owner wants built or fixed. Write its refined entry: a clear, self-contained description that an engineer, or Claude working alone, can act on.

Use what is below and nothing else:

- The primary entry is what the owner wrote first, as they wrote it. Keep every requirement, constraint and detail it states.
- The current refined entry, when there is one, is the last refinement: start from it.
- The owner's refinement notes say what to change, add, drop or clarify: apply every one of them. Where they disagree with the entries, the notes win.

Write it in Markdown, in this order: a short title as a `#` heading; the goal (why it is wanted); what to build or change, as concrete behaviour; acceptance criteria as a checklist; and, only if some remain, the open questions the owner must answer. Do not invent requirements the owner did not state or imply: where something is unclear, list it as an open question instead of deciding it. Answer with the refined entry only, with no preamble and no closing remarks.

<primary_entry>
{primary}
</primary_entry>

<current_refined_entry>
{refined}
</current_refined_entry>

<refinement_notes>
{notes}
</refinement_notes>
"""

TASK_PROMPT_PROMPT = """Write the prompt for an unattended Claude Code task in the software project "{project}", from the refined backlog entry below. Claude will work in the project's checkout with no conversation before it, so the prompt must stand on its own: what to build or fix and why, the behaviour expected, the acceptance criteria, and how to check the result (the tests to add or run). Follow the refined entry faithfully: add nothing it does not ask for, and leave its open questions as questions for Claude to raise in its report rather than decide. Address the instructions to Claude, in Markdown. Answer with the prompt only, with no preamble and no closing remarks, and do not mention the backlog: an instruction about it is added after your text.

<refined_entry>
{refined}
</refined_entry>
"""

DELETE_INSTRUCTION = """

## When you are done

This task comes from the backlog entry `{entry_id}` of the project `{project}`. Once the work is complete, delete that entry from the backlog so that it is not done twice: run `lws project backlog delete --project-id {project} {entry_id}`, or call the MCP tool `workshop_project_backlog_delete` with `project_id` "{project}" and `entry_id` "{entry_id}".
"""


class BacklogError(Exception):
    """The entry cannot be refined or prompted as asked; the message says why."""


def entry_title(entry: dict) -> str:
    """A card's title: the refined entry's heading once there is one, else the first line the owner wrote."""
    heading = re.search(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", entry.get("refined_entry") or "", re.M)
    first_line = next((s.strip() for s in (entry.get("primary_entry") or "").splitlines() if s.strip()), "")
    for line in ([heading.group(1)] if heading else []) + [first_line]:
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if line:
            return line if len(line) <= TITLE_LENGTH else line[: TITLE_LENGTH - 1].rstrip() + "…"
    return "(empty entry)"


def refine_prompt(project: str, entry: dict) -> str:
    primary = (entry.get("primary_entry") or "").strip()
    if not primary:
        raise BacklogError("the entry has no primary entry to refine")
    return REFINE_PROMPT.format(
        project=project, primary=primary, refined=(entry.get("refined_entry") or "").strip() or "(none yet)",
        notes=(entry.get("refinement_notes") or "").strip() or "(none)",
    )


def task_prompt_prompt(project: str, entry: dict) -> str:
    refined = (entry.get("refined_entry") or "").strip()
    if not refined:
        raise BacklogError("the entry has no refined entry yet: refine it first")
    return TASK_PROMPT_PROMPT.format(project=project, refined=refined)


def with_delete_instruction(prompt: str, project: str, entry_id: str) -> str:
    """Claude's prompt text, ending with the instruction to delete the entry once the task is done."""
    return prompt.strip() + DELETE_INSTRUCTION.format(project=project, entry_id=entry_id)
