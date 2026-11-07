"""Requirement markers in test files: the `# --- REQ-nnnnnnnnn: <title> ---` sections that say which
requirement a group of tests covers, and bringing the pillar in line with them
(docs/pillars/requirements_quality.md, "Marker sync").

Creating a requirement and linking it to its suite for each marker was work Claude did by hand, one
MCP call at a time; the markers, the register (`docs/requirements.md`, plus `docs/requirements/*.md` where a
project splits it) and the pillar's own rows
decide every step of it, so it is code."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

MARKER = re.compile(r"^\s*(?:#|//)\s*-{2,}\s*(REQ-\d{9}):\s*(.*?)\s*-{2,}\s*$")
REGISTER_ROW = re.compile(r"^\|\s*(REQ-\d{9})\s*\|\s*(.+?)\s*\|\s*[^|]*\|\s*$")
REGISTER_TITLE = re.compile(r"^\*\*(.+?)\*\*\s*")
TEST_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".cc", ".cpp", ".go", ".rs")
REGISTER_PATH = "docs/requirements.md"
REGISTER_DIR = "docs/requirements"


def register_files(root: Path) -> list[Path]:
    """The register's files: `docs/requirements.md`, then every `docs/requirements/*.md` part, by name."""
    return [root / REGISTER_PATH, *sorted((root / REGISTER_DIR).glob("*.md"))]


def _files(root: Path) -> list[Path]:
    """What git tracks or would track under the checkout (never build output), else every file."""
    listed = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            capture_output=True, text=True)
    if listed.returncode == 0:
        return [root / name for name in listed.stdout.split("\0") if name]
    return [path for path in root.rglob("*") if path.is_file()]


def scan(root: Path) -> dict[str, dict]:
    """key -> {"title", "files": [paths relative to the checkout]} for every marker in a test file."""
    found: dict[str, dict] = {}
    for path in sorted(_files(root)):
        relative = path.relative_to(root)
        if path.suffix not in TEST_SUFFIXES or not any(part.lower() in ("tests", "test", "__tests__", "spec") for part in relative.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = MARKER.match(line)
            if match:
                entry = found.setdefault(match.group(1), {"title": match.group(2), "files": []})
                if str(relative) not in entry["files"]:
                    entry["files"].append(str(relative))
    return found


_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(")
_SECTION = re.compile(r"^\s*(?:#|//)\s*-{2,}\s+\w")  # any `# --- title ---` header (REQ-NONE's too) ends a section


def test_sections(root: Path) -> dict[tuple[str, str], str]:
    """(dotted module, test function) -> the key of the marker section holding it, for every Python test file with
    markers (REQ-000000500): a test belongs to the nearest `# --- REQ-…: … ---` line above it."""
    found: dict[tuple[str, str], str] = {}
    files = sorted({f for entry in scan(root).values() for f in entry["files"] if f.endswith(".py")})
    for rel in files:
        module = rel.removesuffix(".py").replace("/", ".").replace("\\", ".")
        try:
            lines = (root / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        key = None
        for line in lines:
            marker = MARKER.match(line)
            if marker or _SECTION.match(line):
                key = marker.group(1) if marker else None
                continue
            test = _TEST_DEF.match(line)
            if test and key:
                found[(module, test.group(1))] = key
    return found


def requirement_for(test_name: str, sections: dict[tuple[str, str], str]) -> str | None:
    """The key a recorded result (`classname::name`, JUnit's form) belongs to: its module, or the module of its
    class, and its function name without pytest's `[parameters]`."""
    if "::" not in test_name:
        return None
    classname, name = test_name.split("::", 1)
    name = name.split("[", 1)[0]
    return sections.get((classname, name)) or sections.get((classname.rsplit(".", 1)[0], name))


def supersede_in_register(root: Path, old_key: str, new_key: str, title: str, description: str,
                          status: str = "draft") -> bool:
    """Mark `old_key`'s row of the project's register superseded by `new_key` (appended to its last cell,
    which is a status or a milestone depending on the project) and add `new_key`'s row right after it, in
    the same file. False, and nothing written, where there is no such row (REQ-000000448)."""
    for path in register_files(root):
        try:
            lines = path.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        for index, line in enumerate(lines):
            match = REGISTER_ROW.match(line)
            if not match or match.group(1) != old_key:
                continue
            head, last = line.rstrip().rstrip("|").rsplit("|", 1)
            lines[index] = f"{head}| {last.strip()}, superseded by {new_key} |"
            text = (f"**{title.rstrip('.')}.** {description}".strip() if title else description).replace("|", "\\|")
            lines.insert(index + 1, f"| {new_key} | {text} | {status} |")
            path.write_text("\n".join(lines), encoding="utf-8")
            return True
    return False


def register(root: Path) -> dict[str, dict]:
    """key -> {"title", "description"} from the rows of the project's register files
    (`| REQ-… | **Title.** text | status |`), where the project keeps such a register."""
    out: dict[str, dict] = {}
    lines: list[str] = []
    for path in register_files(root):
        try:
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    for line in lines:
        match = REGISTER_ROW.match(line)
        if not match:
            continue
        text = match.group(2)
        title = REGISTER_TITLE.match(text)
        out[match.group(1)] = {"title": title.group(1).rstrip(".") if title else "", "description": REGISTER_TITLE.sub("", text, count=1).strip()}
    return out
