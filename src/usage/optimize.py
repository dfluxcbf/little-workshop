"""The "Optimize Claude usage" task: a fixed description plus a digest of the chosen findings that
this pillar writes itself, so the agent spends no tokens rediscovering the evidence
(docs/pillars/usage.md, "Applying an optimization")."""

from __future__ import annotations

import json

TITLE = "Optimize Claude usage"

DESCRIPTION = """Reduce the tokens Claude sessions spend in this project by addressing the findings below. little-workshop's Usage & Efficiency pillar measured them from real sessions; each one names what was measured, the evidence, and the recommended action. Do not re-measure anything: the evidence is given.

What you may change, in this project only:

- its briefing (`.claude/AGENTS.md` or `AGENTS.md`), path-scoped rules (`.claude/rules/`), skills (`.claude/skills/`) and subagents (`.claude/agents/`);
- the project's own entries in `.claude/settings.json` (for example a `permissions.deny` `Read(...)` rule for a heavy generated folder, or a hook of its own);
- its code, only where a finding calls for a small deterministic tool that replaces work Claude repeats by hand.

Rules:

- Never edit what little-workshop generates (`.claude/little-workshop.md`, `.claude/rules/claude-instructions.md`, the `workshop-lifecycle` and `policy-preflight` skills, `.claude/hooks/lws_scope_guard.py`, the generated entries of `.claude/settings.json`), and never add a `CLAUDE.md`. If a finding can only be fixed there, say so in your report: that fix belongs in little-workshop, for every project.
- Keep every instruction file under 200 lines. Move a procedure into a skill, and guidance for one part of the tree into a rule with `paths`, instead of growing the briefing.
- Change nothing a finding does not call for. A finding you decide not to act on is reported with the reason.
- In your report, list each finding with what you changed for it, or why you changed nothing.

Findings:

{digest}
"""


def digest(findings: list[dict]) -> str:
    """The findings as text, in the order given, the same text for the same findings."""
    blocks = []
    for finding in findings:
        evidence = json.dumps(finding.get("evidence") or {}, indent=2, sort_keys=True)
        value = finding.get("value")
        shown = f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
        blocks.append(
            f"### {finding['detector']}: {finding['title']}\n\n"
            f"- Measured: {finding['metric']} = {shown} "
            f"({finding.get('period_start')} to {finding.get('period_end')})\n"
            f"- Estimated saving: about ${finding.get('saving', 0):.2f} of weighted tokens per week\n"
            f"- Recommended action: {finding['action']}\n\n"
            f"Evidence:\n\n```json\n{evidence}\n```"
        )
    return "\n\n".join(blocks) if blocks else "(no findings were chosen)"


def task_job(project: str, findings: list[dict]) -> dict:
    """The one Orchestration Job the optimization runs: an agent task on a branch, reviewed by a human."""
    return {
        "type": "agent.run_task", "target_pillar": "agent", "project_id": project, "queue_key": project,
        "params": {"title": TITLE, "intent": "task", "review_mode": "manual", "include_standards": False,
                   "description": DESCRIPTION.format(digest=digest(findings))},
    }
