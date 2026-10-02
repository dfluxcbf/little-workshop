"""Policy `requirement-marker-id-format` (requirements pillar; gates branch_finish): where a
requirement marker is present in the project's tests, it references an identifier of the form
REQ- followed by nine digits."""

import os
import re
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(os.environ.get("POLICY_PROJECT_DIR", "/project"))
FIX = (
    "Fix: mark each test with the id of a requirement it covers, e.g. "
    "@pytest.mark.requirement(\"REQ-000000123\"); `lws requirements list --project-id <project>` lists them."
)
EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".rs", ".cs", ".kt", ".php"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "site-packages",
             "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "target"}

DECORATOR = re.compile(
    r"@\s*(?:pytest\.mark\.)?(?:requirements?|req)\b[ \t]*(?:\(([^)]*)\))?"
)
COMMENT = re.compile(
    r"(?:#|//)[ \t]*(?:requirements?|req)[ \t]*[:=][ \t]*(.*)$", re.IGNORECASE
)
MARKER_CALL = re.compile(
    r"\b(?:pytest\.mark\.|mark\.)(?:requirements?|req)\b[ \t]*\(([^)]*)\)"
)
REQ_TOKEN = re.compile(r"REQ-[A-Za-z0-9_\-]*", re.IGNORECASE)
VALID = re.compile(r"REQ-\d{9}")
# A marker argument that's a single bare name (no quotes, no punctuation) --
# e.g. `@pytest.mark.requirement(REQ)` -- rather than a literal string. Real,
# idiomatic code when several tests in one file share one requirement and the
# author defines it once (`REQ = "REQ-000000055"`) instead of repeating the
# literal on every decorator; a purely textual scanner can't see through the
# reference without resolving it (2026-09-22's little-meals false-positive
# finding -- `REQ`/`GLOB_REQ` module constants).
BARE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONST_ASSIGN = re.compile(
    r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*['\"](REQ-\d{9})['\"][ \t]*$"
)


def _is_test_file(path: pathlib.Path) -> bool:
    if path.suffix not in EXTS:
        return False
    rel_parts = path.relative_to(ROOT).parts
    if any(p in SKIP_DIRS for p in rel_parts):
        return False
    name = path.name.lower()
    in_test_dir = any(p.lower() in {"tests", "test", "__tests__", "spec", "specs"} for p in rel_parts[:-1])
    return (
        in_test_dir
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.js", ".test.ts", ".test.tsx",
                          ".test.jsx", ".spec.js", ".spec.ts", "test.java", "tests.java"))
    )


def _project_files(root):
    """The project's own files: what git tracks or would track (never ignored build output,
    caches or vendored packages such as Unity's Library/), else every file under the checkout."""
    listed = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(root), "ls-files", "--cached", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, text=True,
    )
    if listed.returncode == 0:
        return [root / p for p in sorted(set(listed.stdout.split("\0"))) if p]
    return sorted(root.rglob("*"))


def _constants(text: str) -> dict[str, str]:
    """{NAME: "REQ-nnnnnnnnn"} for every simple `NAME = "REQ-nnnnnnnnn"` line
    in the file (any indentation level -- module or class scope alike)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = CONST_ASSIGN.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _collect():
    markers = []  # (relpath, lineno, text, constants)
    if not ROOT.is_dir():
        return markers
    for path in _project_files(ROOT):
        try:
            if not path.is_file() or not _is_test_file(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(ROOT))
        constants = _constants(text)
        for lineno, line in enumerate(text.splitlines(), 1):
            seen = False
            for m in DECORATOR.finditer(line):
                markers.append((rel, lineno, m.group(1) or "", constants))
                seen = True
            if not seen:
                for m in MARKER_CALL.finditer(line):
                    markers.append((rel, lineno, m.group(1) or "", constants))
                    seen = True
            if not seen:
                m = COMMENT.search(line)
                if m:
                    markers.append((rel, lineno, m.group(1), constants))
    return markers


def test_requirement_markers_reference_req_nine_digit_ids():
    markers = _collect()
    if not markers:
        pytest.skip("No requirement markers present in the project's tests")

    violations = []
    for rel, lineno, body, constants in markers:
        stripped = body.strip()
        if BARE_NAME.match(stripped) and stripped in constants:
            # A bare name that resolves to a well-formed constant elsewhere in
            # this same file is valid by construction (VALID.fullmatch already
            # guaranteed at capture time by CONST_ASSIGN's own pattern).
            continue
        tokens = REQ_TOKEN.findall(body)
        if not tokens:
            violations.append(f"{rel}:{lineno}: marker has no REQ- identifier ({body!r})")
            continue
        for tok in tokens:
            if not re.fullmatch(r"REQ-\d{9}", tok):
                violations.append(
                    f"{rel}:{lineno}: identifier {tok!r} is not REQ- followed by exactly nine digits"
                )

    assert not violations, f"Malformed requirement markers. {FIX}\n" + "\n".join(violations)
