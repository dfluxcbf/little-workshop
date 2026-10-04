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


_SEPARATOR = re.compile(r"\s*(?:&&|\|\||;|\n|\|)\s*")
# Commands that only prepare the shell: the work is in what follows them.
_SETUP_WORDS = ("cd", "export", "set", "source", ".", "pushd", "unset")
INLINE = "<inline>"
UNKNOWN_BASH = "Bash(?)"


def _words(simple_command: str) -> list[str]:
    try:
        words = shlex.split(simple_command)
    except ValueError:
        words = simple_command.split()
    while words and re.fullmatch(r"[A-Za-z_]\w*=.*", words[0]):  # leading VAR=value assignments
        words = words[1:]
    return words


def bash_signature(command: str) -> str:
    """`cd repo && python3 -m pytest tests/unit -q` -> `Bash(python3 -m pytest <path>)`: the first
    simple command that does work (not a `cd` or an `export` before it), its leading words kept,
    flags after them dropped, paths and ids as placeholders. A script written on the command line
    (a here-document, `python3 -c`, `bash -c`) is `Bash(<interpreter> <inline>)`: its text is prose
    Claude wrote, not an argument. A command that could not be read is `Bash(?)`."""
    command = (command or "").strip()
    if not command:
        return UNKNOWN_BASH
    heredoc = "<<" in command
    segments = [segment for segment in _SEPARATOR.split(command.split("<<", 1)[0] if heredoc else command) if segment.strip()]
    words: list[str] = []
    for segment in segments:
        words = _words(segment)
        if words and words[0] not in _SETUP_WORDS:
            break
    if not words:
        return UNKNOWN_BASH
    if words[0] in _INTERPRETERS and (heredoc or any(word in ("-c", "-lc", "-e", "-") for word in words[1:3])):
        return f"Bash({words[0]} {INLINE})"
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
