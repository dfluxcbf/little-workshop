"""Invoking Claude Code headlessly under Remote Control — ported from
`little-runner`'s `claude_argv`/`parse_claude_output` (`issue_flow.py`).
"""

from __future__ import annotations

import json
import re
from typing import Any

# Every Claude the workshop launches (task jobs, project sessions, doc updates).
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"


def model_args(model: str | None = None, effort: str | None = None) -> list[str]:
    return ["--model", model or DEFAULT_MODEL, "--effort", effort or DEFAULT_EFFORT]


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
    minimal: bool = False, effort: str | None = None, session_id: str | None = None,
) -> list[str]:
    argv = ["claude", "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
    # A known session id names the run's transcript (to watch it) and its processes (to stop it).
    if session_id:
        argv += ["--session-id", session_id]
    # Remote Control works in print mode: the run stays headless and
    # structured while the Claude app can watch it and answer it.
    if session_name:
        argv += ["--remote-control", session_name]
    if user_messages:
        argv += ["--brief"]
    argv += model_args(model, effort)
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    if minimal:
        # A text-in/text-out call needs no MCP server and no skill: both only add to its startup context.
        argv += ["--strict-mcp-config", "--disable-slash-commands"]
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


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def usage_from_raw(raw: dict[str, Any]) -> dict[str, Any] | None:
    """What a run's result JSON says it used, or None when it carries no usage (unparseable output,
    a run killed before its result). The cost is Claude Code's estimate at list price, not a bill."""
    usage = raw.get("usage") if isinstance(raw, dict) else None
    if not isinstance(usage, dict):
        return None
    model_usage = raw.get("modelUsage") if isinstance(raw.get("modelUsage"), dict) else {}
    cost = raw.get("total_cost_usd")
    return {
        "session_id": raw.get("session_id") if isinstance(raw.get("session_id"), str) else None,
        # The model that produced the most output: a run's subagents may use others.
        "model": max(model_usage, key=lambda name: _count((model_usage[name] or {}).get("outputTokens")), default=None),
        "input_tokens": _count(usage.get("input_tokens")),
        "cache_read_tokens": _count(usage.get("cache_read_input_tokens")),
        "cache_write_tokens": _count(usage.get("cache_creation_input_tokens")),
        "output_tokens": _count(usage.get("output_tokens")),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
        "num_turns": _count(raw.get("num_turns")),
        "duration_ms": _count(raw.get("duration_ms")),
        "is_error": bool(raw.get("is_error")),
        "model_usage": model_usage,
    }


def with_env(argv: list[str], env: dict[str, str] | None) -> list[str]:
    """`argv` run with `env` added to its environment (through `env(1)`); unchanged when there is none."""
    return ["env", *(f"{key}={value}" for key, value in env.items()), *argv] if env else argv


def setenv_args(env: dict[str, str] | None) -> list[str]:
    """`env` as `systemd-run --setenv=` arguments."""
    return [f"--setenv={key}={value}" for key, value in (env or {}).items()]


def with_budget(usage: dict[str, Any] | None, budget_usd: float | None) -> dict[str, Any] | None:
    """The usage with the budget the run was given, so what it used of it can be shown."""
    if usage is None:
        return None
    return {**usage, "budget_usd": budget_usd}
