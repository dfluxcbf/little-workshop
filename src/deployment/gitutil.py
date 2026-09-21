"""Git helpers for worktree-based deploys — ported from `little-runner`'s
`gitutil.py`. `branch_location` exists because a checkout isn't
guaranteed to have the usual `+refs/heads/*:refs/remotes/origin/*` fetch
refspec — several submodules in this ecosystem don't — so asking the
remote directly, then fetching that one branch, makes "any branch" mean
any branch in every checkout, not just ones with the default refspec.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOCAL, REMOTE = "local", "remote"


def run(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(args)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result


@dataclass
class WorktreeEntry:
    path: Path
    branch: str | None  # None means detached HEAD
    sha: str


def _ref_exists(project_dir: Path, ref: str) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], project_dir).returncode == 0


def list_branches(project_dir: Path) -> list[str]:
    result = run(["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"], project_dir)
    names: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line in ("origin", "origin/HEAD"):
            continue
        names.add(line.removeprefix("origin/"))
    return sorted(names)


def recent_commits(repo_dir: Path, limit: int = 10) -> list[dict]:
    """The newest `limit` commits of the checked-out branch, each with the
    contents of its `VERSION` file (None where the commit has none)."""
    result = run(["git", "log", f"-n{limit}", "--format=%H%x1f%s"], repo_dir)
    commits = []
    for line in result.stdout.splitlines():
        sha, _, subject = line.partition("\x1f")
        if not sha:
            continue
        shown = subprocess.run(
            ["git", "show", f"{sha}:VERSION"], cwd=str(repo_dir), capture_output=True, text=True,
        )
        version = shown.stdout.strip() if shown.returncode == 0 and shown.stdout.strip() else None
        commits.append({"sha": sha, "subject": subject, "version": version})
    return commits


def github_base_url(repo_dir: Path) -> str | None:
    """`https://github.com/<owner>/<repo>` from the `origin` remote, whether it
    is an SSH (`git@github.com:o/r.git`) or an HTTPS URL; None if not GitHub."""
    result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo_dir), capture_output=True, text=True)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    for prefix in ("git@github.com:", "ssh://git@github.com/", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            path = url.removeprefix(prefix).removesuffix(".git").strip("/")
            return f"https://github.com/{path}" if path.count("/") == 1 else None
    return None


def locate_file_version(repo_dir: Path, content: str, directory: str) -> dict | None:
    """Which file under `directory`, at which commit, holds exactly `content`?
    Matches by git blob hash, and returns the newest commit that has that exact
    version of the file — so a link built from it shows what is actually in use,
    not whatever the file has become since. `None` when no committed file matches."""
    hashed = subprocess.run(
        ["git", "hash-object", "--stdin"], cwd=str(repo_dir), input=content, capture_output=True, text=True,
    )
    if hashed.returncode != 0:
        return None
    blob = hashed.stdout.strip()
    tracked = run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", directory], repo_dir).stdout.split()
    for path in tracked:
        for commit in run(["git", "log", "--format=%H", "--", path], repo_dir).stdout.split():
            found = subprocess.run(
                ["git", "rev-parse", f"{commit}:{path}"], cwd=str(repo_dir), capture_output=True, text=True,
            )
            if found.returncode == 0 and found.stdout.strip() == blob:
                version = subprocess.run(
                    ["git", "show", f"{commit}:VERSION"], cwd=str(repo_dir), capture_output=True, text=True,
                )
                return {
                    "path": path, "commit": commit,
                    "version": version.stdout.strip() if version.returncode == 0 and version.stdout.strip() else None,
                }
    return None


def branch_location(project_dir: Path, branch: str) -> str | None:
    """Assumes `git fetch origin` has already run. When the branch is only
    on the remote and no tracking ref exists, one is fetched so the
    caller can check it out."""
    if _ref_exists(project_dir, f"refs/heads/{branch}"):
        return LOCAL
    if _ref_exists(project_dir, f"refs/remotes/origin/{branch}"):
        return REMOTE
    listed = run(["git", "ls-remote", "--heads", "origin", branch], project_dir)
    if listed.returncode == 0 and listed.stdout.strip():
        run(["git", "fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"], project_dir)
        if _ref_exists(project_dir, f"refs/remotes/origin/{branch}"):
            return REMOTE
    return None


def add_worktree(project_dir: Path, commitish: str, worktree: Path) -> None:
    """`--detach`: checks out `commitish` without claiming its branch name,
    so this works even when that branch is already checked out elsewhere
    (the project's own directory, or another worktree). A stale worktree
    left behind by a crashed previous run is always cleaned up first."""
    if worktree.exists():
        remove_worktree(project_dir, worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "--detach", str(worktree), commitish], project_dir, check=True)


def remove_worktree(project_dir: Path, worktree: Path) -> None:
    run(["git", "worktree", "remove", "--force", str(worktree)], project_dir)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    run(["git", "worktree", "prune"], project_dir)


def list_worktrees(project_dir: Path) -> list[WorktreeEntry]:
    result = run(["git", "worktree", "list", "--porcelain"], project_dir)
    entries: list[WorktreeEntry] = []
    block: dict[str, str] = {}

    def flush() -> None:
        if "worktree" in block:
            raw_path = block["worktree"]
            top = run(["git", "rev-parse", "--show-toplevel"], Path(raw_path))
            path = Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else Path(raw_path)
            branch = block.get("branch", "").removeprefix("refs/heads/") or None
            entries.append(WorktreeEntry(path=path, branch=branch, sha=block.get("HEAD", "")))

    for line in result.stdout.splitlines():
        if not line.strip():
            flush()
            block = {}
            continue
        key, _, value = line.partition(" ")
        block[key] = value
    flush()
    return entries


def is_dirty(worktree_path: Path) -> bool:
    return bool(run(["git", "status", "--porcelain"], worktree_path).stdout.strip())


def current_commit(project_dir: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], project_dir)
    return result.stdout.strip() if result.returncode == 0 else "?"


def deploy_commitish(project_dir: Path, branch: str) -> str:
    """Prefer the local branch — what's actually checked out and worked on
    — falling back to `origin/<branch>` only when no local branch exists."""
    if run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], project_dir).returncode == 0:
        return branch
    return f"origin/{branch}"
