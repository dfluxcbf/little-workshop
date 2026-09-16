"""Thin wrappers around the git commands used to bring up a new repo —
ported from `bootstrap-little-project`'s `gitops.py`, minus `lvx init`:
Version Control & Release doesn't exist yet (M3), and a little-workshop-
bootstrapped project has no reason to depend on the tool it replaces.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def init_repo_and_commit(
    path: Path, *, commit_message: str = "first", author_email: str | None = None
) -> None:
    run(["git", "init", "-b", "master"], cwd=path)
    if author_email:
        run(["git", "config", "user.email", author_email], cwd=path)
        run(["git", "config", "user.name", "little-workshop"], cwd=path)
    run(["git", "add", "-A"], cwd=path)
    run(["git", "commit", "-m", commit_message], cwd=path)


def create_dev_branch(path: Path) -> None:
    run(["git", "checkout", "-b", "dev"], cwd=path)
    run(["git", "checkout", "master"], cwd=path)


def add_remote_and_push(path: Path, remote_url: str) -> None:
    run(["git", "remote", "add", "origin", remote_url], cwd=path)
    run(["git", "push", "-u", "origin", "master:master"], cwd=path)
    run(["git", "push", "-u", "origin", "dev:dev"], cwd=path)


def remote_url(path: Path) -> str | None:
    """The `origin` remote's URL, or None if there isn't one — an
    imported project (M8) is real, pre-existing state to read, not
    something to require or synthesize the way `project.bootstrap`
    creates it."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=path, capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
