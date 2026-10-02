"""Policy `claude-config-present` (registry pillar; gates branch_finish): the project carries
the Claude Code configuration little-workshop maintains, and nothing that switches it off.

- An AGENTS.md briefing (at the root or as .claude/AGENTS.md) that imports the section every
  project shares (`@little-workshop.md` from .claude/AGENTS.md, `@.claude/little-workshop.md` from a
  root AGENTS.md).
- The generated files: .claude/little-workshop.md, the path-scoped rule
  .claude/rules/claude-instructions.md, the workshop-lifecycle and policy-preflight skills, and the
  guard .claude/hooks/lws_scope_guard.py, which .claude/settings.json registers as a PreToolUse
  hook for Bash, the file-edit tools and the little-workshop MCP tools.
- No CLAUDE.md, .claude/CLAUDE.md or CLAUDE.local.md anywhere in the project: Claude Code reads
  AGENTS.md only where none of them exists.

These files are how a Claude session in the project learns the lifecycle routes, runs the
policies before asking for a gated action, and is refused (with the route) when it reaches for a
raw command, another project or a generated file. Current repository state only."""

import json
import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
GUARD = ".claude/hooks/lws_scope_guard.py"
GENERATED = (
    ".claude/little-workshop.md",
    ".claude/rules/claude-instructions.md",
    ".claude/skills/workshop-lifecycle/SKILL.md",
    ".claude/skills/policy-preflight/SKILL.md",
    ".claude/skills/policy-preflight/scripts/preflight.py",
    GUARD,
)
# The briefing -> the import that loads the shared section from it.
BRIEFINGS = {".claude/AGENTS.md": "@little-workshop.md", "AGENTS.md": "@.claude/little-workshop.md"}
STRAY_NAMES = ("CLAUDE.md", "CLAUDE.local.md")
MCP_TOOL = "mcp__little-workshop__workshop_version_status"
# The tools the guard must see: a shell command, each file-edit tool, a little-workshop MCP tool.
GUARDED_TOOLS = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", MCP_TOOL)
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`+[^`\n]*`+")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FIX = (
    "Fix: in little-workshop, run `python3 tools/sync_claude_files.py --checkout <this checkout> --write`, which "
    "writes the generated files and merges the generated table into .claude/settings.json (keeping the project's "
    "own entries)."
)


def exists(rel):
    return os.path.isfile(os.path.join(PROJECT, rel))


def project_files():
    listed = subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, "ls-files", "--cached", "--others",
                             "--exclude-standard", "-z"], capture_output=True, text=True)
    if listed.returncode != 0:
        return []
    return [f for f in listed.stdout.split("\0") if f and os.path.isfile(os.path.join(PROJECT, f))]


def prose(text):
    """The text Claude Code reads imports from: no fenced blocks, code spans or HTML comments."""
    kept, fenced = [], False
    for line in HTML_COMMENT.sub("", text).splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(CODE_SPAN.sub("", line))
    return "\n".join(kept)


def matches(matcher, tool):
    """Whether a hook matcher (a tool name, `*`, empty, or a regular expression) covers `tool`."""
    if matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def test_claude_code_configuration_present():
    assert os.path.isdir(PROJECT), f"{PROJECT} is not mounted"
    # Ignored files load too (CLAUDE.local.md usually is): check the root ones on disk as well.
    stray = sorted({p for p in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md") if exists(p)}
                   | {f for f in project_files() if f.rsplit("/", 1)[-1] in STRAY_NAMES})
    assert not stray, (
        f"{', '.join(stray)} exists, and Claude Code then reads it instead of AGENTS.md. Fix: move its content into "
        ".claude/AGENTS.md (or a path-scoped .claude/rules/ file, or ~/.claude/ for personal notes) and delete it."
    )

    briefings = [p for p in BRIEFINGS if exists(p)]
    assert briefings, (
        "No AGENTS.md briefing (.claude/AGENTS.md or a root AGENTS.md). Fix: write .claude/AGENTS.md (little-workshop's "
        "claude_templates.agents_md() is the starting point), ending with the line `@little-workshop.md`."
    )
    importing = []
    for rel in briefings:
        with open(os.path.join(PROJECT, rel), encoding="utf-8", errors="replace") as fh:
            if re.search(r"(?<![\w@`/])" + re.escape(BRIEFINGS[rel]) + r"(?![\w./-])", prose(fh.read())):
                importing.append(rel)
    assert importing, (
        f"{' and '.join(briefings)} does not import the section every project shares, so its sessions miss the "
        f"lifecycle routes and what enforces the rules. Fix: add the line `{BRIEFINGS[briefings[0]]}` (outside "
        "backticks) to the briefing."
    )

    missing = [p for p in GENERATED if not exists(p)]
    assert not missing, f"{', '.join(missing)} missing. {FIX}"

    settings_path = os.path.join(PROJECT, ".claude", "settings.json")
    assert os.path.isfile(settings_path), f".claude/settings.json is missing. {FIX}"
    try:
        with open(settings_path, encoding="utf-8") as fh:
            settings = json.load(fh)
    except ValueError as exc:
        pytest.fail(f".claude/settings.json is not valid JSON ({exc}). {FIX}")
    entries = [
        entry for entry in (settings.get("hooks") or {}).get("PreToolUse") or []
        if isinstance(entry, dict) and any(GUARD in (h.get("command") or "") for h in entry.get("hooks") or [])
    ]
    assert entries, f".claude/settings.json does not register {GUARD} as a PreToolUse hook. {FIX}"
    unguarded = [t for t in GUARDED_TOOLS if not any(matches(e.get("matcher"), t) for e in entries)]
    named = ["the little-workshop MCP tools" if t == MCP_TOOL else t for t in unguarded]
    assert not unguarded, f".claude/settings.json does not run {GUARD} for {', '.join(named)}. {FIX}"
