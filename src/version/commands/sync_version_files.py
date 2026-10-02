"""`version.sync_version_files` — computes and writes the correct VERSION
for the currently checked-out working branch, exactly matching this
project's own `branch-commit-dev-suffix`/`commit-bumps-version` policies'
formula (patch+1, -dev.1 for a branch's first commit; same base, -dev.N+1
after that), then propagates it into every other configured file via the
same `run_version_hooks` the three finish commands already call, and
stages everything touched.

Meant to be called from a pre-commit hook (`version.init` installs one,
baking in the project's own id) so a commit made through it satisfies both
policies by construction, closing the gap `bump_dev` in `stages.py` left
open -- ported from `little-versions` but never actually wired into
`create_prefixed_branch`/`start_integration_branch`, so a branch's first
commit was always left carrying its parent's unmodified VERSION until a
human or agent noticed and fixed it by hand (2026-09-22's finding).

A no-op on any branch little-workshop's own finish commands already
manage directly (integration, production, release/hotfix/support) --
those write VERSION themselves as part of finishing, and this command
running there too would just fight that.

`git commit --amend` replaces HEAD instead of adding a commit, so the commit
being made is numbered from HEAD's parent: counted from HEAD, an amended first
commit came out `-dev.2` and failed branch-commit-dev-suffix (2026-10-02).
Git tells a pre-commit hook nothing about an amend, so the sync reads its own
ancestry: the hook runs as a child of the `git commit` making the commit.

Only what the sync itself changed is staged -- VERSION and the files the
version hooks wrote -- never the rest of the working tree: staging every
modified and untracked file swept unrelated work into the commit."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from version.common import CommandResult, get_integration_branch, get_production_branch, run_version_hooks
from version.configstore import load_config
from version.gitops import current_branch, get_commit_sha, is_ancestor, run
from version.stages import GitflowVersion, bump_dev, bump_patch
from version.workflows import VERSION_FILE, read_version, write_version

_HOOK_DIR = ".githooks"
_HOOK_NAME = "pre-commit"
_HOOK_TEMPLATE = """#!/bin/sh
# Installed by `lws version init` -- keeps VERSION (and everything
# version_hooks propagates it into) correct on every working-branch commit,
# matching this project's own branch-commit-dev-suffix/commit-bumps-version
# policies' formula exactly. Never blocks the commit itself: those policies
# are the real enforcement backstop (still checked after the fact, still
# catch a bypassed/missing hook); this is best-effort proactive correctness,
# not a gate. See little-workshop's src/version/commands/sync_version_files.py.
# Only a working branch's VERSION is this hook's job: release/hotfix/support,
# dev and master are written by the finish commands themselves -- and a sync
# queued behind one of those (a Release Job holds the project's queue) would
# otherwise land later, on whatever branch is checked out by then.
case "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" in
    {working_branches}) ;;
    *) exit 0 ;;
esac
if ! command -v lws >/dev/null 2>&1; then
    exit 0
fi
lws version sync-version-files --project-id '{project_id}' || \\
    echo "pre-commit: lws version sync-version-files failed; VERSION may be out of date" >&2
exit 0
"""


def install_pre_commit_hook(repo_root: Path, project_id: str) -> Path:
    """Write `.githooks/pre-commit` (tracked, so it survives a clone) and
    point `core.hooksPath` at it. Idempotent: safe to call on every
    `version.init`, e.g. a re-run after the project's own id changes."""
    hook_dir = repo_root / _HOOK_DIR
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / _HOOK_NAME
    prefixes = load_config(repo_root)["prefixes"]
    working = "|".join(f"{prefixes[k]}/*" for k in ("feature", "improvement"))
    hook_path.write_text(_HOOK_TEMPLATE.format(project_id=project_id, working_branches=working), encoding="utf-8")
    hook_path.chmod(0o755)
    run(["git", "config", "core.hooksPath", _HOOK_DIR], repo_root)
    return hook_path


_COMMIT_MSG_HOOK_NAME = "commit-msg"
_COMMIT_MSG_TEMPLATE = """#!/bin/sh
# Installed by little-workshop (`lws version install-hooks`, also run by `lws version init`).
# Runs every Policy & Compliance policy that gates `commit` for this project against
# the commit being made -- its message, its staged files and VERSION -- in the policy
# sandbox, and refuses the commit when one fails, printing what to fix. If lws, the
# daemon or Docker is unavailable it warns and lets the commit through: the same
# policies still gate the branch finish. little-workshop's own finish and bookkeeping
# commits run with hooks off. See little-workshop's docs/pillars/policy_compliance.md.
if ! command -v lws >/dev/null 2>&1; then
    echo "commit-msg: lws not found; commit-time policies were not checked" >&2
    exit 0
fi
if ! lws policy evaluate --help >/dev/null 2>&1; then
    echo "commit-msg: this lws predates 'lws policy evaluate'; commit-time policies were not checked" >&2
    exit 0
fi
exec lws policy evaluate --project-id '{project_id}' --gate-point commit --commit-message-file "$1" --fail-open
"""


def install_commit_msg_hook(repo_root: Path, project_id: str) -> Path:
    """Write `.githooks/commit-msg`, which runs the project's commit-gated policies on
    every commit (policy gate point `commit`), and point `core.hooksPath` at it."""
    hook_dir = repo_root / _HOOK_DIR
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / _COMMIT_MSG_HOOK_NAME
    hook_path.write_text(_COMMIT_MSG_TEMPLATE.format(project_id=project_id), encoding="utf-8")
    hook_path.chmod(0o755)
    run(["git", "config", "core.hooksPath", _HOOK_DIR], repo_root)
    return hook_path


def install_git_hooks(repo_root: Path, project_id: str) -> list[Path]:
    """Every hook a registered project carries: the VERSION sync (pre-commit) and the
    commit-time policy check (commit-msg). Idempotent."""
    return [install_pre_commit_hook(repo_root, project_id), install_commit_msg_hook(repo_root, project_id)]


def _lvx_managed_prefixes(repo_root: Path) -> tuple[str, ...]:
    """release/, hotfix/, support/ -- branches whose VERSION little-workshop's
    own finish commands already write directly, feature/improvement excluded
    since those are exactly this command's own audience."""
    cfg = load_config(repo_root)
    return tuple(cfg["prefixes"][k] for k in ("release", "hotfix", "support"))


def _process(pid: int) -> tuple[int, list[str]] | None:
    """(parent pid, argv) of process `pid`, or None when it cannot be read."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
        with open(f"/proc/{pid}/stat", "rb") as fh:
            stat = fh.read().decode(errors="replace")
        return int(stat[stat.rindex(")") + 2:].split()[1]), argv
    except (OSError, ValueError, IndexError):
        pass
    try:  # no /proc (macOS, some Windows shells): ask ps
        listed = subprocess.run(["ps", "-o", "ppid=", "-o", "args=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5)
        ppid, _, args = listed.stdout.strip().partition(" ")
        return (int(ppid), args.split()) if listed.returncode == 0 and args else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def amending_commit(start_pid: int | None = None, max_depth: int = 8) -> bool:
    """Whether this process runs under `git commit --amend`: the nearest git process among its
    ancestors (the hook's caller) was started with `--amend`."""
    pid = os.getppid() if start_pid is None else start_pid
    for _ in range(max_depth):
        if pid <= 1:
            return False
        found = _process(pid)
        if found is None:
            return False
        parent, argv = found
        if argv and os.path.basename(argv[0]).removesuffix(".exe") == "git":
            return "--amend" in argv
        pid = parent
    return False


def _version_at(repo_root: Path, commit: str) -> GitflowVersion | None:
    shown = run(["git", "show", f"{commit}:{VERSION_FILE}"], repo_root, check=False)
    try:
        return GitflowVersion.parse(shown.stdout.strip()) if shown.returncode == 0 else None
    except ValueError:
        return None


def _unstaged(repo_root: Path) -> dict[str, str | None]:
    """Every file with unstaged changes, or untracked and not ignored -> a digest of its content."""
    modified = run(["git", "diff", "--name-only", "-z"], repo_root, check=False).stdout.split("\0")
    untracked = run(["git", "ls-files", "--others", "--exclude-standard", "-z"], repo_root, check=False).stdout.split("\0")
    digests: dict[str, str | None] = {}
    for rel in filter(None, modified + untracked):
        try:
            digests[rel] = hashlib.sha256((repo_root / rel).read_bytes()).hexdigest()
        except OSError:
            digests[rel] = None
    return digests


def cmd_sync_version_files(repo_root: Path, *, amend: bool | None = None) -> CommandResult:
    """`amend`: whether the commit being made replaces HEAD; None reads it from the process
    ancestry (`amending_commit`)."""
    branch = current_branch(repo_root)
    integ = get_integration_branch(repo_root)
    prod = get_production_branch(repo_root)
    managed_prefixes = _lvx_managed_prefixes(repo_root)

    if branch in (integ, prod) or branch.startswith(tuple(f"{p}/" for p in managed_prefixes)):
        return CommandResult(
            success=True,
            message=f"{branch!r} is managed directly by finish commands; nothing to sync.",
            data={"skipped": True},
        )

    current = read_version(repo_root)
    head_sha = get_commit_sha(repo_root)
    if amend is None:
        amend = amending_commit()
    # An amend replaces HEAD, so the commit being made is numbered from HEAD's parent -- unless
    # HEAD is still the integration branch's (nothing of this branch's own to replace).
    parent = ""
    if amend and not is_ancestor(repo_root, head_sha, integ):
        parent = run(["git", "rev-parse", "--verify", "-q", "HEAD^"], repo_root, check=False).stdout.strip()
    if parent:
        base = _version_at(repo_root, parent) or current
        first = is_ancestor(repo_root, parent, integ)
        new_version = bump_dev(bump_patch(base)) if first else bump_dev(base)
    # HEAD unchanged from the integration branch's own tip means no commit has
    # been made on this working branch yet -- the one about to be created is
    # the branch's first, and gets next-patch, -dev.1.
    elif is_ancestor(repo_root, head_sha, integ):
        new_version = bump_dev(bump_patch(current))
    else:
        new_version = bump_dev(current)

    before = _unstaged(repo_root)
    write_version(repo_root, new_version)
    hook_errors = run_version_hooks(repo_root, str(new_version))

    # What the sync changed: VERSION, and every file the version hooks modified or created
    # (a hook is free to write a new, untracked file). A file that already had unstaged
    # changes before keeps them out of the commit only if the hooks left it alone.
    after = _unstaged(repo_root)
    touched = sorted({VERSION_FILE} | {rel for rel, digest in after.items() if rel not in before or before[rel] != digest})
    run(["git", "add", "--", *touched], repo_root)
    mixed = [rel for rel in touched if rel in before and rel != VERSION_FILE]

    message = f"Synced VERSION to {new_version} on {branch!r}{' (amending HEAD)' if parent else ''}."
    if mixed:
        message += ("\nWarning: the version hooks changed files that already had unstaged changes, now staged with "
                    "them: " + ", ".join(mixed))
    if hook_errors:
        message += "\nWarning: some version hooks failed:\n" + "\n".join(hook_errors)
    return CommandResult(
        success=True,
        message=message,
        data={"version": str(new_version), "hook_errors": hook_errors, "files_touched": touched, "amend": bool(parent)},
    )
