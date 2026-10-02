#!/usr/bin/env python3
"""PreToolUse hook (Bash): keep a Claude session in little-workshop from running
tests/integration beside the live install (REQ-000000304).

tests/integration's fixtures use the live `little-workshop-<pillar>.service` unit names, so
running them on this machine stops the live pillars (it happened on 2026-09-21). pytest pulls
them in whenever it is given no path from the repository root or tests/ (pyproject's testpaths
is `tests`), and so do `bazel test //:test`, `//:coverage` and `//...` and the scripts behind
them. This denies those commands with the alternative; tests/unit and tests/ui stay allowed.
Input it cannot read passes: it guards one known accident, it is not a sandbox.
Registered in .claude/settings.json through registry/claude_templates.OWN_SETTINGS."""

import json
import os
import re
import shlex
import sys

OPERATORS = set("();<>|&`")
SHELLS = ("bash", "sh", "zsh", "dash")
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# pytest options that take a separate value, so that value is not mistaken for a path.
VALUE_OPTIONS = {
    "-k", "-m", "-o", "-p", "-c", "-r", "-n", "-W", "--rootdir", "--cov", "--cov-report",
    "--cov-fail-under", "--cov-config", "--junitxml", "--junit-xml", "--deselect", "--ignore",
    "--ignore-glob", "--tb", "--basetemp", "--maxfail", "--confcutdir", "--durations", "--log-level",
    "--import-mode", "--capture", "--override-ini",
}
BAZEL_SUITES = {"//:test", "//:coverage", "//...", "//:all", ":test", ":coverage", "...", "//:*"}
SUITE_SCRIPTS = ("run_pytest.sh", "run_pytest_coverage.sh")
REASON = (
    "little-workshop live install: this would run tests/integration, whose fixtures use the live "
    "little-workshop-<pillar>.service unit names and stop the live pillars. Run `python3 -m pytest "
    "tests/unit` (add `--cov=src` for coverage), or name paths under tests/unit or tests/ui."
)


def without_heredoc_bodies(command):
    kept, delimiters = [], []
    for line in command.split("\n"):
        if delimiters:
            if line.strip() == delimiters[0]:
                delimiters.pop(0)
            continue
        kept.append(line)
        delimiters.extend(match.group(2) for match in HEREDOC.finditer(line))
    return "\n".join(kept)


def simple_commands(command):
    command = without_heredoc_bodies(command)
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars="".join(sorted(OPERATORS)))
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    commands, current = [], []
    for word in words:
        if set(word) <= OPERATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(word)
    if current:
        commands.append(current)
    return commands


def pytest_args(words):
    """The arguments after pytest, if this simple command runs pytest; else None."""
    for i, word in enumerate(words):
        base = word.rsplit("/", 1)[-1]
        if base in ("pytest", "py.test"):
            return words[i + 1:]
        if re.fullmatch(r"python[0-9.]*", base) and words[i + 1:i + 3] == ["-m", "pytest"]:
            return words[i + 3:]
    return None


def runs_integration(args, cwd, root):
    """Whether pytest, given these arguments in `cwd`, would collect tests/integration."""
    paths, skip_next = [], False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            skip_next = arg in VALUE_OPTIONS
            continue
        paths.append(arg.split("::", 1)[0])
    integration = os.path.join(root, "tests", "integration")
    broad = {os.path.normpath(root), os.path.join(root, "tests")}
    if not paths:
        return os.path.normpath(cwd) in broad
    for path in paths:
        full = os.path.normpath(os.path.join(cwd, path))
        if full in broad or full == integration or full.startswith(integration + os.sep):
            return True
    return False


def violation(command, cwd, root):
    for words in simple_commands(command):
        if not words:
            continue
        base = words[0].rsplit("/", 1)[-1]
        if base == "cd" and len(words) > 1:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(words[1])))
            continue
        if base in SHELLS:
            for j, option in enumerate(words[1:], 1):
                if option.startswith("-") and not option.startswith("--") and "c" in option[1:] and j + 1 < len(words):
                    if violation(words[j + 1], cwd, root):
                        return True
                    break
            if any(w.rsplit("/", 1)[-1] in SUITE_SCRIPTS for w in words[1:]):
                return True
            continue
        if base in SUITE_SCRIPTS:
            return True
        if base == "bazel" and "test" in words[1:] and BAZEL_SUITES & set(words[words.index("test") + 1:]):
            return True
        args = pytest_args(words)
        if args is not None and runs_integration(args, cwd, root):
            return True
    return False


def main():
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") != "Bash":
            return
        command = str((event.get("tool_input") or {}).get("command", ""))
    except (ValueError, TypeError, AttributeError):
        return
    root = os.path.normpath(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd())
    cwd = os.path.normpath(event.get("cwd") or root)
    if violation(command, cwd, root):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }}))


if __name__ == "__main__":
    main()
