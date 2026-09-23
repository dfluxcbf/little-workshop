"""Parses `docs/pillars/policy_catalog.md` into structured entries —
`docs/milestones.md` M13's own input: "running the ~50 entries ...
through the real four-gate pipeline one at a time." This module only
reads the catalog; it never authors or activates anything itself — that
stays `policy.author`/`policy.activate`, the identical pipeline any
other policy goes through, per the catalog's own "each one earns its
policies row the same way any future policy will."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SECTION_RE = re.compile(r"^## ([A-Z])\. (.+)$")
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")


@dataclass
class CatalogEntry:
    topic: str
    ears_text: str
    source: str
    section: str
    section_title: str


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "pillars" / "policy_catalog.md"


def load_catalog(path: Path | None = None) -> list[CatalogEntry]:
    text = (path or default_catalog_path()).read_text(encoding="utf-8")
    entries: list[CatalogEntry] = []
    section, section_title = "", ""

    for line in text.splitlines():
        section_match = _SECTION_RE.match(line)
        if section_match:
            section, section_title = section_match.group(1), section_match.group(2).replace("&amp;", "&")
            continue

        row_match = _ROW_RE.match(line)
        if not row_match or not section:
            continue
        topic, ears_text, source = row_match.groups()
        if topic.lower() == "topic":  # the header row itself, e.g. "| Topic | EARS requirement | Source |"
            continue
        entries.append(CatalogEntry(
            topic=topic, ears_text=_strip_markdown(ears_text), source=_strip_markdown(source),
            section=section, section_title=section_title,
        ))
    return entries


def _strip_markdown(text: str) -> str:
    text = text.replace("&amp;", "&")
    return re.sub(r"`([^`]+)`", r"\1", text)


def find_entry(topic: str, entries: list[CatalogEntry] | None = None) -> CatalogEntry | None:
    entries = entries if entries is not None else load_catalog()
    return next((e for e in entries if e.topic == topic), None)
