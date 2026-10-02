"""Policy `claude-config-present` (registry pillar; gates branch_finish): the project carries
the Claude Code configuration little-workshop scaffolds: .claude/AGENTS.md, and a
.claude/settings.json that registers the .claude/hooks/lws_scope_guard.py PreToolUse hook; and
it has no CLAUDE.md, which would make Claude Code skip .claude/AGENTS.md.

These files are how a Claude session in the project learns to route branch finishes, releases
and deploys through little-workshop, and the guard is what keeps that session inside the
project. Current repository state only."""

import json
import os

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
GUARD = ".claude/hooks/lws_scope_guard.py"
FIX = (
    "Fix: restore the files project.bootstrap scaffolds (little-workshop's "
    "src/registry/claude_templates.py writes them: .claude/AGENTS.md, .claude/settings.json and "
    f"{GUARD}) and keep the project's guidance in .claude/AGENTS.md."
)


def test_claude_code_configuration_present():
    assert os.path.isdir(PROJECT), f"{PROJECT} is not mounted"
    stray = [p for p in ("CLAUDE.md", ".claude/CLAUDE.md") if os.path.isfile(os.path.join(PROJECT, p))]
    assert not stray, (
        f"{', '.join(stray)} exists, and Claude Code then ignores .claude/AGENTS.md. Fix: move its "
        "content into .claude/AGENTS.md (or a path-scoped .claude/rules/ file) and delete it."
    )
    assert os.path.isfile(os.path.join(PROJECT, ".claude", "AGENTS.md")), f".claude/AGENTS.md is missing. {FIX}"
    settings_path = os.path.join(PROJECT, ".claude", "settings.json")
    assert os.path.isfile(settings_path), f".claude/settings.json is missing. {FIX}"
    try:
        with open(settings_path, encoding="utf-8") as fh:
            settings = json.load(fh)
    except ValueError as exc:
        pytest.fail(f".claude/settings.json is not valid JSON ({exc}). {FIX}")
    commands = [
        hook.get("command", "")
        for entry in (settings.get("hooks") or {}).get("PreToolUse") or []
        for hook in entry.get("hooks") or []
    ]
    assert any(GUARD in command for command in commands), (
        f".claude/settings.json does not register {GUARD} as a PreToolUse hook. {FIX}"
    )
    assert os.path.isfile(os.path.join(PROJECT, GUARD)), f"{GUARD} is missing. {FIX}"
