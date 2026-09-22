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
running there too would just fight that."""

from __future__ import annotations

from pathlib import Path

from version.common import CommandResult, get_integration_branch, get_production_branch, run_version_hooks
from version.configstore import load_config
from version.gitops import current_branch, get_commit_sha, is_ancestor, run
from version.stages import bump_dev, bump_patch
from version.workflows import read_version, write_version

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
    hook_path.write_text(_HOOK_TEMPLATE.format(project_id=project_id), encoding="utf-8")
    hook_path.chmod(0o755)
    run(["git", "config", "core.hooksPath", _HOOK_DIR], repo_root)
    return hook_path


def _lvx_managed_prefixes(repo_root: Path) -> tuple[str, ...]:
    """release/, hotfix/, support/ -- branches whose VERSION little-workshop's
    own finish commands already write directly, feature/improvement excluded
    since those are exactly this command's own audience."""
    cfg = load_config(repo_root)
    return tuple(cfg["prefixes"][k] for k in ("release", "hotfix", "support"))


def cmd_sync_version_files(repo_root: Path) -> CommandResult:
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
    # HEAD unchanged from the integration branch's own tip means no commit has
    # been made on this working branch yet -- the one about to be created is
    # the branch's first, and gets next-patch, -dev.1.
    if is_ancestor(repo_root, head_sha, integ):
        new_version = bump_dev(bump_patch(current))
    else:
        new_version = bump_dev(current)

    write_version(repo_root, new_version)
    hook_errors = run_version_hooks(repo_root, str(new_version))

    # `git diff --name-only` alone misses a hook-created *new* file (it's
    # untracked, not modified) -- a version_hooks script is free to write one
    # (2026-09-22's own test caught this), so both are needed.
    modified = run(["git", "diff", "--name-only"], repo_root, check=False).stdout.splitlines()
    untracked = run(["git", "ls-files", "--others", "--exclude-standard"], repo_root, check=False).stdout.splitlines()
    touched = sorted({p.strip() for p in modified + untracked if p.strip()})
    if touched:
        run(["git", "add", "--", *touched], repo_root)

    message = f"Synced VERSION to {new_version} on {branch!r}."
    if hook_errors:
        message += "\nWarning: some version hooks failed:\n" + "\n".join(hook_errors)
    return CommandResult(
        success=True,
        message=message,
        data={"version": str(new_version), "hook_errors": hook_errors, "files_touched": touched},
    )
