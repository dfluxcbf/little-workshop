"""The small subset of git operations Agentic Development needs —
its own copy, not an import from `deployment.gitutil`/`version.gitops`:
no pillar shares a Python import path with another
(docs/architecture.md's PI section), so a little duplication of pure,
dependency-free git wrappers is the accepted cost of real process
independence, same as Deployment & Servers and Version Control &
Release each already carrying their own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(args)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def is_dirty(project_dir: Path) -> bool:
    return bool(run(["git", "status", "--porcelain"], project_dir).stdout.strip())


def current_branch(project_dir: Path) -> str:
    return run(["git", "branch", "--show-current"], project_dir).stdout.strip()


def head_sha(project_dir: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], project_dir).stdout.strip()


def branch_exists(project_dir: Path, ref: str) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], project_dir).returncode == 0


def has_origin_remote(project_dir: Path) -> bool:
    """A project.bootstrap'd project with no GitHub repo created has no
    `origin` at all — nothing to fetch from or publish to, and that's a
    legitimate, common state, not an error."""
    result = run(["git", "remote"], project_dir)
    return "origin" in result.stdout.split()


def fetch(project_dir: Path) -> None:
    if not has_origin_remote(project_dir):
        return
    run(["git", "fetch", "origin", "--prune"], project_dir, check=True)


def checkout(project_dir: Path, branch: str) -> None:
    run(["git", "checkout", branch], project_dir, check=True)


def checkout_new(project_dir: Path, branch: str, base: str | None = None) -> None:
    args = ["git", "checkout", "-b", branch]
    if base:
        args.append(base)
    run(args, project_dir, check=True)


def pull_ff_only(project_dir: Path, branch: str) -> None:
    if not has_origin_remote(project_dir):
        return  # nothing to pull for a local-only project
    run(["git", "pull", "--ff-only", "origin", branch], project_dir, check=True)


def branch_location(project_dir: Path, branch: str) -> str | None:
    """'local', 'remote', or None. Assumes `fetch()` already ran."""
    if branch_exists(project_dir, f"refs/heads/{branch}"):
        return "local"
    if branch_exists(project_dir, f"refs/remotes/origin/{branch}"):
        return "remote"
    return None


def checkout_remote_tracking(project_dir: Path, branch: str) -> None:
    run(["git", "checkout", "-b", branch, "--no-track", f"refs/remotes/origin/{branch}"], project_dir, check=True)
    run(["git", "branch", f"--set-upstream-to=origin/{branch}", branch], project_dir)


def commit_if_dirty(project_dir: Path, message: str) -> bool:
    run(["git", "add", "-A"], project_dir)
    result = run(["git", "diff", "--cached", "--quiet"], project_dir)
    if result.returncode != 0:
        run(["git", "commit", "-q", "-m", message], project_dir, check=True)
        return True
    return False


def push(project_dir: Path, branch: str) -> None:
    run(["git", "push", "-u", "origin", branch], project_dir, check=True)


def ahead_count(project_dir: Path, base: str, branch: str) -> int:
    result = run(["git", "rev-list", "--count", f"{base}..{branch}"], project_dir)
    return int(result.stdout.strip() or "0")


def reset_branch_to(project_dir: Path, branch: str, sha: str) -> None:
    run(["git", "branch", "-f", branch, sha], project_dir, check=True)
