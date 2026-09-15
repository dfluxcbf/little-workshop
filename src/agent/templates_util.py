"""Access to the packaged prompt templates — ported from `little-runner`'s
`templates_util.py`."""

from __future__ import annotations

from importlib import resources


def read_prompt(name: str) -> str:
    return resources.files("agent").joinpath("templates", "prompts", name).read_text(encoding="utf-8")


def render(text: str, **values: str) -> str:
    """Minimal `{name}` substitution that leaves every other brace alone."""
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
