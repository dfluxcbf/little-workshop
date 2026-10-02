"""The commit a `commit-msg` hook is about to let through, read the way git itself
will build it: the message after git's own cleanup, the paths it changes and its
staged VERSION. Read here, in the hook's own process, because git may keep that
commit's index in a temporary file (`git commit -a`, `git commit <paths>`) that
only the hook's environment (`GIT_INDEX_FILE`) points at — the Policy &
Compliance sandbox could never see it from the checkout alone."""

from __future__ import annotations

import subprocess
from pathlib import Path

_SCISSORS = "------------------------ >8 ------------------------"


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)


def comment_char(repo_root: Path) -> str:
    configured = _git(repo_root, "config", "--get", "core.commentChar").stdout.strip()
    # "auto" picks a character absent from the message; "#" is still the usual one.
    return configured if configured and configured != "auto" else "#"


def clean_message(raw: str, comment: str = "#") -> str:
    """The message as `git commit`'s default cleanup leaves it: comment lines and
    everything below a `--verbose` scissors line removed, outer blank lines trimmed."""
    kept: list[str] = []
    for line in raw.splitlines():
        if line.startswith(comment) and _SCISSORS in line:
            break
        if line.startswith(comment):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def pending_commit(repo_root: Path, message_file: Path) -> dict:
    """`{"message", "files", "version"}` for Policy & Compliance's `policy.evaluate`."""
    message = clean_message(message_file.read_text(encoding="utf-8", errors="replace"), comment_char(repo_root))
    listed = _git(repo_root, "diff", "--cached", "--name-only", "-z")
    files = [f for f in listed.stdout.split("\0") if f] if listed.returncode == 0 else []
    staged_version = _git(repo_root, "show", ":VERSION")
    version = staged_version.stdout if staged_version.returncode == 0 else None
    return {"message": message, "files": files, "version": version}


def checkout_root(cwd: Path | None = None) -> Path | None:
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=str(cwd) if cwd else None,
    )
    return Path(toplevel.stdout.strip()) if toplevel.returncode == 0 and toplevel.stdout.strip() else None
