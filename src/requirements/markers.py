"""Requirement markers in test files: the `# --- REQ-nnnnnnnnn: <title> ---` sections that say which
requirement a group of tests covers, and bringing the pillar in line with them
(docs/pillars/requirements_quality.md, "Marker sync").

Creating a requirement and linking it to its suite for each marker was work Claude did by hand, one
MCP call at a time; the markers, the register in `docs/requirements.md` and the pillar's own rows
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


def register(root: Path) -> dict[str, dict]:
    """key -> {"title", "description"} from the project's `docs/requirements.md` rows
    (`| REQ-… | **Title.** text | status |`), where the project keeps such a register."""
    out: dict[str, dict] = {}
    try:
        lines = (root / REGISTER_PATH).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        match = REGISTER_ROW.match(line)
        if not match:
            continue
        text = match.group(2)
        title = REGISTER_TITLE.match(text)
        out[match.group(1)] = {"title": title.group(1).rstrip(".") if title else "", "description": REGISTER_TITLE.sub("", text, count=1).strip()}
    return out
