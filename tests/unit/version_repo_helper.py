"""Shared helper for version-pillar tests: a real git repo, git init'd
on master with dev branched off it and `version.init` already run —
every test starts from this same known-good state."""

from __future__ import annotations

import subprocess
from pathlib import Path

from version.commands.init import cmd_init


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (repo / "README.md").write_text("# repo\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["checkout", "-b", "dev"], repo)
    _git(["checkout", "master"], repo)

    result = cmd_init(repo, production_branch="master", integration_branch="dev")
    assert result.success, result.message
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "chore: version init"], repo)
    _git(["checkout", "dev"], repo)
    _git(["merge", "--ff-only", "master"], repo)
    _git(["checkout", "master"], repo)

    return repo
