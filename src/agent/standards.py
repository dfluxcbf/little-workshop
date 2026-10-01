"""A task's read-only copy of little-workshop's `shared_project_standards/`.

The copy holds every Markdown file at the folder's root plus the subfolder of the
project the task is for, sits in a temporary directory, and is deleted when the task
completes. The agent reads it; the original is never touched."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from common.paths import Paths
from common.registry_client import ProjectResolutionError, resolve_project

WORKSHOP_PROJECT = "little-workshop"
STANDARDS_FOLDER = "shared_project_standards"


def project_folder_name(project_id: str) -> str:
    return project_id.replace("-", "_") + "_standards"


def resolve_standards_root(paths: Paths) -> Path | None:
    try:
        checkout = Path(resolve_project(paths, WORKSHOP_PROJECT)["path"])
    except (ProjectResolutionError, KeyError):
        return None
    root = checkout / STANDARDS_FOLDER
    return root if root.is_dir() else None


def make_copy(root: Path, project_id: str, *, run_id: str) -> Path:
    copy = Path(tempfile.mkdtemp(prefix=f"lws-standards-{run_id}-")) / STANDARDS_FOLDER
    copy.mkdir()
    for doc in sorted(root.glob("*.md")):
        shutil.copy2(doc, copy / doc.name)
    project_dir = root / project_folder_name(project_id)
    if project_dir.is_dir():
        for doc in sorted(project_dir.rglob("*.md")):
            target = copy / project_dir.name / doc.relative_to(project_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(doc, target)
    return copy


def remove_copy(copy: Path | None) -> None:
    if copy is not None:
        shutil.rmtree(copy.parent, ignore_errors=True)
