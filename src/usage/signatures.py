"""A tool call as a signature: the tool plus its arguments with everything particular to one call
taken out, so that the same kind of call compares equal across sessions (docs/pillars/usage.md,
"Episodes and signatures"). File contents never reach a signature."""

from __future__ import annotations

import json
import re
import shlex

_HEX = re.compile(r"\b[0-9a-f]{7,64}\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_NUMBER = re.compile(r"\b\d+(?:\.\d+)*\b")
_REQ = re.compile(r"\bREQ-\d{9}\b")
_PATH = re.compile(r"(?:~|\.{0,2})?/[\w./@+-]+|\b[\w.-]+/[\w./@+-]+")
# How many leading words of a shell command name what it does (`python3 -m pytest`, `git status`).
_KEEP_WORDS = 4
MCP_PREFIX = "mcp__"
_INTERPRETERS = ("python3", "python", "bash", "sh", "node")
_SCRIPT_SUFFIXES = (".py", ".sh", ".js", ".mjs")


def _scrub(text: str) -> str:
    text = _UUID.sub("<id>", text)
    text = _REQ.sub("<req>", text)
    text = _PATH.sub("<path>", text)
    text = _HEX.sub("<hash>", text)
    return _NUMBER.sub("<n>", text)


def bash_signature(command: str) -> str:
    """`python3 -m pytest tests/unit -q` -> `Bash(python3 -m pytest <path>)`: the command's first
    simple command, its leading words kept, flags after them dropped, paths and ids as placeholders."""
    first = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command.strip(), maxsplit=1)[0]
    try:
        words = shlex.split(first)
    except ValueError:
        words = first.split()
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):  # leading VAR=value assignments
        words = words[1:]
    if words[:1] == ["cd"] and "&&" in command:  # `cd X && real command`: the real command names the work
        return bash_signature(command.split("&&", 1)[1])
    kept = [_scrub(word) for word in words[:_KEEP_WORDS]]
    # The script an interpreter runs names the work (`python3 tools/sync_policies.py`): it stays as written.
    if len(words) > 1 and words[0] in _INTERPRETERS and not words[1].startswith(("-", "/", "~", "..")) and words[1].endswith(_SCRIPT_SUFFIXES):
        kept[1] = words[1]
    rest = sorted({_scrub(word) for word in words[_KEEP_WORDS:] if not word.startswith("-")} & {"<path>", "<id>", "<req>"})
    return f"Bash({' '.join(kept + rest)})"


def signature(tool: str, tool_input: dict | str | None) -> str:
    """The signature of one tool call. `tool_input` is the tool's input object (or its JSON text)."""
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            tool_input = {}
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool == "Bash":
        return bash_signature(str(tool_input.get("command") or tool_input.get("full_command") or ""))
    if tool.startswith(MCP_PREFIX):
        # An MCP call is named by its tool and which arguments it was given; their values vary.
        name = tool[len(MCP_PREFIX):].split("__", 1)[-1]
        return f"mcp:{name}({', '.join(sorted(tool_input))})"
    if tool in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        return f"{tool}(<path>)"
    if tool in ("Agent", "Task"):
        return f"{tool}({tool_input.get('subagent_type') or 'default'})"
    if tool == "Skill":
        return f"Skill({tool_input.get('skill') or ''})"
    return f"{tool}()"


def detail(tool: str, tool_input: dict | str | None) -> str:
    """What the call acted on, kept beside the signature: the command, the file path, the skill."""
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return ""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "full_command", "file_path", "skill", "subagent_type", "pattern", "url", "job_id", "pillar"):
        if tool_input.get(key):
            return str(tool_input[key])[:500]
    return ""
