"""The desired Claude Code user profile: what `~/.claude/settings.json` must hold for every Claude
session on this machine to report to the intake (docs/pillars/usage.md, "Configuration"), to be named
after its checkout (the session-title hook, REQ-000000413) and to show little-workshop's status (the
status mod in `~/.claude/skills/`, REQ-000000414).

These are the only files this pillar edits outside every repository, so nothing here writes without being
asked: `plan` says what would change, `apply` changes it after keeping a backup of the settings, and both
leave every entry the owner made alone."""

from __future__ import annotations

import copy
import importlib.resources
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HOOK_PATH = "/v1/hooks/"
HTTP_HOOK_EVENTS = ("SessionEnd", "PreCompact", "PostCompact")
COMMAND_HOOK_EVENTS = ("SessionStart",)  # Claude Code 2.1.289 posts no `http` hook at session start
CLEANUP_PERIOD_DAYS = 90
STATUSLINE_FILE = "statusline.py"
SESSION_TITLE_FILE = "session_title.py"  # this package's own module, copied beside the statusline script
TITLE_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit")  # the events whose answer may carry `sessionTitle`
TITLE_HOOK_TIMEOUT = 10
MOD_NAME = "little-workshop-status"
# The status mod's files: where each lands in its plugin folder, from its source in `claude_mod/` beside this module.
MOD_FILES = {
    ".claude-plugin/plugin.json": "plugin.json",
    "hooks/hooks.json": "hooks.json",
    "hooks/register.tsx": "register.tsx",
    "types/index.d.ts": "state.d.ts",
}


@dataclass(frozen=True)
class StatusMod:
    """Where the status mod reads the Dashboard (its Unix socket) and where its links open it."""

    dashboard_socket: Path
    dashboard_url: str
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


def session_title_path(statusline_path: Path) -> Path:
    return statusline_path.parent / SESSION_TITLE_FILE


def session_title_script() -> str:
    return (importlib.resources.files("usage") / SESSION_TITLE_FILE).read_text(encoding="utf-8")


def mod_dir(settings_path: Path) -> Path:
    """The status mod's folder: a plugin in the user's skills folder, which every session loads by itself."""
    return settings_path.parent / "skills" / MOD_NAME


def mod_files(mod: StatusMod) -> dict[str, str]:
    """{path in the mod's folder: its text}, with the Dashboard's socket and URL written in."""
    source = importlib.resources.files("usage") / "claude_mod"
    values = {"'@@DASHBOARD_SOCKET@@'": json.dumps(str(mod.dashboard_socket)), "'@@DASHBOARD_URL@@'": json.dumps(mod.dashboard_url.rstrip("/"))}
    files = {}
    for target, name in MOD_FILES.items():
        text = (source / name).read_text(encoding="utf-8")
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        files[target] = text
    return files


def desired(port: int, statusline_path: Path) -> dict:
    hooks = {event: [_hook(event, port)] for event in (*COMMAND_HOOK_EVENTS, *HTTP_HOOK_EVENTS)}
    title = {"hooks": [{"type": "command", "command": f"python3 {session_title_path(statusline_path)}", "timeout": TITLE_HOOK_TIMEOUT}]}
    for event in TITLE_HOOK_EVENTS:
        hooks.setdefault(event, []).append(title)
    return {
        "env": telemetry_env(port),
        "hooks": hooks,
        "statusLine": {"type": "command", "command": f"python3 {statusline_path}"},
        "cleanupPeriodDays": CLEANUP_PERIOD_DAYS,
    }


def _is_ours(entry) -> bool:
    return isinstance(entry, dict) and any(
        HOOK_PATH in str(hook.get("url") or hook.get("command") or "") or SESSION_TITLE_FILE in str(hook.get("command") or "")
        for hook in entry.get("hooks") or [] if isinstance(hook, dict))


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


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _files(path: Path, port: int, statusline_path: Path, status_mod: StatusMod | None) -> list[tuple[Path, str, str]]:
    """The files the profile writes beside the settings: (where, its text, what to call it in a change)."""
    files = [(statusline_path, statusline_script(port), "the generated statusline script"),
             (session_title_path(statusline_path), session_title_script(), "the session-title hook")]
    if status_mod is not None:
        folder = mod_dir(path)
        files += [(folder / target, text, f"the {MOD_NAME} mod's {target}") for target, text in mod_files(status_mod).items()]
    return files


def plan(path: Path, port: int, statusline_path: Path, *, status_mod: StatusMod | None = None) -> dict:
    """What applying the profile would change: {"settings_path", "changes", "notes", "in_place"}. The status mod
    is part of it when `status_mod` says where the Dashboard is."""
    _, changes, notes = merged(read_settings(path), desired(port, statusline_path))
    for target, text, name in _files(path, port, statusline_path, status_mod):
        current = _read(target)
        if current != text:
            changes.append({"setting": str(target), "from": "missing" if current is None else "an older version", "to": name})
    return {"settings_path": str(path), "changes": changes, "notes": notes, "in_place": not changes}


def apply(path: Path, port: int, statusline_path: Path, *, status_mod: StatusMod | None = None, now: datetime | None = None) -> dict:
    """Write the profile. The settings file is copied beside itself first; the result names the backup."""
    report = plan(path, port, statusline_path, status_mod=status_mod)
    if report["in_place"]:
        return {**report, "applied": False, "backup": None}
    after, _, _ = merged(read_settings(path), desired(port, statusline_path))
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.lws-backup-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    for target, text, _ in _files(path, port, statusline_path, status_mod):
        if _read(target) != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
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
