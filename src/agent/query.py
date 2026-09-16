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

import tempfile
from dataclasses import dataclass

from common.shell import CommandRunner, DEFAULT_RUNNER
from agent.claude_invoke import claude_argv, parse_claude_output

DEFAULT_QUERY_TIMEOUT_SECONDS = 300.0


@dataclass
class QueryResult:
    text: str
    is_error: bool


def run_query(prompt: str, *, runner: CommandRunner = DEFAULT_RUNNER, timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS) -> QueryResult:
    argv = claude_argv(prompt, user_messages=False)
    with tempfile.TemporaryDirectory(prefix="lws-agent-query-") as scratch:
        result = runner.run(argv, cwd=scratch, timeout=timeout)
    text, is_error, _raw = parse_claude_output(result.stdout)
    if not result.ok and not text:
        return QueryResult(text=(result.stderr or "claude exited non-zero with no output").strip(), is_error=True)
    return QueryResult(text=text, is_error=is_error)
