"""Turning what Claude Code sends into rows: OpenTelemetry logs (OTLP http/json), hook posts and
statusline posts (docs/pillars/usage.md, "Sources"). Pure parsing: nothing here touches the store.

The OTLP shapes were taken from a real export (Claude Code 2.1.289, 2026-10-04): every attribute
value arrives as a string, and the resource attributes little-workshop sets on a run it starts
(`lws.project`, `lws.job`, `lws.job_type`, `lws.run`) are repeated on each log record."""

from __future__ import annotations

from datetime import datetime, timezone

from usage import signatures

EVENT_REQUEST, EVENT_TOOL, EVENT_MCP = "api_request", "tool_result", "mcp_server_connection"


def _value(wrapped: dict | None):
    """An OTLP AnyValue ({"stringValue": "x"}, {"intValue": "3"}, ...) as a Python value."""
    if not isinstance(wrapped, dict) or not wrapped:
        return None
    kind, value = next(iter(wrapped.items()))
    if kind == "intValue":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value if kind in ("stringValue", "doubleValue", "boolValue") else None


def _attributes(items) -> dict:
    return {item["key"]: _value(item.get("value")) for item in items or [] if isinstance(item, dict) and "key" in item}


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time(attrs: dict, record: dict) -> str:
    stamp = attrs.get("event.timestamp")
    if isinstance(stamp, str) and stamp:
        return stamp.replace("Z", "+00:00")
    nanos = _int(record.get("timeUnixNano"))
    moment = datetime.fromtimestamp(nanos / 1e9, timezone.utc) if nanos else datetime.now(timezone.utc)
    return moment.isoformat()


def parse_logs(payload: dict) -> dict:
    """{"sessions": [...], "requests": [...], "tool_calls": [...], "events": [...]} from one `/v1/logs` body.
    Events this pillar does not use, and records it cannot read, are skipped."""
    sessions: dict[str, dict] = {}
    requests, tool_calls, events = [], [], []
    for resource_logs in (payload or {}).get("resourceLogs") or []:
        resource = _attributes((resource_logs.get("resource") or {}).get("attributes"))
        for scope_logs in resource_logs.get("scopeLogs") or []:
            for record in scope_logs.get("logRecords") or []:
                attrs = {**resource, **_attributes(record.get("attributes"))}
                session_id = attrs.get("session.id")
                if not session_id:
                    continue
                ts = _time(attrs, record)
                session = sessions.setdefault(session_id, {"id": session_id, "seen": ts})
                session["seen"] = max(session["seen"], ts)
                session.update({key: value for key, value in {
                    "tag_project": attrs.get("lws.project"), "job_id": attrs.get("lws.job"), "job_type": attrs.get("lws.job_type"),
                    "run_id": attrs.get("lws.run"), "cc_version": attrs.get("service.version"),
                    "repository": attrs.get("vcs.repository.url.full") or attrs.get("vcs.repository.name"),
                }.items() if value})
                name = attrs.get("event.name")
                if name == EVENT_REQUEST and attrs.get("request_id"):
                    session["model"] = attrs.get("model") or session.get("model")
                    requests.append({
                        "request_id": attrs["request_id"], "session_id": session_id, "ts": ts, "model": attrs.get("model") or "",
                        "input_tokens": _int(attrs.get("input_tokens")) or 0, "output_tokens": _int(attrs.get("output_tokens")) or 0,
                        "cache_read_tokens": _int(attrs.get("cache_read_tokens")) or 0,
                        "cache_write_tokens": _int(attrs.get("cache_creation_tokens")) or 0,
                        "cost_usd": _float(attrs.get("cost_usd")), "duration_ms": _int(attrs.get("duration_ms")),
                        "query_source": attrs.get("query_source"), "agent_name": attrs.get("agent.name"),
                        "skill_name": attrs.get("skill.name"), "prompt_id": attrs.get("prompt.id"), "source": "otel",
                    })
                elif name == EVENT_MCP and attrs.get("server_name") and attrs.get("status") == "connected":
                    events.append({"session_id": session_id, "ts": ts, "name": "mcp_server", "data": {"server": attrs["server_name"]}})
                elif name == EVENT_TOOL and attrs.get("tool_use_id"):
                    tool = attrs.get("tool_name") or ""
                    tool_input = attrs.get("tool_input") or attrs.get("tool_parameters")
                    tool_calls.append({
                        "id": attrs["tool_use_id"], "session_id": session_id, "prompt_id": attrs.get("prompt.id"), "ts": ts,
                        "tool": tool, "signature": signatures.signature(tool, tool_input), "detail": signatures.detail(tool, tool_input),
                        "input_bytes": _int(attrs.get("tool_input_size_bytes")), "result_bytes": _int(attrs.get("tool_result_size_bytes")),
                        "duration_ms": _int(attrs.get("duration_ms")), "success": str(attrs.get("success")).lower() == "true",
                        # Telemetry carries no result text: only whether the tool reported an error.
                        "outcome": "ok" if str(attrs.get("success")).lower() == "true" else "failed",
                        "source": "otel",
                    })
    return {"sessions": list(sessions.values()), "requests": requests, "tool_calls": tool_calls, "events": events}


HOOK_EVENTS = ("SessionStart", "SessionEnd", "PreCompact", "PostCompact")


def parse_hook(event: str, body: dict, *, now: str) -> dict | None:
    """A hook post as {"session": {...}, "event": {...}}, or None when it names no session."""
    session_id = (body or {}).get("session_id")
    if not session_id or event not in HOOK_EVENTS:
        return None
    session = {"id": session_id, "seen": now, "cwd": body.get("cwd"), "transcript_path": body.get("transcript_path")}
    if event == "SessionEnd":
        session["end_reason"] = body.get("reason")
    data = {key: body[key] for key in ("source", "reason", "trigger", "context_tokens", "prompt_cache_likely_expired",
                                       "estimated_cache_write_usd") if key in body}
    return {"session": session, "event": {"session_id": session_id, "ts": now, "name": event, "data": data}}


def parse_statusline(body: dict, *, now: str) -> dict | None:
    """The plan pressure a statusline post carries: {"ts", "session_id", "five_hour", "seven_day"}, or None."""
    limits = (body or {}).get("rate_limits") or {}
    five = _float((limits.get("five_hour") or {}).get("used_percentage"))
    seven = _float((limits.get("seven_day") or {}).get("used_percentage"))
    if five is None and seven is None:
        return None
    return {"ts": now, "session_id": body.get("session_id"), "five_hour": five, "seven_day": seven,
            "cwd": (body.get("workspace") or {}).get("current_dir") or body.get("cwd"),
            "transcript_path": body.get("transcript_path")}
