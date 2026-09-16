"""The four-gate authoring pipeline's own logic
(`docs/pillars/policy_compliance.md`'s "Authoring a policy") — pure
prompt-building and response-parsing, independent of *how* a prompt
actually reaches Claude, so it's unit-testable against a plain
`Callable[[str], str]` rather than a real Agentic Development socket.
`policy/app.py` supplies the real one (`policy.agent_client.query`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from policy.models import Policy

QueryFn = Callable[[str], str]


# -- Gate 1: EARS-compliant? --------------------------------------------------


@dataclass
class Gate1Result:
    ears_text: str
    refined: bool


_EARS_REFINED_RE = re.compile(r"^\s*EARS_REFINED:\s*", re.IGNORECASE | re.DOTALL)
_EARS_OK_RE = re.compile(r"^\s*EARS_OK:\s*", re.IGNORECASE | re.DOTALL)


def gate1_ears_check(query: QueryFn, draft_text: str) -> Gate1Result:
    prompt = (
        "You are checking whether a requirement is written in EARS form "
        "(Easy Approach to Requirements Syntax): ubiquitous ('the <system> shall <response>'), "
        "event-driven ('WHEN <trigger>, the <system> shall <response>'), state-driven "
        "('WHILE <state>, the <system> shall <response>'), unwanted-behavior "
        "('IF <trigger>, THEN the <system> shall <response>'), optional-feature "
        "('WHERE <feature is present>, the <system> shall <response>'), or a combination.\n\n"
        f"Requirement:\n{draft_text}\n\n"
        "If it is already valid EARS form, reply with exactly 'EARS_OK: ' followed by the "
        "text unchanged. If not, reply with exactly 'EARS_REFINED: ' followed by your best "
        "rewrite into valid EARS form, preserving the original intent. Reply with nothing else."
    )
    response = query(prompt).strip()
    refined_match = _EARS_REFINED_RE.match(response)
    if refined_match:
        return Gate1Result(ears_text=response[refined_match.end():].strip(), refined=True)
    ok_match = _EARS_OK_RE.match(response)
    if ok_match:
        return Gate1Result(ears_text=response[ok_match.end():].strip(), refined=False)
    # Any other shape from the model is treated as a refinement proposal
    # rather than silently trusting the original draft was already fine.
    return Gate1Result(ears_text=response, refined=True)


# -- Gate 2: mechanically enforceable? ----------------------------------------


@dataclass
class Gate2Result:
    enforceable: bool
    reason: str


_ENFORCEABLE_RE = re.compile(r"ENFORCEABLE:\s*(yes|no)\s*-?\s*(.*)", re.IGNORECASE | re.DOTALL)


def gate2_enforceability_check(query: QueryFn, ears_text: str) -> Gate2Result:
    prompt = (
        "A policy's test script must verify a requirement automatically, with no human "
        "judgment call inside the check itself. Given this EARS requirement:\n\n"
        f"{ears_text}\n\n"
        "Can an automated test script verify this with no human judgment call? Reply with "
        "exactly 'ENFORCEABLE: yes' or 'ENFORCEABLE: no - <brief reason>'. Reply with nothing else."
    )
    response = query(prompt).strip()
    match = _ENFORCEABLE_RE.search(response)
    if match is None:
        # An unparseable answer is never treated as a pass — Gate 2 fails
        # closed, the same "silence should never default to permissive"
        # discipline this document applies to action-gating.
        return Gate2Result(enforceable=False, reason=f"could not parse enforceability verdict: {response!r}")
    enforceable = match.group(1).lower() == "yes"
    reason = match.group(2).strip()
    return Gate2Result(enforceable=enforceable, reason=reason)


# -- Gate 3b: semantic conflict (best-effort) ---------------------------------


@dataclass
class Gate3bResult:
    conflicts: bool
    conflicting_topic: str | None
    detail: str


_CONFLICT_RE = re.compile(r"CONFLICT:\s*(\S+)\s*-?\s*(.*)", re.IGNORECASE | re.DOTALL)


def gate3b_conflict_check(query: QueryFn, ears_text: str, existing_policies: list[Policy]) -> Gate3bResult:
    if not existing_policies:
        return Gate3bResult(conflicts=False, conflicting_topic=None, detail="")
    existing_block = "\n".join(f"- [{p.topic}] {p.ears_text}" for p in existing_policies)
    prompt = (
        "Check whether a new requirement contradicts any existing one — not merely similar, "
        "but a genuine contradiction (both cannot hold at once).\n\n"
        f"New requirement:\n{ears_text}\n\n"
        f"Existing requirements:\n{existing_block}\n\n"
        "Reply with exactly 'NO_CONFLICT' if there is no contradiction, or "
        "'CONFLICT: <topic> - <brief reason>' naming the one topic it contradicts. "
        "Reply with nothing else."
    )
    response = query(prompt).strip()
    match = _CONFLICT_RE.search(response)
    if match is None:
        return Gate3bResult(conflicts=False, conflicting_topic=None, detail="")
    return Gate3bResult(conflicts=True, conflicting_topic=match.group(1), detail=match.group(2).strip())


# -- Script generation ---------------------------------------------------------


_CODE_FENCE_RE = re.compile(r"^```(?:python)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def generate_test_script(query: QueryFn, ears_text: str, *, project_bound: bool) -> str:
    scope_note = (
        "This policy is project-bound: the script may only read the target project's own "
        "repository checkout and its little-workshop project-files directory."
        if project_bound else
        "This policy is framework-bound: the script may only read little-workshop's own "
        "enumerated source/docs files, never a specific user project's."
    )
    prompt = (
        "Write ONE pytest test script (a single Python file's full contents, nothing else) "
        "that mechanically enforces this EARS requirement. Use `pytest.skip()` for the "
        "not-applicable case (an optional-feature 'WHERE' requirement whose feature isn't "
        f"present), distinct from an assertion failure. {scope_note}\n\n"
        f"Requirement:\n{ears_text}\n\n"
        "Reply with only the script's own source code, no explanation."
    )
    response = query(prompt).strip()
    fence_match = _CODE_FENCE_RE.match(response)
    return fence_match.group(1) if fence_match else response
