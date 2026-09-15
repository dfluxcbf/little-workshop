"""Invoking Claude Code headlessly under Remote Control — ported from
`little-runner`'s `claude_argv`/`parse_claude_output` (`issue_flow.py`).
"""

from __future__ import annotations

import json
import re
from typing import Any

STATUS_DONE, STATUS_PARTIAL, STATUS_FAILED = "done", "partial", "failed"
STATUS_RE = re.compile(r"^\s*AGENT_STATUS:\s*(done|partial|failed)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_status(summary: str) -> str:
    """Read the agent's final `AGENT_STATUS:` marker. A missing or
    unreadable marker is `partial`, never `done` — an ambiguous run can
    never trigger an automatic finish."""
    matches = STATUS_RE.findall(summary or "")
    return matches[-1].lower() if matches else STATUS_PARTIAL


def claude_argv(
    prompt: str, *, session_name: str | None = None, model: str | None = None,
    max_budget_usd: float | None = None, user_messages: bool = True, extra_args: tuple[str, ...] = (),
) -> list[str]:
    argv = ["claude", "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
    # Remote Control works in print mode: the run stays headless and
    # structured while the Claude app can watch it and answer it.
    if session_name:
        argv += ["--remote-control", session_name]
    if user_messages:
        argv += ["--brief"]
    if model:
        argv += ["--model", model]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    argv += list(extra_args)
    return argv


def parse_claude_output(stdout: str) -> tuple[str, bool, dict[str, Any]]:
    """`claude -p --output-format json` -> (result text, is_error, raw)."""
    text = stdout.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if data is None:
            return text, False, {}
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), data[-1] if data else {})
    result = data.get("result") if isinstance(data, dict) else None
    return (result if isinstance(result, str) else text), bool(isinstance(data, dict) and data.get("is_error")), data if isinstance(data, dict) else {}
