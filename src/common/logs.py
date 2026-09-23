"""Size-based rolling log retention — docs/architecture.md's Data model,
"Log retention, resolved 2026-09-15": size-based, not age-based (this is
one personal machine where disk is the actual constraint), oldest lines
trimmed first, a rolling window rather than periodic full-file rotation.

Built once, here, in M1 (Core is the first thing that writes a log) and
reused by every later pillar rather than reimplemented per pillar —
docs/milestones.md M1's "Shared infrastructure that has to start
somewhere."
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB per log file — implementation default.


def append_line(path: Path, line: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    trim_to_size(path, max_bytes=max_bytes)


def trim_to_size(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    """If `path` has grown past `max_bytes`, drop the oldest lines until it
    fits — a rolling window, never a full-file rotation to a `.1` sibling."""
    if not path.exists():
        return
    size = path.stat().st_size
    if size <= max_bytes:
        return

    with path.open("rb") as fh:
        data = fh.read()

    # Keep the tail: drop whole lines from the front until we're at or
    # under budget, so we never leave a truncated partial line at the top.
    trimmed = data[-max_bytes:]
    first_newline = trimmed.find(b"\n")
    if first_newline != -1:
        trimmed = trimmed[first_newline + 1 :]

    with path.open("wb") as fh:
        fh.write(trimmed)
