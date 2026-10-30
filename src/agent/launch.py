"""How each kind of Claude run is launched: its model, effort and budget
(docs/pillars/agentic_development.md, "Launch defaults per job type").

One default for everything hid the choice; a table makes it one line per job type, and
`<config>/agent/launch.json` overrides any of it without a code change:

    {"agent.query": {"model": "claude-haiku-4-5-20251001", "effort": "low", "max_budget_usd": 0.25}}

The budget is Claude Code's `--max-budget-usd`: an estimate at list price that stops a run which
keeps going, not money spent on a subscription."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from agent.claude_invoke import DEFAULT_EFFORT, DEFAULT_MODEL
from common.paths import Paths

LAUNCH_FILE = "launch.json"
JOB_RUN_TASK, JOB_QUERY, JOB_UPDATE_DOCS, JOB_SESSION = "agent.run_task", "agent.query", "agent.update_docs", "agent.session"
# Not a Job type of its own: an `agent.query` that reads a project's checkout (a backlog entry's
# refinement and task prompt), launched apart because it reads for several turns, not one request.
JOB_QUERY_CHECKOUT = "agent.query.checkout"


@dataclass(frozen=True)
class Launch:
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    max_budget_usd: float | None = None


DEFAULTS: dict[str, Launch] = {
    JOB_RUN_TASK: Launch(),
    # One request of text in, text out: a query that costs more than this has gone wrong.
    JOB_QUERY: Launch(max_budget_usd=1.0),
    # Reading the code it needs takes a few turns more, each re-reading what was read before.
    JOB_QUERY_CHECKOUT: Launch(max_budget_usd=3.0),
    JOB_UPDATE_DOCS: Launch(),
    JOB_SESSION: Launch(),
}


def launch_for(job_type: str, paths: Paths | None = None) -> Launch:
    """The job type's defaults with the owner's overrides; an unknown job type gets the plain default."""
    launch = DEFAULTS.get(job_type, Launch())
    if paths is None:
        return launch
    try:
        custom = json.loads((paths.pillar_config_dir("agent") / LAUNCH_FILE).read_text(encoding="utf-8")).get(job_type)
    except (OSError, ValueError, AttributeError):
        return launch
    if not isinstance(custom, dict):
        return launch
    fields = {}
    if isinstance(custom.get("model"), str) and custom["model"]:
        fields["model"] = custom["model"]
    if isinstance(custom.get("effort"), str) and custom["effort"]:
        fields["effort"] = custom["effort"]
    if "max_budget_usd" in custom:
        budget = custom["max_budget_usd"]
        fields["max_budget_usd"] = float(budget) if isinstance(budget, (int, float)) and not isinstance(budget, bool) else None
    return replace(launch, **fields)
