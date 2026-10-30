"""A Job's brief: the small part of its params and result, kept beside them when the Job is written.

A list of Jobs shows what each is about and how it ended, never a 1 MB test-run result or a JUnit file
someone posted as a param; reading and parsing those on every listing was most of what a list of Jobs
cost (28 MB for 1,785 Jobs on 2026-10-05). So each value up to a small size is kept as it is (a scalar,
or a short list or map of scalars), and every larger one is only named, under `_omitted`. The full Job
stays one read away."""

from __future__ import annotations

from typing import Any

MAX_TEXT = 1000  # characters of a string kept
MAX_ITEMS = 20  # items of a list, or keys of a map, kept (each of them a short scalar)


def _small(value: Any, *, nested: bool = False) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= (200 if nested else MAX_TEXT)
    if nested:
        return False  # one level of nesting only: a list of maps (a test run's suites) is left out
    if isinstance(value, list):
        return len(value) <= MAX_ITEMS and all(_small(item, nested=True) for item in value)
    if isinstance(value, dict):
        return len(value) <= MAX_ITEMS and all(isinstance(k, str) and _small(v, nested=True) for k, v in value.items())
    return False


def brief(value: dict | None) -> dict | None:
    """`value` without its large entries, which are named under `_omitted` (absent when none was)."""
    if value is None:
        return None
    kept = {key: item for key, item in value.items() if _small(item)}
    omitted = [key for key in value if key not in kept]
    if omitted:
        kept["_omitted"] = omitted
    return kept
