"""Helpers every `lws` command module shares."""

from __future__ import annotations

import json


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))
