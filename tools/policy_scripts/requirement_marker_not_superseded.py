"""Policy `requirement-marker-not-superseded` (requirements pillar; gates branch_finish):
where the project keeps a `docs/requirements.md` register (with its `docs/requirements/*.md` parts, where it is
split), a commit leaves no test citing a requirement
whose row is marked superseded or removed (REQ-000000449).

A superseded row whose tests still carry its id keeps asserting the old behaviour, and either fails or
quietly pulls new work back toward it. `workshop_requirements_supersede` marks the row and lists the
tests to re-tag; this check refuses the commit that leaves one behind. Judges the commit being made
when one is pending (POLICY_COMMIT_DIR), otherwise HEAD against its first parent, and
only what it introduces: every test citing a row the commit newly retires, and the test files the
commit changes that cite any retired row. An old leftover never fails every later commit."""

import os
import re
import pathlib
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
ROOT = pathlib.Path(PROJECT)
REGISTER_PATH = "docs/requirements.md"
REGISTER_DIR = "docs/requirements"
FIX = (
    "Fix: re-tag these tests with the requirement that replaced it (the row's last cell names it), or delete "
    "them if the behaviour is gone; `workshop_requirements_supersede` lists the files still tagged."
)
EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".rs", ".cs", ".kt", ".php", ".cc", ".cpp"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "site-packages",
             "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "target"}
REGISTER_ROW = re.compile(r"^\|\s*(REQ-\d{9})\s*\|.*\|\s*([^|]*?)\s*\|\s*$")
RETIRED = re.compile(r"\b(superseded|removed)\b", re.IGNORECASE)
# The citations a test makes: a `# --- REQ-nnnnnnnnn: title ---` section, a requirement decorator or
# marker call, or a `# requirement: REQ-…` comment (the shapes requirement-marker-id-format reads).
SECTION = re.compile(r"^\s*(?:#|//)\s*-{2,}\s*(REQ-\d{9}):")
CITATION = re.compile(r"(?:@\s*(?:pytest\.mark\.)?|\bmark\.)(?:requirements?|req)\b[ \t]*\(([^)]*)\)"
                      r"|(?:#|//)[ \t]*(?:requirements?|req)[ \t]*[:=][ \t]*(.*)$", re.IGNORECASE)
REQ_ID = re.compile(r"REQ-\d{9}")


def _is_test_file(path: pathlib.Path) -> bool:
    if path.suffix not in EXTS:
        return False
    rel_parts = path.relative_to(ROOT).parts
    if any(p in SKIP_DIRS for p in rel_parts):
        return False
    name = path.name.lower()
    return (any(p.lower() in {"tests", "test", "__tests__", "spec", "specs"} for p in rel_parts[:-1])
            or name.startswith("test_") or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts")))


def _project_files(root):
    """The project's own files: what git tracks or would track, else every file under the checkout."""
    listed = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "--cached", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, text=True,
    )
    if listed.returncode == 0:
        return [root / p for p in sorted(set(listed.stdout.split("\0"))) if p]
    return sorted(root.rglob("*"))


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def _commit():
    """(paths the commit under judgment adds or changes, its object prefix, its parent's prefix)."""
    if COMMIT_DIR:
        with open(os.path.join(COMMIT_DIR, "files"), encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip()], ":", "HEAD:"
    if git("rev-parse", "--verify", "-q", "HEAD^").returncode != 0:
        pytest.skip("HEAD has no parent commit to compare against")
    diff = git("diff", "--name-only", "--no-renames", "--diff-filter=AMRCT", "-z", "HEAD^", "HEAD")
    assert diff.returncode == 0, f"cannot read HEAD's changes: {diff.stderr.strip()}"
    return [f for f in diff.stdout.split("\0") if f], "HEAD:", "HEAD^:"


def _text(prefix, path):
    """`path` at `prefix` (":" the index, falling back to the checkout), or None where it is absent."""
    shown = git("show", prefix + path)
    if shown.returncode == 0:
        return shown.stdout
    full = ROOT / path
    if prefix == ":" and full.is_file():
        return full.read_text(encoding="utf-8", errors="replace")
    return None


def _register_text(prefix):
    """The register's text at `prefix` (":" the index, else a revision): `docs/requirements.md` and every
    `docs/requirements/*.md` part, joined."""
    if prefix == ":":
        listed = git("ls-files", "-z", "--", REGISTER_PATH, REGISTER_DIR)
    else:
        listed = git("ls-tree", "-r", "-z", "--name-only", prefix.rstrip(":"), "--", REGISTER_PATH, REGISTER_DIR)
    names = [n for n in listed.stdout.split("\0") if n] if listed.returncode == 0 else []
    if prefix == ":" and not names:  # not a git checkout's index: the files as they are
        names = [str(p.relative_to(ROOT)) for p in [ROOT / REGISTER_PATH, *sorted((ROOT / REGISTER_DIR).glob("*.md"))]
                 if p.is_file()]
    parts = [n for n in names if n == REGISTER_PATH or (n.startswith(REGISTER_DIR + "/") and n.endswith(".md"))]
    return "\n".join(_text(prefix, n) or "" for n in parts)


def _retired(text) -> dict[str, str]:
    """REQ key -> its register row's last cell, for every row marked superseded or removed."""
    out = {}
    for line in (text or "").splitlines():
        match = REGISTER_ROW.match(line)
        if match and RETIRED.search(match.group(2)):
            out[match.group(1)] = match.group(2)
    return out


def _cited(text):
    """(line number, REQ key) for every requirement a test file's text cites."""
    for lineno, line in enumerate(text.splitlines(), 1):
        section = SECTION.match(line)
        keys = {section.group(1)} if section else set()
        for match in CITATION.finditer(line):
            keys.update(REQ_ID.findall(match.group(1) or match.group(2) or ""))
        for key in sorted(keys):
            yield lineno, key


def test_no_test_cites_a_superseded_or_removed_requirement():
    if not (ROOT / REGISTER_PATH).is_file() and not (ROOT / REGISTER_DIR).is_dir():
        pytest.skip("The project keeps no docs/requirements.md register")
    changed, now, before = _commit()
    retired = _retired(_register_text(now))
    if not retired:
        pytest.skip("No requirement in the register is superseded or removed")
    newly = {key for key in retired if key not in _retired(_register_text(before))}
    violations = []
    # Nothing newly retired: only the commit's own files can break the rule, so the walk stays off
    # a large checkout's every test on every commit.
    candidates = _project_files(ROOT) if newly else [ROOT / rel for rel in changed]
    for path in candidates:
        if not path.is_file() or not _is_test_file(path):
            continue
        rel = str(path.relative_to(ROOT))
        judged = retired if rel in changed else newly
        if not judged:
            continue
        text = _text(now, rel) if rel in changed else path.read_text(encoding="utf-8", errors="replace")
        for lineno, key in _cited(text or ""):
            if key in judged:
                violations.append(f"{rel}:{lineno}: cites {key}, which the register marks {retired[key]!r}")
    assert not violations, f"Tests cite retired requirements. {FIX}\n" + "\n".join(violations)
