"""The user's own display preferences (the order of project cards on each page).

Kept in one small JSON file under the workshop's state directory. It is the
only thing the Dashboard stores: everything else on a page is read live from
the pillar that owns it, and a missing or unreadable file just means the
default order."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from common.paths import Paths

ORDERED_PAGES = ("dashboard", "control")


def _file(paths: Paths) -> Path:
    return paths.state_home / "dashboard" / "prefs.json"


def _load(paths: Paths) -> dict:
    try:
        data = json.loads(_file(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_order(names: list[str], saved: list[str]) -> list[str]:
    """`names` in the saved order; projects the user has never placed keep their given order after the placed ones."""
    placed = [n for n in saved if n in names]
    return placed + [n for n in names if n not in placed]


def ordered(paths: Paths, page: str, names: list[str]) -> list[str]:
    saved = _load(paths).get("order", {}).get(page, [])
    return apply_order(names, saved if isinstance(saved, list) else [])


def move(paths: Paths, page: str, names: list[str], name: str, direction: str) -> list[str]:
    """Swap `name` with its neighbour and remember the whole order; returns the new order."""
    if page not in ORDERED_PAGES or direction not in ("up", "down"):
        raise ValueError(f"cannot move {name!r} {direction!r} on {page!r}")
    current = ordered(paths, page, names)
    if name not in current:
        raise ValueError(f"{name!r} is not a project on this page")
    i = current.index(name)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(current):
        current[i], current[j] = current[j], current[i]
    data = _load(paths)
    data.setdefault("order", {})[page] = current
    target = _file(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, target)
    return current
