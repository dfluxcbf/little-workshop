"""Requirement conflict detection: pure code, no model, the same requirements always give the same conflicts.

Each requirement's description is read as EARS statements (`requirements/ears.py`'s form): its sentences, and
within a sentence every `, and the <system> shall …` after the first, each with the sentence's `While`/`When`/
`Where`/`If` clauses. Text is compared as words: lower case, punctuation and quotes dropped, `a`/`an`/`the`
ignored. Two requirements of one project conflict when:

- `duplicate_key`: they carry the same key (each later copy of a key against its first);
- `contradiction`: a statement of each has the same conditions, the same system and the same response, and one
  says `shall` where the other says `shall not` (or `shall never`);
- `different_value`: a statement of each has the same conditions, system and polarity, and the same response
  except for the numbers in it (`within 5 seconds` against `within 10 seconds`).

The older of the two is the one created first (then the lower key, then the lower id). A conflict's id is a hash
of its kind, its two requirement ids and the statements involved, so a later analysis finds the same conflict
under the same id."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from requirements.models import Requirement

_WORD = re.compile(r"[a-z0-9]+(?:[._'-][a-z0-9]+)*")
_IGNORED = frozenset({"a", "an", "the"})
_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_DETERMINER = r"(?:the|each|every|all|any|an?)"
_CLAUSE = re.compile(r"^\s*(while|when|where|if)\b([^,]{1,300}?),\s*(?:then\s+)?", re.IGNORECASE)
_SUBJECT = re.compile(rf"^\s*{_DETERMINER}\s+([^,.;:]{{1,160}}?)\s+shall\s+(not\s+|never\s+)?", re.IGNORECASE)
_NEXT_SUBJECT = re.compile(rf",?\s+and\s+(?={_DETERMINER}\s+[^,.;:]{{1,160}}?\s+shall\s)", re.IGNORECASE)
# A sentence ends at `.`, `!` or `?` followed by white space and a capital letter, or at the end of the text;
# `pyproject.toml` or `0.2` stays whole.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

DUPLICATE_KEY = "duplicate_key"
CONTRADICTION = "contradiction"
DIFFERENT_VALUE = "different_value"
KINDS = {
    DUPLICATE_KEY: "Same key",
    CONTRADICTION: "Contradiction",
    DIFFERENT_VALUE: "Different values",
}


def words(text: str) -> tuple[str, ...]:
    return tuple(w for w in _WORD.findall(text.lower().replace("`", "")) if w not in _IGNORED)


@dataclass(frozen=True)
class Statement:
    """One `[conditions,] the <system> shall [not] <response>` of a requirement, as words."""

    conditions: frozenset[tuple[str, tuple[str, ...]]]
    system: tuple[str, ...]
    negative: bool
    response: tuple[str, ...]
    text: str  # as written, for the reader

    def shape(self) -> tuple:
        """The statement with its numbers blanked: two statements of one shape differ in numbers at most."""
        return (self.conditions, self.system, self.negative, tuple("#" if _NUMBER.match(w) else w for w in self.response))


def statements(description: str) -> list[Statement]:
    """The EARS statements of a description, in order; a sentence not in EARS form gives none."""
    found: list[Statement] = []
    for sentence in _SENTENCE_END.split((description or "").strip()):
        rest, conditions = sentence.strip(), set()
        while (clause := _CLAUSE.match(rest)) is not None:
            conditions.add((clause.group(1).lower(), words(clause.group(2))))
            rest = rest[clause.end():]
        prefix = sentence.strip()[:len(sentence.strip()) - len(rest)]  # the clauses as written, shown with each statement
        for part in _NEXT_SUBJECT.split(rest):
            subject = _SUBJECT.match(part)
            if subject is None:
                continue
            response = words(part[subject.end():])
            if response:
                found.append(Statement(
                    frozenset(conditions), words(subject.group(1)), subject.group(2) is not None, response,
                    (prefix + part.strip()).rstrip(".!?"),
                ))
    return found


@dataclass(frozen=True)
class Conflict:
    id: str
    project_id: str
    kind: str
    older: Requirement
    newer: Requirement
    older_text: str  # the part of each requirement in conflict
    newer_text: str
    summary: str


def _age(requirement: Requirement) -> tuple:
    return (requirement.created_at, requirement.key, requirement.id)


def _conflict(kind: str, a: Requirement, b: Requirement, a_text: str, b_text: str, summary: str) -> Conflict:
    (older, older_text), (newer, newer_text) = sorted(((a, a_text), (b, b_text)), key=lambda pair: _age(pair[0]))
    digest = hashlib.sha256("\x1f".join((kind, older.id, newer.id, older_text, newer_text)).encode()).hexdigest()
    return Conflict(digest[:32], older.project_id, kind, older, newer, older_text, newer_text, summary)


def _statement_conflict(a: Statement, b: Statement) -> tuple[str, str] | None:
    """(kind, summary) when two statements of different requirements conflict, else None."""
    if a.conditions != b.conditions or a.system != b.system:
        return None
    system = " ".join(a.system)
    if a.negative != b.negative and a.response == b.response:
        return CONTRADICTION, f"one requires the {system} to {' '.join(a.response)}, the other forbids it"
    if a.response != b.response and a.shape() == b.shape():
        values = [(x, y) for x, y in zip(a.response, b.response) if x != y]
        shown = ", ".join(f"{x} against {y}" for x, y in values)
        return DIFFERENT_VALUE, f"the same rule for the {system} with different values: {shown}"
    return None


def find_conflicts(requirements: list[Requirement]) -> list[Conflict]:
    """Every conflict between two requirements of the same project, oldest pair first."""
    found: dict[str, Conflict] = {}

    def add(c: Conflict) -> None:
        found.setdefault(c.id, c)

    # Only statements with the same project, conditions and system can conflict: compare within those groups.
    by_key: dict[tuple, list[Requirement]] = {}
    groups: dict[tuple, list[tuple[Requirement, Statement]]] = {}
    for r in sorted(requirements, key=_age):
        by_key.setdefault((r.project_id, r.key), []).append(r)
        for s in statements(r.description):
            groups.setdefault((r.project_id, s.conditions, s.system), []).append((r, s))
    for oldest, *copies in by_key.values():  # each later copy of a key conflicts with the first, not with each other
        for b in copies:
            add(_conflict(DUPLICATE_KEY, oldest, b, oldest.title, b.title, f"two requirements carry the key {oldest.key}"))
    for members in groups.values():
        for i, (a, sa) in enumerate(members):
            for b, sb in members[i + 1:]:
                if a.id != b.id and (verdict := _statement_conflict(sa, sb)) is not None:
                    add(_conflict(verdict[0], a, b, sa.text, sb.text, verdict[1]))
    return sorted(found.values(), key=lambda c: (_age(c.older), _age(c.newer), c.kind))
