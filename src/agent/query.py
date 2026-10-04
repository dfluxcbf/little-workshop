"""`agent.query` — a narrowly-scoped, text-in/text-out Claude CLI call:
no branch, no checkout, no file writes, no Remote Control session,
nowhere near `agent.run_task`'s full branching/PR/review flow. Exists
for callers (Policy & Compliance's authoring pipeline, M12: EARS
refinement, conflict checking, test-script drafting) that need Claude's
judgment on plain text, never an actual code change — "a proposal, not
yet anything saved or executable against real state," as
`docs/pillars/policy_compliance.md` puts it. Still goes through this
pillar's own Pillar Interface, never reimplemented elsewhere — the same
"one owner per capability" rule every other cross-pillar call in this
project already follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.shell import CommandRunner, DEFAULT_RUNNER
from agent.claude_invoke import claude_argv, parse_claude_output, usage_from_raw, with_budget
from agent.launch import DEFAULTS, JOB_QUERY, Launch
from common.paths import Paths

DEFAULT_QUERY_TIMEOUT_SECONDS = 300.0


@dataclass
class QueryResult:
    text: str
    is_error: bool


# A query is one request: a 1-hour cache write costs twice the input price and nothing reads it after
# five minutes, the time consecutive authoring calls take.
QUERY_ENV = ("CLAUDE_CODE_PROMPT_CACHE_TTL=5m",)


def query_dir(paths: Paths) -> Path:
    """Where every query runs. Claude's prompt cache is scoped to the working directory, so one
    directory for all of them lets a query read what the one before it cached; nothing is written here."""
    path = paths.state_home / "agent-query"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_query(
    prompt: str, *, cwd: Path, runner: CommandRunner = DEFAULT_RUNNER, timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    on_usage: Callable[[dict | None], object] = lambda usage: None, env: dict[str, str] | None = None,
    launch: Launch | None = None,
) -> QueryResult:
    """`env` is the launch environment the Usage & Efficiency pillar gives (`usage_client.launch_env`)."""
    extra = [f"{key}={value}" for key, value in (env or {}).items()]
    launch = launch or DEFAULTS[JOB_QUERY]
    argv = ["env", *QUERY_ENV, *extra, *claude_argv(
        prompt, user_messages=False, minimal=True, model=launch.model, effort=launch.effort, max_budget_usd=launch.max_budget_usd)]
    result = runner.run(argv, cwd=cwd, timeout=timeout)
    text, is_error, raw = parse_claude_output(result.stdout)
    on_usage(with_budget(usage_from_raw(raw), launch.max_budget_usd))
    if not result.ok and not text:
        return QueryResult(text=(result.stderr or "claude exited non-zero with no output").strip(), is_error=True)
    return QueryResult(text=text, is_error=is_error)
