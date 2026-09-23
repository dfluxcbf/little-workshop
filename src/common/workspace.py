"""The workspace: the directory that holds every project checkout (`~/little-projects`).

A session started here works across projects. It is addressed with the reserved
project id `workspace`, which is never looked up in the Registry."""

from __future__ import annotations

import os
from pathlib import Path

WORKSPACE_ID = "workspace"


def workspace_dir() -> Path:
    override = os.environ.get("LITTLE_WORKSHOP_WORKSPACE_DIR")
    if override:
        return Path(override)
    repo = os.environ.get("LITTLE_WORKSHOP_REPO_DIR")
    if repo:
        return Path(repo).resolve().parent
    return Path.home() / "little-projects"
