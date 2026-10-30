"""Policy `claude-instructions-valid` (registry pillar; gates branch_finish): the project's
Claude Code instructions load as written, and the part every session loads stays small.

- Every `@path` import in an AGENTS.md or a rule file resolves to a file inside the project.
- Every rule in .claude/rules/ has frontmatter that parses, uses no field but `paths`, and has
  `paths` patterns that each match at least one file in the project.
- Every skill's SKILL.md and every subagent in .claude/agents/ has frontmatter with a
  description (and a subagent a name); every script a SKILL.md names and every hook script
  .claude/settings.json runs exists.
- Every instruction file (each AGENTS.md, rule and imported file) stays within 200 lines, and
  what a session loads at launch (the root briefing, the rules without `paths` and everything they
  import) within 250.

Claude Code fails quietly on each of these: a missing import is skipped, one outside the project
loads only after the user approves it (and never in a sandbox or another clone), a rule whose
patterns match nothing never loads, frontmatter that does not parse or a misspelt `paths` turns a
path-scoped rule into one loaded in every session, a skill without a description is never picked,
and a long briefing is followed less reliably. Current repository state only."""

import json
import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
MAX_LINES = 200
MAX_STARTUP_LINES = 250
MAX_IMPORT_DEPTH = 4
ROOT_BRIEFINGS = ("AGENTS.md", ".claude/AGENTS.md")
TEXT_EXTENSIONS = ("md", "txt", "json", "yaml", "yml", "toml", "py", "ts", "tsx", "js", "cs", "sh", "cfg", "ini", "xml")
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`+[^`\n]*`+")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMPORT = re.compile(r"(?<![\w@`/])@((?:\\ |[^\s`'\"()<>\[\]{}])+)")
SKILL_SCRIPT = re.compile(r"(?:\.claude/skills/[\w.-]+/)?scripts/[\w./-]*[\w]")
HOOK_SCRIPT = re.compile(r"\$\{?CLAUDE_PROJECT_DIR\}?\"?/([^\s\"']+)")


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def project_files():
    listed = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if listed.returncode == 0:
        # Tracked files deleted in the checkout are listed too: the checkout is what loads.
        return sorted({f for f in listed.stdout.split("\0") if f and os.path.isfile(os.path.join(PROJECT, f))})
    found = []
    for top, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d != ".git"]
        found += [os.path.relpath(os.path.join(top, f), PROJECT).replace(os.sep, "/") for f in files]
    return found


def read(rel):
    with open(os.path.join(PROJECT, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_frontmatter(text):
    """(frontmatter text or None, body)."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "\n".join(lines[1:]), ""


def parse_frontmatter(text):
    """A dict, or raises ValueError. PyYAML when installed; otherwise the subset rules use."""
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise ValueError(str(exc).splitlines()[0])
        if not isinstance(data, dict):
            raise ValueError("frontmatter is not a mapping")
        return data
    data, key = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item and key and not isinstance(data.get(key), str):
            data.setdefault(key, []).append(item.group(1).strip().strip("\"'"))
            continue
        if line[:1].isspace() and key and isinstance(data.get(key), str):
            data[key] = f"{data[key]} {line.strip()}".strip()  # a folded or wrapped scalar's next line
            continue
        pair = re.match(r"^([\w-]+)\s*:\s*(.*)$", line)
        if not pair:
            raise ValueError(f"cannot read the line {line.strip()!r}")
        key, value = pair.group(1), pair.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[key] = value.strip("\"'")
    return data


def expand_braces(pattern):
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[:match.start()], pattern[match.end():]
    return [p for option in match.group(1).split(",") for p in expand_braces(head + option + tail)]


def glob_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                return None
            body = pattern[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def prose(text):
    """The text Claude Code parses for imports: no fenced blocks, code spans or HTML comments."""
    kept, fenced = [], False
    for line in HTML_COMMENT.sub("", text).splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(CODE_SPAN.sub("", line))
    return "\n".join(kept)


def imports_of(rel, text):
    """(token, project-relative path, or None for a path outside the project) per import."""
    found = []
    base = os.path.dirname(rel)
    for match in IMPORT.finditer(prose(text)):
        token = match.group(1).replace("\\ ", " ").rstrip(".,;:!?")
        if token.startswith(("~", "/")):
            found.append((token, None))  # outside the checkout
            continue
        if "/" not in token and not token.lower().endswith(tuple("." + e for e in TEXT_EXTENSIONS)):
            continue  # a handle or a word after @, not a path
        target = os.path.normpath(os.path.join(base, token)).replace(os.sep, "/")
        found.append((token, None if target.startswith("../") or target == ".." else target))
    return found


def loaded_lines(text):
    return len(HTML_COMMENT.sub("", split_frontmatter(text)[1]).strip("\n").splitlines())


def frontmatter_of(rel, problems, what):
    """The parsed frontmatter of `rel` (a dict), or None after recording why it cannot be read."""
    front, _ = split_frontmatter(read(rel))
    if front is None:
        problems.append(f"{rel}: has no frontmatter, so Claude Code cannot tell when to use this {what}. Fix: start "
                        "the file with `---`, a `description:` line saying when it applies, and `---`.")
        return None
    try:
        return parse_frontmatter(front)
    except ValueError as exc:
        problems.append(f"{rel}: its frontmatter does not parse ({exc}). Fix the YAML.")
        return None


def test_claude_instructions_load_as_written():
    assert os.path.isdir(PROJECT), f"{PROJECT} is not mounted"
    files = project_files()
    briefings = [f for f in files if f == "AGENTS.md" or f.endswith("/AGENTS.md")]
    rules = [f for f in files if f.startswith(".claude/rules/") and f.endswith(".md")]
    skills = [f for f in files if re.fullmatch(r"\.claude/skills/[^/]+/SKILL\.md", f)]
    agents = [f for f in files if re.fullmatch(r"\.claude/agents/[^/]+\.md", f)]
    if not (briefings or rules or skills or os.path.isfile(os.path.join(PROJECT, ".claude", "settings.json"))):
        pytest.skip("the project has no Claude Code instructions")
    present = set(files)
    problems = []

    # Rules: frontmatter that parses, only `paths`, patterns that match a file. A rule without
    # `paths` loads in every session.
    unconditional = []
    for rule in rules:
        front, _ = split_frontmatter(read(rule))
        if front is None:
            unconditional.append(rule)
            continue
        try:
            data = parse_frontmatter(front)
        except ValueError as exc:
            problems.append(f"{rule}: its frontmatter does not parse ({exc}), so Claude Code ignores it and "
                            "loads the rule in every session. Fix the YAML.")
            unconditional.append(rule)
            continue
        others = sorted(k for k in data if k != "paths")
        if others:
            problems.append(f"{rule}: Claude Code reads only `paths` from a rule's frontmatter, so "
                            f"{', '.join(repr(k) for k in others)} is ignored"
                            f"{' and the rule loads in every session' if 'paths' not in data else ''}. Fix: list "
                            "the glob patterns under `paths:` and remove the other fields.")
        paths = data.get("paths")
        if paths is None:
            unconditional.append(rule)
            continue
        if isinstance(paths, str):
            paths = [p.strip() for p in paths.split(",") if p.strip()]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            problems.append(f"{rule}: `paths` must be a list of glob patterns (or one comma-separated string).")
            continue
        for pattern in paths:
            regexes = [glob_regex(p) for p in expand_braces(pattern.lstrip("/"))]
            if None in regexes:
                problems.append(f"{rule}: the pattern {pattern!r} is not a valid glob.")
            elif not any(r.match(f) for r in regexes for f in files):
                problems.append(f"{rule}: the pattern {pattern!r} matches no file in the project, so the rule "
                                "never loads for it. Fix the pattern or delete it.")

    # Imports, followed as Claude Code follows them (up to four hops), and file sizes.
    lines_of = {}
    queue = [(f, 0) for f in briefings + rules]
    while queue:
        rel, depth = queue.pop(0)
        if rel in lines_of:
            continue
        text = read(rel)
        lines_of[rel] = loaded_lines(text)
        if lines_of[rel] > MAX_LINES:
            problems.append(f"{rel}: {lines_of[rel]} lines loaded into context (limit {MAX_LINES}). Fix: keep the "
                            "briefing to what every session needs; move guidance for one part of the tree into a "
                            ".claude/rules/ file with `paths:`, and procedures into a skill.")
        if depth >= MAX_IMPORT_DEPTH:
            continue
        for token, target in imports_of(rel, text):
            if target is None:
                problems.append(f"{rel}: imports @{token}, outside the project, which Claude Code loads only after "
                                "the user approves external imports, and never in a sandbox, a worktree or another "
                                "clone. Fix: keep the file in the project and import it from there, or mention it "
                                "in backticks.")
            elif target in present or os.path.isfile(os.path.join(PROJECT, target)):
                queue.append((target, depth + 1))
            else:
                problems.append(f"{rel}: imports @{token}, which does not exist ({target}), and Claude Code skips "
                                "it silently. Fix the path; to mention a path without importing it, wrap it in "
                                "backticks.")

    # What every session loads at launch: the root briefing, the rules without `paths`, and what they import.
    startup, queue = {}, [(f, 0) for f in ROOT_BRIEFINGS if f in present] + [(f, 0) for f in unconditional]
    while queue:
        rel, depth = queue.pop(0)
        if rel in startup or rel not in lines_of and not os.path.isfile(os.path.join(PROJECT, rel)):
            continue
        startup[rel] = lines_of.get(rel, loaded_lines(read(rel)))
        if depth < MAX_IMPORT_DEPTH:
            queue += [(t, depth + 1) for _, t in imports_of(rel, read(rel)) if t]
    total = sum(startup.values())
    if total > MAX_STARTUP_LINES:
        parts = ", ".join(f"{rel} {n}" for rel, n in sorted(startup.items(), key=lambda kv: -kv[1]))
        problems.append(f"Every session starts with {total} lines of instructions ({parts}; limit "
                        f"{MAX_STARTUP_LINES}). Fix: keep in the briefing only what every session needs; give "
                        "rules a `paths:` list so they load with the files they cover, turn procedures into skills, "
                        "and drop what the code or a hook already says.")

    # Skills and subagents: a description to be picked by, and the scripts a skill names.
    for skill in skills:
        data = frontmatter_of(skill, problems, "skill")
        if data is not None and not str(data.get("description") or "").strip():
            problems.append(f"{skill}: has no `description`, so Claude never picks the skill on its own. Fix: add "
                            "a `description:` that says what it does and when to use it.")
        folder = os.path.dirname(skill)
        for token in sorted(set(SKILL_SCRIPT.findall(read(skill)))):
            target = token if token.startswith(".claude/") else f"{folder}/{token}"
            if not os.path.exists(os.path.join(PROJECT, target)):
                problems.append(f"{skill}: names {token}, which does not exist ({target}). Fix: add the script or "
                                "correct the SKILL.md.")
    for agent in agents:
        data = frontmatter_of(agent, problems, "subagent")
        missing = [k for k in ("name", "description") if data is not None and not str(data.get(k) or "").strip()]
        if missing:
            problems.append(f"{agent}: its frontmatter has no {' or '.join(missing)}, so Claude Code does not load "
                            "the subagent. Fix: add them (`name:` in lowercase with hyphens, `description:` saying "
                            "when to delegate to it).")

    # Hooks: every script .claude/settings.json runs from the project exists.
    settings_path = os.path.join(PROJECT, ".claude", "settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except ValueError as exc:
            settings = {}
            problems.append(f".claude/settings.json is not valid JSON ({exc}).")
        for event, entries in (settings.get("hooks") or {}).items():
            for entry in entries or []:
                for hook in entry.get("hooks") or []:
                    for target in HOOK_SCRIPT.findall(hook.get("command", "")):
                        if not os.path.isfile(os.path.join(PROJECT, target)):
                            problems.append(f".claude/settings.json: the {event} hook runs {target}, which does "
                                            "not exist. Fix the command or add the script.")

    assert not problems, (
        "The project's Claude Code instructions do not load as written:\n- " + "\n- ".join(problems)
    )
