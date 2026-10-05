"""The peek: what a Claude session the workshop started has done so far, read from the transcript
Claude Code writes as it works (`~/.claude/projects/<its directory>/<session id>.jsonl`), shortened to
what a person following it wants to see — what Claude says, and each tool it uses with the gist of
its input, and which of them failed. Read from a byte offset, so a watcher only reads what was added."""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_LIMIT = 200
TEXT_LIMIT = 1500
INPUT_LIMIT = 240
ERROR_LIMIT = 400
# On a first read of a long transcript, only its end is read: the start is past the events shown anyway.
FIRST_READ_BYTES = 4 * 1024 * 1024


def transcripts_dir(directory: Path) -> Path:
    """Where Claude Code keeps the transcripts of sessions started in `directory`."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(directory))


def transcript_of_session(directory: Path, session_id: str) -> Path | None:
    candidate = transcripts_dir(directory) / f"{session_id}.jsonl"
    if candidate.is_file():
        return candidate
    found = sorted((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return found[0] if found else None


def newest_transcript(directory: Path) -> Path | None:
    """The newest session started in `directory` (an update-doc worktree has one)."""
    files = [p for p in transcripts_dir(directory).glob("*.jsonl") if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_gist(name: str, data: dict) -> str:
    """The part of a tool call's input that says what it does."""
    if not isinstance(data, dict):
        return _short(json.dumps(data, default=str), INPUT_LIMIT)
    for key in ("command", "file_path", "notebook_path", "pattern", "url", "query", "description", "prompt", "path"):
        if data.get(key):
            gist = str(data[key])
            if key == "pattern" and data.get("path"):
                gist += f" in {data['path']}"
            return _short(gist, INPUT_LIMIT)
    if name == "TodoWrite" and isinstance(data.get("todos"), list):
        done = sum(1 for t in data["todos"] if isinstance(t, dict) and t.get("status") == "completed")
        return f"{done} of {len(data['todos'])} to-dos done"
    return _short(json.dumps(data, default=str, ensure_ascii=False), INPUT_LIMIT)


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def events_of(entry: dict, tools: dict[str, str]) -> list[dict]:
    """The events of one transcript line. `tools` maps a tool call's id to its name, so a failed result
    can say which tool failed (it is filled in as calls are read)."""
    kind = entry.get("type")
    at = entry.get("timestamp") or ""
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content")
    events: list[dict] = []
    if kind == "assistant" and isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                events.append({"at": at, "kind": "text", "text": _short(block["text"], TEXT_LIMIT)})
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                tools[str(block.get("id") or "")] = name
                events.append({"at": at, "kind": "tool", "tool": name, "text": _tool_gist(name, block.get("input") or {})})
    elif kind == "user":
        if isinstance(content, str) and not entry.get("isMeta"):
            events.append({"at": at, "kind": "prompt", "text": _short(content, INPUT_LIMIT)})
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                    events.append({"at": at, "kind": "error", "tool": tools.get(str(block.get("tool_use_id") or ""), "tool"),
                                   "text": _short(_result_text(block.get("content")), ERROR_LIMIT)})
    elif kind == "system" and entry.get("subtype") == "compact_boundary":
        events.append({"at": at, "kind": "system", "text": "the conversation was compacted"})
    return events


def read_events(path: Path, *, after: int = 0, limit: int = DEFAULT_LIMIT) -> dict:
    """The events written after byte `after` (a negative `after`: none, only where the file ends now),
    at most the last `limit` of them. {"events", "next" (where to read from next time), "skipped"
    (events left out before the first returned; -1 when the start of a long file was not read)}."""
    size = path.stat().st_size
    if after < 0:
        return {"events": [], "next": size, "skipped": 0}
    if after > size:
        after = 0  # the file was replaced: read it again
    start, skipped_unread = after, False
    if after == 0 and size > FIRST_READ_BYTES:
        start, skipped_unread = size - FIRST_READ_BYTES, True
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(size - start)
    if skipped_unread:
        chunk = chunk[chunk.find(b"\n") + 1:]
    end = chunk.rfind(b"\n") + 1  # an unfinished last line is read next time
    tools: dict[str, str] = {}
    events: list[dict] = []
    for line in chunk[:end].splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            events.extend(events_of(entry, tools))
    skipped = max(len(events) - limit, 0)
    return {"events": events[-limit:] if limit else events, "next": size - (len(chunk) - end),
            "skipped": -1 if skipped_unread else skipped}
