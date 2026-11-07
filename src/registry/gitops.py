"""Thin wrappers around the git commands used to bring up a new repo —
ported from `bootstrap-little-project`'s `gitops.py`. `lvx init`'s own
equivalent (writing `.lvx/config.json`) is no longer missing (M16,
2026-09-17): it's a real cross-pillar call to Version Control &
Release, made from `registry/app.py`'s own `job_bootstrap` endpoint via
`registry/version_client.py` — never a Python import of `version`'s
own code, and never a dependency on the real `lvx` tool this pillar is
absorbing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from common.windows import git_command, is_windows_project

# The line every non-Unity Windows-hosted project's .gitattributes starts with: text stored and checked out
# with LF whichever git touches it, so WSL tools and Windows tools see the same files (REQ-000000511).
EOL_RULE = "* text=auto eol=lf"


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    if args and args[0] == "git":
        args = [*git_command(cwd), *args[1:]]
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def pin_autocrlf(path: Path) -> bool:
    """`core.autocrlf=false` in a Windows-hosted checkout (Windows' git defaults to true, WSL's to nothing, so
    the two disagreed about every text file); False and nothing done for a WSL checkout."""
    if not is_windows_project(path):
        return False
    run(["git", "config", "core.autocrlf", "false"], cwd=path)
    return True


def eol_gitattributes(current: str | None) -> str | None:
    """The .gitattributes text holding `EOL_RULE` first, or None when `current` already has it."""
    lines = (current or "").splitlines()
    if EOL_RULE in (line.strip() for line in lines):
        return None
    kept = [line for line in lines if line.strip() not in ("* text=auto", "* text=auto eol=crlf")]
    return "\n".join([EOL_RULE, *kept]) + "\n"


def init_repo_and_commit(
    path: Path, *, commit_message: str = "first", author_email: str | None = None
) -> None:
    run(["git", "init", "-b", "master"], cwd=path)
    pin_autocrlf(path)
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
        [*git_command(path), "remote", "get-url", "origin"], cwd=path, capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
