"""The optional semantic conflict check (REQ-000000523): a user-started Job has Claude read a project's
published and draft requirements (Agentic Development's `agent.query`, no file access) and answer with the
pairs it suspects conflict in meaning, each with its reason. Nothing is recorded by the check itself; the user
records a suspected pair from the Job's page (`requirements.record_semantic_conflict`), as a conflict of kind
`semantic` that the lexical analysis (`conflicts.py`) never resolves on its own."""

from __future__ import annotations

import json
import re

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket
from requirements.models import Requirement, RequirementStatus

KIND = "semantic"
QUERY_TIMEOUT_SECONDS = 900.0
CHECKED_STATUSES = (RequirementStatus.PUBLISHED, RequirementStatus.DRAFT)

PROMPT = """You are checking the requirements of the project `{project_id}` for conflicts in meaning: two requirements that cannot both be met, or that state the same rule with different values, behaviour or scope, even when they use different words. Do not report a pair that merely overlaps, refines or repeats the other consistently, and do not report wording or style problems.

The requirements (key, status, title, then the statement):

{requirements}

Answer with only a JSON array, no prose before or after it. Each element is one suspected conflict:
{{"older_key": "<key>", "newer_key": "<key>", "older_text": "<the conflicting part of the first, quoted>", "newer_text": "<the conflicting part of the second, quoted>", "summary": "<one line: what conflicts>", "reason": "<why both cannot hold, in two sentences at most>"}}
Use only keys from the list. Answer `[]` when you find none."""


class SemanticCheckError(RuntimeError):
    pass


def checked(requirements: list[Requirement]) -> list[Requirement]:
    return [r for r in requirements if r.status in CHECKED_STATUSES]


def build_prompt(project_id: str, requirements: list[Requirement]) -> str:
    lines = [f"- {r.key} ({r.status.value}) {r.title}: {(r.description or '').strip()}" for r in requirements]
    return PROMPT.format(project_id=project_id, requirements="\n".join(lines))


def parse_answer(text: str, requirements: list[Requirement]) -> list[dict]:
    """The suspected conflicts in Claude's answer, each naming two different known requirements, ordered older
    first (by creation), with the requirement ids added; anything else in the answer is dropped."""
    match = re.search(r"\[.*\]", text or "", re.DOTALL)
    try:
        items = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        items = []
    by_key = {r.key: r for r in requirements}
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        a, b = by_key.get(str(item.get("older_key"))), by_key.get(str(item.get("newer_key")))
        if a is None or b is None or a.id == b.id:
            continue
        a_text, b_text = str(item.get("older_text") or a.description), str(item.get("newer_text") or b.description)
        if b.created_at < a.created_at:
            a, b, a_text, b_text = b, a, b_text, a_text
        if (a.id, b.id) in seen:
            continue
        seen.add((a.id, b.id))
        found.append({
            "older_key": a.key, "newer_key": b.key, "older_requirement_id": a.id, "newer_requirement_id": b.id,
            "older_text": a_text[:2000], "newer_text": b_text[:2000],
            "summary": str(item.get("summary") or "")[:500], "reason": str(item.get("reason") or "")[:2000],
        })
    return found


def ask_claude(paths: Paths, prompt: str, *, job_id: str, project_id: str) -> str:
    """Claude's answer through Agentic Development's `agent.query` (the Job's peek shows its session)."""
    socket_path = resolve_pillar_socket(paths, "agent", timeout=5.0)
    if socket_path is None:
        raise SemanticCheckError("the Agentic Development pillar is unavailable (unreachable, disabled or faulted)")
    params = {"prompt": prompt, "timeout_seconds": QUERY_TIMEOUT_SECONDS - 20}
    try:
        with client_for_socket(socket_path, timeout=QUERY_TIMEOUT_SECONDS) as client:
            response = client.post("/v1/jobs/agent.query", json={"job_id": job_id, "project_id": project_id, "params": params})
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise SemanticCheckError(f"agent.query unreachable: {exc}") from exc
    if body.get("outcome") != "succeeded":
        raise SemanticCheckError(f"agent.query failed: {body.get('error')}")
    return (body.get("result") or {}).get("text") or ""
