"""The transcript backfill: reading `~/.claude/projects/<dir>/<session>.jsonl` for the sessions
telemetry did not cover (before the user profile, or while the intake was down).

The transcript format is internal to Claude Code and may change with any release, so the parser is
tolerant: a line it cannot read is skipped and counted, never an error. One API request is written
as several assistant lines (one per content block) that repeat the same `requestId` and usage; the
store's request id key makes them one row."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from usage import signatures

DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def transcript_files(root: Path) -> list[Path]:
    try:
        return sorted(path for path in root.glob("*/*.jsonl") if path.is_file())
    except OSError:
        return []


def _lines(path: Path) -> Iterator[dict | None]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    yield None
                    continue
                yield entry if isinstance(entry, dict) else None
    except OSError:
        return


def _result_bytes(content) -> int:
    if isinstance(content, str):
        return len(content.encode("utf-8", errors="replace"))
    if isinstance(content, list):
        return sum(len(str(block.get("text") or "").encode("utf-8", errors="replace")) for block in content if isinstance(block, dict))
    return 0


def parse_transcript(path: Path) -> dict:
    """{"session": {...} | None, "requests": [...], "tool_calls": [...], "skipped": n} for one file."""
    session: dict | None = None
    requests: dict[str, dict] = {}
    tool_calls: dict[str, dict] = {}
    skipped = 0
    prompts = 0  # a user message that is not a tool result starts a new prompt: the episode its tool calls belong to
    for entry in _lines(path):
        if entry is None:
            skipped += 1
            continue
        session_id, ts = entry.get("sessionId"), entry.get("timestamp")
        if not session_id or not isinstance(ts, str):
            continue
        if session is None:
            session = {"id": session_id, "seen": ts, "transcript_path": str(path)}
        session["seen"] = max(session["seen"], ts)
        session["first"] = min(session.get("first", ts), ts)
        for key, field in (("cwd", "cwd"), ("cc_version", "version"), ("entrypoint", "entrypoint")):
            if entry.get(field) and not session.get(key):
                session[key] = entry[field]
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content") if isinstance(message.get("content"), list) else []
        if entry.get("type") == "assistant":
            usage, request_id = message.get("usage"), entry.get("requestId")
            if isinstance(usage, dict) and request_id and message.get("model") != "<synthetic>":
                creation = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else None
                session["model"] = message.get("model") or session.get("model")
                requests[request_id] = {
                    "request_id": request_id, "session_id": session_id, "ts": ts, "model": message.get("model") or "",
                    "input_tokens": usage.get("input_tokens") or 0, "output_tokens": usage.get("output_tokens") or 0,
                    "cache_read_tokens": usage.get("cache_read_input_tokens") or 0,
                    "cache_write_tokens": usage.get("cache_creation_input_tokens") or 0,
                    "cache_write_1h_tokens": creation.get("ephemeral_1h_input_tokens") if creation else None,
                    "query_source": "subagent" if entry.get("isSidechain") else "main", "source": "transcript",
                }
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    tool, tool_input = block.get("name") or "", block.get("input")
                    tool_calls[block["id"]] = {
                        "id": block["id"], "session_id": session_id, "request_id": request_id, "ts": ts, "tool": tool,
                        "prompt_id": f"{session_id}:{prompts}",
                        "signature": signatures.signature(tool, tool_input), "detail": signatures.detail(tool, tool_input),
                        "input_bytes": len(json.dumps(tool_input or {})), "source": "transcript",
                    }
        elif entry.get("type") == "user":
            if isinstance(message.get("content"), str) or any(isinstance(b, dict) and b.get("type") == "text" for b in content):
                prompts += 1
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in tool_calls:
                    call = tool_calls[block["tool_use_id"]]
                    call["result_bytes"] = _result_bytes(block.get("content"))
                    call["success"] = not block.get("is_error")
    return {"session": session, "requests": list(requests.values()), "tool_calls": list(tool_calls.values()), "skipped": skipped}
