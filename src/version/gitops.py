"""Git subprocess wrapper for Gitflow-critical operations — ported
near-verbatim from `little-versions`' `git/ops.py`. Refuses on conflict,
never auto-resolves (docs/milestones.md M3)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(Exception):
    pass


class DirtyWorkingTreeError(GitError):
    pass


class MissingBranchError(GitError):
    pass


class MergeConflictError(GitError):
    pass


class RebaseConflictError(GitError):
    pass


class TagExistsError(GitError):
    pass


def run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=str(cwd), check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise GitError(
            f"git command failed: {' '.join(args)}\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def current_branch(repo_root: Path) -> str:
    result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    branch = result.stdout.strip()
    if branch == "HEAD":
        raise GitError("Repository is in detached HEAD state.")
    return branch


def is_git_repo(path: Path) -> bool:
    result = run(["git", "rev-parse", "--git-dir"], path, check=False)
    return result.returncode == 0


def has_commits(repo_root: Path) -> bool:
    result = run(["git", "rev-list", "-n", "1", "--all"], repo_root, check=False)
    return bool(result.stdout.strip())


def is_dirty(repo_root: Path) -> bool:
    """Uncommitted changes outside `.lvx/` (tool-managed metadata) block
    operations; changes inside it never do."""
    result = run(["git", "status", "--porcelain"], repo_root)
    for line in result.stdout.splitlines():
        file_path = line[3:].strip().split(" -> ")[-1]
        if not file_path.startswith(".lvx/"):
            return True
    return False


def branch_exists(repo_root: Path, name: str) -> bool:
    result = run(["git", "rev-parse", "--verify", f"refs/heads/{name}"], repo_root, check=False)
    return result.returncode == 0


def create_branch(repo_root: Path, name: str, from_ref: str) -> None:
    if branch_exists(repo_root, name):
        raise GitError(f"Branch already exists: {name!r}")
    run(["git", "branch", name, from_ref], repo_root)


def checkout(repo_root: Path, name: str) -> None:
    run(["git", "checkout", name], repo_root)


def create_and_checkout(repo_root: Path, name: str, from_ref: str) -> None:
    run(["git", "checkout", "-b", name, from_ref], repo_root)


def merge_no_ff(repo_root: Path, branch: str, message: str) -> None:
    """Raises MergeConflictError if conflicts occur (never auto-resolves)."""
    result = run(["git", "merge", "--no-ff", "-m", message, branch], repo_root, check=False)
    if result.returncode != 0:
        status = run(["git", "status", "--porcelain"], repo_root, check=False)
        if "UU" in status.stdout or "AA" in status.stdout or "DD" in status.stdout:
            raise MergeConflictError(
                f"Merge conflict when merging {branch!r}. Resolve conflicts manually, then commit."
            )
        raise GitError(f"Merge failed: {result.stderr.strip() or result.stdout.strip()}")


def create_annotated_tag(repo_root: Path, tag: str, message: str, commit: str | None = None) -> None:
    if tag_exists(repo_root, tag):
        raise TagExistsError(f"Tag already exists: {tag!r}")
    args = ["git", "tag", "-a", tag, "-m", message]
    if commit:
        args.append(commit)
    run(args, repo_root)


def tag_exists(repo_root: Path, tag: str) -> bool:
    result = run(["git", "tag", "-l", tag], repo_root)
    return bool(result.stdout.strip())


def list_tags(repo_root: Path, pattern: str = "") -> list[str]:
    args = ["git", "tag", "-l"]
    if pattern:
        args.append(pattern)
    result = run(args, repo_root)
    return [t for t in result.stdout.strip().splitlines() if t]


def commit_file(repo_root: Path, file_path: Path, message: str) -> str:
    run(["git", "add", str(file_path.relative_to(repo_root))], repo_root)
    run(["git", "commit", "-m", message], repo_root)
    return get_commit_sha(repo_root, "HEAD")


def commit_all_tracked(repo_root: Path, message: str) -> str:
    run(["git", "add", "-u"], repo_root)
    run(["git", "commit", "-m", message], repo_root)
    return get_commit_sha(repo_root, "HEAD")


def commit_if_dirty(repo_root: Path, message: str) -> bool:
    run(["git", "add", "-A"], repo_root)
    result = run(["git", "diff", "--cached", "--quiet"], repo_root, check=False)
    if result.returncode != 0:
        run(["git", "commit", "-m", message], repo_root)
        return True
    return False


def get_commit_sha(repo_root: Path, ref: str = "HEAD") -> str:
    result = run(["git", "rev-parse", ref], repo_root)
    return result.stdout.strip()


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    result = run(["git", "merge-base", "--is-ancestor", ancestor, descendant], repo_root, check=False)
    return result.returncode == 0


def delete_branch(repo_root: Path, name: str, force: bool = False) -> None:
    flag = "-D" if force else "-d"
    run(["git", "branch", flag, name], repo_root)


def delete_remote_branch(repo_root: Path, name: str, remote: str = "origin") -> None:
    run(["git", "push", remote, "--delete", name], repo_root)


def get_tag_commit(repo_root: Path, tag: str) -> str:
    result = run(["git", "rev-list", "-n", "1", tag], repo_root)
    return result.stdout.strip()


def write_file_in_repo(repo_root: Path, rel_path: str, content: str) -> Path:
    target = repo_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def get_merge_base(repo_root: Path, ref1: str, ref2: str) -> str:
    result = run(["git", "merge-base", ref1, ref2], repo_root)
    return result.stdout.strip()


def rebase_onto(repo_root: Path, base_branch: str) -> None:
    """Raises RebaseConflictError if conflicts occur."""
    result = run(["git", "rebase", base_branch], repo_root, check=False)
    if result.returncode != 0:
        status = run(["git", "status", "--porcelain"], repo_root, check=False)
        if any(line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for line in status.stdout.splitlines()):
            raise RebaseConflictError(
                f"Rebase conflict when rebasing onto {base_branch!r}. "
                "Resolve conflicts, stage the files, then run: version feature finish --continue\n"
                "To cancel, run: version feature finish --abort"
            )
        raise GitError(f"Rebase failed: {result.stderr.strip() or result.stdout.strip()}")


def rebase_continue(repo_root: Path) -> None:
    full_env = {**os.environ, "GIT_EDITOR": "true"}
    result = subprocess.run(
        ["git", "rebase", "--continue"], cwd=str(repo_root), check=False, capture_output=True, text=True, env=full_env
    )
    if result.returncode != 0:
        status = run(["git", "status", "--porcelain"], repo_root, check=False)
        if any(line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for line in status.stdout.splitlines()):
            raise RebaseConflictError(
                "Further rebase conflicts remain. Resolve them, stage the files, then run: "
                "version feature finish --continue\nTo cancel, run: version feature finish --abort"
            )
        raise GitError(f"Rebase --continue failed: {result.stderr.strip() or result.stdout.strip()}")


def rebase_abort(repo_root: Path) -> None:
    run(["git", "rebase", "--abort"], repo_root)


def is_merge_in_progress(repo_root: Path) -> bool:
    git_dir_result = run(["git", "rev-parse", "--git-dir"], repo_root)
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    return (git_dir / "MERGE_HEAD").exists()


def merge_abort(repo_root: Path) -> None:
    run(["git", "merge", "--abort"], repo_root)


def merge_commit(repo_root: Path, message: str) -> str:
    """Caller must have staged all conflict resolutions before calling this."""
    run(["git", "commit", "-m", message], repo_root)
    return get_commit_sha(repo_root, "HEAD")


def is_rebase_in_progress(repo_root: Path) -> bool:
    git_dir_result = run(["git", "rev-parse", "--git-dir"], repo_root)
    git_dir = repo_root / git_dir_result.stdout.strip()
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def squash_to_single_commit(repo_root: Path, base_ref: str, message: str) -> str:
    """Returns the existing HEAD SHA if there is nothing to commit."""
    run(["git", "reset", "--soft", base_ref], repo_root)
    staged = run(["git", "diff", "--cached", "--quiet"], repo_root, check=False)
    if staged.returncode == 0:
        return get_commit_sha(repo_root, "HEAD")
    run(["git", "commit", "-m", message], repo_root)
    return get_commit_sha(repo_root, "HEAD")
