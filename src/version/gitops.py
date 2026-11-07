"""Git subprocess wrapper for Gitflow-critical operations — ported
near-verbatim from `little-versions`' `git/ops.py`. Refuses on conflict,
never auto-resolves (docs/milestones.md M3) -- with one narrow exception: a
rebase conflict hunk whose two sides differ only by each commit's own
VERSION string (`resolve_version_only_conflicts`). Every working branch's
first commit bumps VERSION, so any two branches cut from the same dev tip
always conflicted there at finish, and finish rewrites VERSION anyway."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from common.windows import GIT_EXE, git_command, git_path


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


# The pillar's own git calls never run the repository's hooks: they are for
# commits an agent or a person makes, and the pillar's own commits (a version
# bump, a squash of commits already checked) gain nothing from them.
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


# git.exe gets no WSL environment (GIT_EDITOR=true below does not cross), so it is told the editor itself.
_WINDOWS_NO_EDITOR = ("-c", "core.editor=true")


def _git_args(args: list[str], cwd: Path | None = None) -> list[str]:
    """`args` with hooks off, and `git` the checkout's own (`git.exe` for a Windows-hosted one, REQ-000000510)."""
    if not args or args[0] != "git":
        return list(args)
    git = git_command(cwd) if cwd is not None else ["git"]
    extra = _WINDOWS_NO_EDITOR if git[0] == GIT_EXE else ()
    return [*git, *_NO_HOOKS, *extra, *args[1:]]


def run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(_git_args(args, cwd), cwd=str(cwd), check=False, capture_output=True, text=True)
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


def dirty_paths(repo_root: Path) -> list[str]:
    """The uncommitted paths outside `.lvx/` (see `is_dirty`)."""
    result = run(["git", "status", "--porcelain"], repo_root)
    paths = [line[3:].strip().split(" -> ")[-1] for line in result.stdout.splitlines()]
    return [path for path in paths if not path.startswith(".lvx/")]


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


def merge_squash(repo_root: Path, branch: str, message: str) -> None:
    """Land `branch` on the current branch as one commit, with no merge commit. Raises MergeConflictError
    if conflicts occur (never auto-resolves)."""
    result = run(["git", "merge", "--squash", branch], repo_root, check=False)
    if result.returncode != 0:
        status = run(["git", "status", "--porcelain"], repo_root, check=False)
        if "UU" in status.stdout or "AA" in status.stdout or "DD" in status.stdout:
            raise MergeConflictError(f"Merge conflict when squash-merging {branch!r}. Resolve conflicts manually, then commit.")
        raise GitError(f"Merge failed: {result.stderr.strip() or result.stdout.strip()}")
    if run(["git", "diff", "--cached", "--quiet"], repo_root, check=False).returncode != 0:
        run(["git", "commit", "-m", message], repo_root)


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


# Cited, instead of requirement ids, by a commit that affects no requirement: never alongside a real
# REQ-nnnnnnnnn id.
NO_REQUIREMENT = "REQ-NONE"


def no_requirement(message: str) -> str:
    """`message` with the REQ-NONE trailer: for little-workshop's own
    bookkeeping commits (version bumps, finish state, changelogs), which a
    version hook may still make touch src/ (little-home's src/package.json)."""
    return f"{message}\n\n{NO_REQUIREMENT}"


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


def is_merged(repo_root: Path, ref: str, target: str) -> bool:
    """Whether `target` already holds everything `ref` changed: `ref` is in its history, or merging `ref`
    into it would change nothing, as after a squash merge, whose commits never become ancestors."""
    if is_ancestor(repo_root, ref, target):
        return True
    merged = run(["git", "merge-tree", "--write-tree", target, ref], repo_root, check=False)
    if merged.returncode != 0 or not merged.stdout.strip():
        return False
    tree = run(["git", "rev-parse", f"{target}^{{tree}}"], repo_root, check=False).stdout.strip()
    return merged.stdout.splitlines()[0].strip() == tree


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


_FINISH_STATE_PATH = ".lvx/state.json"  # where the version state lived before the state directory
_CONFLICT_CODES = ("UU", "AA", "DD", "AU", "UA", "DU", "UD")


def _conflicted_paths(repo_root: Path) -> list[str]:
    status = run(["git", "status", "--porcelain"], repo_root, check=False)
    return [line[3:] for line in status.stdout.splitlines() if line[:2] in _CONFLICT_CODES]


conflicted_paths = _conflicted_paths


def _version_at(repo_root: Path, rev: str) -> str | None:
    shown = run(["git", "show", f"{rev}:VERSION"], repo_root, check=False)
    return (shown.stdout.strip() or None) if shown.returncode == 0 else None


def _stage(repo_root: Path, n: int, path: str) -> str | None:
    shown = run(["git", "show", f":{n}:{path}"], repo_root, check=False)
    return shown.stdout if shown.returncode == 0 else None


def _resolve_hunks(merged: str, base_v: str, ours_v: str, theirs_v: str) -> str | None:
    """`git merge-file --diff3` output with every conflict hunk resolved, or
    None if any hunk differs by more than the version string. Only the
    hunks are touched: a line outside them is never rewritten."""
    out: list[str] = []
    lines = merged.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        if not lines[i].startswith("<<<<<<< "):
            out.append(lines[i])
            i += 1
            continue
        ours, base, theirs, part = [], [], [], "ours"
        i += 1
        while not lines[i].startswith(">>>>>>> "):
            line = lines[i]
            if line.startswith("||||||| "):
                part = "base"
            elif line.startswith("======="):
                part = "theirs"
            else:
                {"ours": ours, "base": base, "theirs": theirs}[part].append(line)
            i += 1
        i += 1
        ours_s = "".join(ours)
        base_s = "".join(base).replace(base_v, ours_v)
        theirs_s = "".join(theirs).replace(theirs_v, ours_v)
        if theirs_s in (ours_s, base_s):
            out.append(ours_s)  # the branch changed nothing here but its version
        elif ours_s == base_s:
            out.append(theirs_s)  # upstream changed nothing here but its version
        else:
            return None
    return "".join(out)


def resolve_version_only_conflicts(repo_root: Path) -> bool:
    """During a rebase, resolve every conflicted file whose conflicting
    hunks differ only by the two commits' own VERSION strings (VERSION
    itself, and whatever version_hooks write it into), taking the upstream
    side's version; `.lvx/state.json` (the state's old place) takes the
    upstream's removal, else the replayed commit's side.
    True if nothing is left conflicted; files with a real
    conflict are left exactly as git left them."""
    conflicted = _conflicted_paths(repo_root)
    if not conflicted:
        return True
    ours_v = _version_at(repo_root, "HEAD")
    theirs_v = _version_at(repo_root, "REBASE_HEAD")
    base_v = _version_at(repo_root, "REBASE_HEAD^")
    if not (ours_v and theirs_v and base_v):
        return False
    remaining = False
    for path in conflicted:
        if path == _FINISH_STATE_PATH:
            # The state's old place in the repository, written by nothing but the pillar: an
            # upstream that no longer tracks it wins; otherwise the replayed commit's side.
            if _stage(repo_root, 2, path) is None:
                run(["git", "rm", "-q", "--cached", "--", path], repo_root)
            else:
                run(["git", "checkout", "--theirs", "--", path], repo_root)
                run(["git", "add", "--", path], repo_root)
            continue
        stages = [_stage(repo_root, n, path) for n in (1, 2, 3)]
        if any(s is None for s in stages):
            remaining = True  # add/delete conflicts are never version-only
            continue
        base, ours, theirs = stages
        # In the checkout for git.exe, which reads Windows paths only (a WSL /tmp has none it can rely on).
        tmp_parent = str(repo_root) if git_command(repo_root)[0] == GIT_EXE else None
        with tempfile.TemporaryDirectory(dir=tmp_parent, prefix=".lws-merge-") as tmp_dir:
            files = [Path(tmp_dir) / name for name in ("ours", "base", "theirs")]
            for f, content in zip(files, (ours, base, theirs)):
                f.write_text(content, encoding="utf-8")
            merged = run(["git", "merge-file", "-p", "--diff3", *(git_path(f, repo_root) for f in files)], repo_root, check=False).stdout
        resolved = _resolve_hunks(merged, base_v, ours_v, theirs_v)
        if resolved is None:
            remaining = True
            continue
        (repo_root / path).write_text(resolved, encoding="utf-8")
        run(["git", "add", "--", path], repo_root)
    return not remaining


# Settles the conflicted generated files among the paths given and returns those it settled
# (`version.generated.regenerate_conflicted`); a finish passes it to the rebase.
Regenerate = Callable[[Path, list[str]], list[str]]


def resolve_known_conflicts(repo_root: Path, regenerate: Regenerate | None = None) -> bool:
    """Settle what the code can: generated files written again from the generator (`regenerate`), then
    version-only hunks. True if nothing is left conflicted."""
    if regenerate is not None:
        conflicted = _conflicted_paths(repo_root)
        if conflicted:
            regenerate(repo_root, conflicted)
    return resolve_version_only_conflicts(repo_root)


def _continue_past_version_conflicts(repo_root: Path, regenerate: Regenerate | None = None) -> bool:
    """Keep the rebase going while every stop is version-only or in generated
    files. False when a real conflict (or any other stop) is left for a person."""
    full_env = {**os.environ, "GIT_EDITOR": "true"}
    for _ in range(10_000):  # one pass per replayed commit, at most
        if not is_rebase_in_progress(repo_root):
            return True
        if not _conflicted_paths(repo_root) and run(["git", "diff", "--cached", "--quiet"], repo_root, check=False).returncode == 0:
            # A commit that changed nothing but its version is empty once
            # resolved: drop it, as `git rebase` itself drops empty picks.
            step = ["git", "rebase", "--skip"]
        elif resolve_known_conflicts(repo_root, regenerate):
            step = ["git", "rebase", "--continue"]
        else:
            return False
        result = subprocess.run(_git_args(step, repo_root), cwd=str(repo_root), check=False, capture_output=True, text=True, env=full_env)
        if result.returncode != 0 and not _conflicted_paths(repo_root) and is_rebase_in_progress(repo_root):
            if run(["git", "diff", "--cached", "--quiet"], repo_root, check=False).returncode != 0:
                return False  # stopped for a reason that is not a conflict
    return False


def rebase_onto(repo_root: Path, base_branch: str, regenerate: Regenerate | None = None) -> None:
    """Raises RebaseConflictError if conflicts occur that are more than
    each commit's own version string or generated files (`regenerate`)."""
    result = run(["git", "rebase", base_branch], repo_root, check=False)
    if result.returncode != 0 and is_rebase_in_progress(repo_root) and _continue_past_version_conflicts(repo_root, regenerate):
        return
    if result.returncode != 0:
        status = run(["git", "status", "--porcelain"], repo_root, check=False)
        if any(line[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for line in status.stdout.splitlines()):
            raise RebaseConflictError(
                f"Rebase conflict when rebasing onto {base_branch!r}. "
                "Resolve conflicts, stage the files, then run: version feature finish --continue\n"
                "To cancel, run: version feature finish --abort"
            )
        raise GitError(f"Rebase failed: {result.stderr.strip() or result.stdout.strip()}")


def rebase_continue(repo_root: Path, regenerate: Regenerate | None = None) -> None:
    full_env = {**os.environ, "GIT_EDITOR": "true"}
    result = subprocess.run(
        _git_args(["git", "rebase", "--continue"], repo_root), cwd=str(repo_root), check=False, capture_output=True, text=True, env=full_env
    )
    if result.returncode != 0 and is_rebase_in_progress(repo_root) and _continue_past_version_conflicts(repo_root, regenerate):
        return
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


REQUIREMENT_ID = re.compile(r"\bREQ-\d{9}\b")


def branch_requirements(repo_root: Path, base_ref: str) -> list[str]:
    """The requirement ids a branch names since `base_ref`, in order of first mention: those its commit messages cite
    and those on the lines its diff adds (the tests' `# --- REQ-nnnnnnnnn: ... ---` markers) (REQ-000000499)."""
    messages = run(["git", "log", "--format=%B", f"{base_ref}..HEAD"], repo_root, check=False).stdout
    diff = run(["git", "diff", "--unified=0", base_ref, "HEAD"], repo_root, check=False).stdout
    added = "\n".join(line for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return list(dict.fromkeys(REQUIREMENT_ID.findall(messages) + REQUIREMENT_ID.findall(added)))


def with_requirements(message: str, keys: list[str]) -> str:
    """`message` with a `Requirements: REQ-…, REQ-…` line for the ids it does not name yet."""
    missing = [k for k in keys if k not in message]
    return f"{message.rstrip()}\n\nRequirements: {', '.join(missing)}" if missing else message


def squash_to_single_commit(repo_root: Path, base_ref: str, message: str) -> str:
    """Returns the existing HEAD SHA if there is nothing to commit. The squash commit's body lists the requirement
    ids the branch named (`branch_requirements`): the squash drops the branch's own commits and their citations."""
    message = with_requirements(message, branch_requirements(repo_root, base_ref))
    run(["git", "reset", "--soft", base_ref], repo_root)
    staged = run(["git", "diff", "--cached", "--quiet"], repo_root, check=False)
    if staged.returncode == 0:
        return get_commit_sha(repo_root, "HEAD")
    run(["git", "commit", "-m", message], repo_root)
    return get_commit_sha(repo_root, "HEAD")
