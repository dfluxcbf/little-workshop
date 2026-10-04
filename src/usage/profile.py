"""The desired Claude Code user profile: what `~/.claude/settings.json` must hold for every Claude
session on this machine to report to the intake (docs/pillars/usage.md, "Configuration").

It is the only file this pillar edits outside every repository, so nothing here writes without being
asked: `plan` says what would change, `apply` changes it after keeping a backup, and both leave every
entry the owner made alone."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

HOOK_PATH = "/v1/hooks/"
HTTP_HOOK_EVENTS = ("SessionEnd", "PreCompact", "PostCompact")
COMMAND_HOOK_EVENTS = ("SessionStart",)  # Claude Code 2.1.289 posts no `http` hook at session start
CLEANUP_PERIOD_DAYS = 90
STATUSLINE_FILE = "statusline.py"
# Prompt and response text stay out of the store (decision 10 of the proposal: off until decided).
FORBIDDEN_ENV = ("OTEL_LOG_USER_PROMPTS", "OTEL_LOG_ASSISTANT_RESPONSES")


def default_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def telemetry_env(port: int) -> dict[str, str]:
    """The variables that make a Claude session export to the intake; also the launch environment of
    every Claude little-workshop starts."""
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint(port),
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
        "OTEL_METRICS_INCLUDE_REPOSITORY": "true",
        "OTEL_LOG_TOOL_DETAILS": "1",
    }


def _hook(event: str, port: int) -> dict:
    url = f"{endpoint(port)}{HOOK_PATH}{event}"
    if event in COMMAND_HOOK_EVENTS:
        command = f"curl -s -m 2 -o /dev/null -H 'content-type: application/json' --data-binary @- {url} || true"
        return {"hooks": [{"type": "command", "command": command}]}
    return {"hooks": [{"type": "http", "url": url}]}


def desired(port: int, statusline_path: Path) -> dict:
    return {
        "env": telemetry_env(port),
        "hooks": {event: [_hook(event, port)] for event in (*COMMAND_HOOK_EVENTS, *HTTP_HOOK_EVENTS)},
        "statusLine": {"type": "command", "command": f"python3 {statusline_path}"},
        "cleanupPeriodDays": CLEANUP_PERIOD_DAYS,
    }


def _is_ours(entry) -> bool:
    return isinstance(entry, dict) and any(
        HOOK_PATH in str(hook.get("url") or hook.get("command") or "") for hook in entry.get("hooks") or [] if isinstance(hook, dict))


def merged(current: dict, want: dict) -> tuple[dict, list[dict], list[str]]:
    """(the settings after applying `want`, the changes that makes, what was left alone and why)."""
    out = copy.deepcopy(current) if isinstance(current, dict) else {}
    changes: list[dict] = []
    notes: list[str] = []

    env = out.setdefault("env", {}) if isinstance(out.get("env", {}), dict) else {}
    out["env"] = env
    for key, value in want["env"].items():
        if env.get(key) != value:
            changes.append({"setting": f"env.{key}", "from": env.get(key), "to": value})
            env[key] = value
    for key in FORBIDDEN_ENV:
        if env.get(key):
            notes.append(f"env.{key} is set: prompt or response text is being exported; this profile leaves it as you set it")

    hooks = out.setdefault("hooks", {}) if isinstance(out.get("hooks", {}), dict) else {}
    out["hooks"] = hooks
    for event, entries in want["hooks"].items():
        existing = hooks.get(event) if isinstance(hooks.get(event), list) else []
        kept = [entry for entry in existing if not _is_ours(entry)]
        if [entry for entry in existing if _is_ours(entry)] != entries:
            changes.append({"setting": f"hooks.{event}", "from": [e for e in existing if _is_ours(e)] or None, "to": entries})
        hooks[event] = kept + entries

    status = out.get("statusLine")
    if status is None or (isinstance(status, dict) and STATUSLINE_FILE in str(status.get("command") or "")):
        if status != want["statusLine"]:
            changes.append({"setting": "statusLine", "from": status, "to": want["statusLine"]})
            out["statusLine"] = want["statusLine"]
    else:
        notes.append("statusLine is your own: left as it is, so plan pressure (the 5-hour and 7-day usage) is not collected")

    days = out.get("cleanupPeriodDays")
    if not isinstance(days, (int, float)) or days < want["cleanupPeriodDays"]:
        changes.append({"setting": "cleanupPeriodDays", "from": days, "to": want["cleanupPeriodDays"]})
        out["cleanupPeriodDays"] = want["cleanupPeriodDays"]
    return out, changes, notes


def read_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return data


def plan(path: Path, port: int, statusline_path: Path) -> dict:
    """What applying the profile would change: {"settings_path", "changes", "notes", "in_place"}."""
    _, changes, notes = merged(read_settings(path), desired(port, statusline_path))
    script_current = statusline_path.read_text(encoding="utf-8") if statusline_path.exists() else None
    if script_current != statusline_script(port):
        changes.append({"setting": str(statusline_path), "from": "missing" if script_current is None else "an older version", "to": "the generated statusline script"})
    return {"settings_path": str(path), "changes": changes, "notes": notes, "in_place": not changes}


def apply(path: Path, port: int, statusline_path: Path, *, now: datetime | None = None) -> dict:
    """Write the profile. The settings file is copied beside itself first; the result names the backup."""
    report = plan(path, port, statusline_path)
    if report["in_place"]:
        return {**report, "applied": False, "backup": None}
    after, _, _ = merged(read_settings(path), desired(port, statusline_path))
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.lws-backup-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    statusline_path.parent.mkdir(parents=True, exist_ok=True)
    statusline_path.write_text(statusline_script(port), encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(after, indent=2) + "\n", encoding="utf-8")
    return {**report, "applied": True, "backup": str(backup) if backup else None}


_STATUSLINE = r'''#!/usr/bin/env python3
"""Claude Code status line, generated by little-workshop's Usage & Efficiency pillar: do not edit,
`usage.configure_claude` rewrites it. Prints context, cache and plan usage, and posts the plan usage
to the pillar's intake when it changed."""
import json
import os
import sys
import tempfile
import urllib.request

URL = "http://127.0.0.1:@@PORT@@/v1/statusline"


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    parts = []
    context = (data.get("context_window") or {}).get("used_percentage")
    if context is not None:
        parts.append(f"ctx {context:.0f}%")
    cache = (data.get("prompt_cache") or {}).get("hit_ratio")
    if cache is not None:
        parts.append(f"cache {cache * 100:.0f}%")
    limits = data.get("rate_limits") or {}
    five = (limits.get("five_hour") or {}).get("used_percentage")
    seven = (limits.get("seven_day") or {}).get("used_percentage")
    if five is not None:
        parts.append(f"5h {five:.0f}%")
    if seven is not None:
        parts.append(f"7d {seven:.0f}%")
    model = (data.get("model") or {}).get("display_name")
    print(" · ".join(([model] if model else []) + parts))
    if five is None and seven is None:
        return
    state = os.path.join(tempfile.gettempdir(), f"lws-statusline-{os.getuid()}")
    mark = f"{five}:{seven}"
    try:
        if open(state).read() == mark:
            return
    except OSError:
        pass
    try:
        body = json.dumps({k: data.get(k) for k in ("session_id", "cwd", "workspace", "transcript_path", "rate_limits")}).encode()
        urllib.request.urlopen(urllib.request.Request(URL, body, {"content-type": "application/json"}), timeout=0.5)
        open(state, "w").write(mark)
    except Exception:
        pass


main()
'''


def statusline_script(port: int) -> str:
    return _STATUSLINE.replace("@@PORT@@", str(port))
