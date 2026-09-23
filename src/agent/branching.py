"""The BranchingSpec — ported from `little-runner`'s `branching.py`.
Issue-body parsing (`parse_branching`/`parse_review`) is pure text
parsing, kept close to verbatim since GitHub still renders its issue
form the same way. `resolve()` no longer reads `.lvx/config.json`
directly — it validates against the branch-type policy Version Control
& Release's own Pillar Interface returns (`GET /v1/config`), the same
shape `.lvx/config.json` always had, so nothing here needs its own
parallel copy of that policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MODE_AUTO, MODE_NEW, MODE_EXISTING = "auto", "new", "existing"
MODES = (MODE_AUTO, MODE_NEW, MODE_EXISTING)

H_BRANCHING = "branching"
H_TYPE = "branch type"
H_BASE = "start from"
H_EXISTING = "branch name"
H_FINISH = "finish the branch when done"
H_REVIEW = "review"

UNSET_VALUES = {"", "_no response_", "none", "n/a", "-", "let claude decide", "claude decides", "auto"}

_MODE_WORDS = {
    "new branch": MODE_NEW,
    "new": MODE_NEW,
    "existing branch": MODE_EXISTING,
    "existing": MODE_EXISTING,
    "let claude decide": MODE_AUTO,
    "claude decides": MODE_AUTO,
    "auto": MODE_AUTO,
}

AUTO_APPROVE_WORDS = ("auto approve", "auto-approve", "auto", "approve")


class BranchingError(ValueError):
    """A branching directive that cannot be carried out."""


@dataclass
class BranchingSpec:
    mode: str = MODE_AUTO
    branch_type: str | None = None  # for MODE_NEW
    base: str | None = None  # for MODE_NEW
    branch: str | None = None  # for MODE_EXISTING
    auto_approve: bool = False

    @property
    def specified(self) -> bool:
        """True when the flow (not the agent) owns the branching."""
        return self.mode in (MODE_NEW, MODE_EXISTING)


# --------------------------------------------------------------------------- issue body


def parse_sections(body: str) -> dict[str, str]:
    """Split a GitHub issue-form body into {lowercased heading: value}."""
    sections: dict[str, str] = {}
    heading: str | None = None
    buf: list[str] = []
    for line in (body or "").splitlines():
        match = re.match(r"^\s{0,3}#{2,3}\s+(.+?)\s*$", line)
        if match:
            if heading is not None:
                sections[heading] = "\n".join(buf).strip()
            heading = match.group(1).strip().lower()
            buf = []
        elif heading is not None:
            buf.append(line)
    if heading is not None:
        sections[heading] = "\n".join(buf).strip()
    return sections


def _value(sections: dict[str, str], key: str) -> str | None:
    raw = sections.get(key, "").strip()
    return None if raw.lower() in UNSET_VALUES else raw


def _checked(sections: dict[str, str], key: str) -> bool:
    return bool(re.search(r"^\s*[-*]\s*\[[xX]\]", sections.get(key, ""), re.MULTILINE))


def parse_review(sections: dict[str, str]) -> bool:
    """True when the issue asks for auto-approve."""
    raw = (sections.get(H_REVIEW, "") or "").strip().lower()
    if raw and raw not in UNSET_VALUES:
        return any(word in raw for word in AUTO_APPROVE_WORDS)
    return _checked(sections, H_FINISH)


def parse_branching(body: str) -> BranchingSpec:
    """Read the branching directives out of an issue body."""
    sections = parse_sections(body)
    spec = BranchingSpec(auto_approve=parse_review(sections))

    raw_mode = _value(sections, H_BRANCHING)
    mode = _MODE_WORDS.get((raw_mode or "").lower())
    if raw_mode is not None and mode is None:
        mode = MODE_AUTO  # unreadable value: let the agent decide, never fail
    spec.mode = mode or MODE_AUTO

    if spec.mode == MODE_NEW:
        spec.branch_type = (_value(sections, H_TYPE) or "").lower() or None
        spec.base = _value(sections, H_BASE)
    elif spec.mode == MODE_EXISTING:
        spec.branch = _value(sections, H_EXISTING)
    return spec


# --------------------------------------------------------------------------- branch-type policy


def _base_branch(role: str, cfg: dict) -> str:
    return cfg["production_branch"] if role == "production" else cfg["integration_branch"]


def required_base(branch_type: str, cfg: dict) -> str:
    """The only branch `version <type> start` may be run from."""
    policy = cfg["branch_types"].get(branch_type)
    role = policy["base"] if policy else "integration"
    return _base_branch(role, cfg)


def branch_type_from_name(branch: str, cfg: dict) -> str | None:
    for branch_type, policy in cfg["branch_types"].items():
        prefix = policy.get("prefix", branch_type)
        if branch.startswith(f"{prefix}/"):
            return branch_type
    return None


def default_branch_type(intent: str) -> str:
    return "feature" if intent == "milestone" else "improvement"


def resolve(spec: BranchingSpec, intent: str, cfg: dict) -> BranchingSpec:
    """Fill in the defaults a specified spec is allowed to omit, and
    reject a combination Version Control & Release cannot execute."""
    if spec.mode == MODE_NEW:
        given_type = spec.branch_type if spec.branch_type in cfg["branch_types"] else None
        branch_type = given_type or default_branch_type(intent)
        base = spec.base or required_base(branch_type, cfg)
        needed = required_base(branch_type, cfg)
        if base != needed:
            raise BranchingError(
                f"`{branch_type} start` can only be run from `{needed}`, but the task asks to start "
                f"from `{base}`. Change the base to `{needed}`, or pick a branch type that starts from `{base}`."
            )
        return BranchingSpec(MODE_NEW, branch_type, base, None, spec.auto_approve)
    if spec.mode == MODE_EXISTING:
        if not spec.branch:
            raise BranchingError(
                "the task asks to use an existing branch but no branch name was given; provide one "
                "(e.g. `improvement/better_something`), or set branching to auto."
            )
        return BranchingSpec(MODE_EXISTING, branch_type_from_name(spec.branch, cfg), None, spec.branch, spec.auto_approve)
    return BranchingSpec(MODE_AUTO, None, None, None, spec.auto_approve)
