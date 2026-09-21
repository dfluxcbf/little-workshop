"""EARS (Easy Approach to Requirements Syntax) form check for a requirement's description.

A requirement is `[While <state>,] [When <event>,] [Where <feature>,] [If <condition>, then]
the <system> shall <response>` — a fixed, optional-clause prefix followed by a determiner, a
system name and `shall`. Anything else (a description of behaviour, a status note) is not a
requirement and is refused with a message that says how to fix it."""

from __future__ import annotations

import re

_CLAUSE = r"(?:while|when|where|if)\b[^,]{1,300}?,\s*(?:then\s+)?"
_EARS_RE = re.compile(
    rf"^(?:{_CLAUSE})*(?:the|each|every|all|any|an?)\s+[^,.;:]{{1,160}}?\s+shall\s+\S",
    re.IGNORECASE | re.DOTALL,
)

HINT = (
    "a requirement must be written in EARS form: '<The system> shall <response>', optionally "
    "prefixed by 'While <state>,', 'When <trigger>,', 'Where <feature>,' or 'If <condition>, then' "
    "(e.g. 'When the user taps Redeploy, the Dashboard shall submit one job per selected pillar.')"
)


def is_ears(text: str | None) -> bool:
    return bool(text and _EARS_RE.match(text.strip()))


def ears_error(text: str | None) -> str | None:
    """None when `text` is EARS-compliant, else why it is not."""
    if is_ears(text):
        return None
    return f"description is not EARS-compliant: {HINT}"
