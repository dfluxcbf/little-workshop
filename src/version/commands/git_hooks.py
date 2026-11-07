"""The git hooks little-workshop installs in a project (`lws version install-hooks`, also run by
`version.init` with a project id): `.githooks/commit-msg`, which refuses a commit that adds a
secret (a local scan, `lws policy scan-secrets`), with `core.hooksPath` pointed at `.githooks`.

Working branches leave VERSION alone: the finish writes it once. A `.githooks/pre-commit` that
little-workshop installed to sync VERSION on every working-branch commit is removed."""

from __future__ import annotations

from pathlib import Path

from version.gitops import run

_HOOK_DIR = ".githooks"
RETIRED_PRE_COMMIT = f"{_HOOK_DIR}/pre-commit"
RETIRED_PRE_COMMIT_MARK = "lws version sync-version-files"
_COMMIT_MSG_HOOK_NAME = "commit-msg"
COMMIT_MSG_HOOK = """#!/bin/sh
# Installed by little-workshop (`lws version install-hooks`, also run by `lws version init`).
# Refuses a commit that adds a secret -- a private key, a provider token or API key, an .env
# file -- with a local scan of its staged changes: no daemon, no Docker. The other policies
# gate the branch finish. In WSL or Linux without lws (little-workshop not installed here) it
# warns and lets the commit through: no-committed-secrets still gates the finish. On Windows
# (Git for Windows) without the lws shim it refuses the commit: the shim is how these checks
# reach WSL there. little-workshop's own finish commits run with hooks off. See
# little-workshop's docs/pillars/policy_compliance.md.
if ! command -v lws >/dev/null 2>&1; then
    case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*)
            echo "commit-msg: lws not found on Windows, so the commit cannot be scanned for secrets and is refused." >&2
            echo "  Install the shim once from WSL: lws windows install-shim  (then open a new terminal)" >&2
            exit 1 ;;
    esac
    echo "commit-msg: lws not found; the commit was not scanned for secrets" >&2
    exit 0
fi
if ! lws policy scan-secrets --help >/dev/null 2>&1; then
    echo "commit-msg: this lws predates 'lws policy scan-secrets'; the commit was not scanned for secrets" >&2
    exit 0
fi
exec lws policy scan-secrets
"""


# Each hook this module writes, by path: what a rebase stopped on one of them is settled with.
GENERATED_HOOKS = {f"{_HOOK_DIR}/{_COMMIT_MSG_HOOK_NAME}": COMMIT_MSG_HOOK}


def install_commit_msg_hook(repo_root: Path) -> Path:
    """Write `.githooks/commit-msg` (tracked, so it survives a clone) and point `core.hooksPath` at it."""
    hook_dir = repo_root / _HOOK_DIR
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hook_dir / _COMMIT_MSG_HOOK_NAME
    hook_path.write_text(COMMIT_MSG_HOOK, encoding="utf-8")
    hook_path.chmod(0o755)
    run(["git", "config", "core.hooksPath", _HOOK_DIR], repo_root)
    return hook_path


def remove_retired_pre_commit(repo_root: Path) -> bool:
    """Remove the VERSION-sync pre-commit hook little-workshop once installed; a project's own pre-commit stays."""
    hook_path = repo_root / RETIRED_PRE_COMMIT
    try:
        if RETIRED_PRE_COMMIT_MARK not in hook_path.read_text(encoding="utf-8"):
            return False
    except OSError:
        return False
    hook_path.unlink()
    return True


def install_git_hooks(repo_root: Path, project_id: str) -> list[Path]:
    """Every hook a registered project carries. Idempotent. `project_id` names the project the hooks
    belong to; the commit-msg hook needs nothing of it."""
    del project_id
    remove_retired_pre_commit(repo_root)
    return [install_commit_msg_hook(repo_root)]
