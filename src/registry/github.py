"""GitHub repository creation — ported near-verbatim from
`bootstrap-little-project`'s `github.py`. Two backends, tried in this
order by `bootstrap.py`: the authenticated `gh` CLI, then a minimal,
dependency-free (stdlib `urllib`) REST client for a token passed
explicitly.

Never exercised against the real GitHub API in this project's own test
suite — every test mocks this module's boundary (subprocess/urllib),
matching the established sandbox constraint that a real token can't be
used here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request

API_URL = "https://api.github.com/user/repos"


class GitHubError(RuntimeError):
    pass


def gh_available() -> bool:
    """True when the ``gh`` CLI is on PATH and has an active login."""
    if shutil.which("gh") is None:
        return False
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, check=False)
    return result.returncode == 0


def create_repository_with_gh(
    name: str, owner: str, *, private: bool = True, description: str = ""
) -> dict:
    full_name = f"{owner}/{name}"
    args = ["gh", "repo", "create", full_name, "--private" if private else "--public"]
    if description:
        args += ["--description", description]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise GitHubError(
            f"gh repo create failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return {"full_name": full_name, "html_url": result.stdout.strip() or f"https://github.com/{full_name}"}


# The seven real labels `little-runner init` ensures on a project's
# GitHub repo (docs/requirements.md's M16 follow-up, closing
# `bootstrap-little-project`'s parity gap) — copied here as plain data,
# the same "mirror as literals, never import" rule every other
# cross-project reference in this codebase already follows, since
# little-workshop never shares a Python import with a tool it's
# absorbing.
TRIGGER_AND_OUTCOME_LABELS = (
    ("claude", "7057ff", "Hand this issue to Claude on the WSL host (little-runner)"),
    ("claude:done", "0e8a16", "little-runner: Claude finished; see the PR/comment"),
    ("claude:failed", "b60205", "little-runner: the Claude flow failed; see the comment and log"),
    ("claude:awaiting-finish", "fbca04", "little-runner: the branch is ready and waiting for /finish or /skip"),
    ("claude:finished", "1d76db", "little-runner: the branch was finished with little-versions"),
    ("claude:skipped", "d4c5f9", "little-runner: the user skipped finishing the branch"),
    ("claude:queued", "c5def5", "little-runner: waiting for this repository's current issue to finish"),
)
INTENT_LABELS = (
    ("docs", "0075ca", "little-runner intent: update documentation/requirements"),
    ("milestone", "5319e7", "little-runner intent: execute a milestone from docs/milestones.md"),
    ("bug", "d73a4a", "little-runner intent: fix a bug (GitHub default label)"),
    ("task", "cfd3d7", "little-runner intent: generic task"),
)
BOOTSTRAP_LABELS = TRIGGER_AND_OUTCOME_LABELS + INTENT_LABELS


def ensure_label_with_gh(repo: str, name: str, color: str, description: str) -> None:
    result = subprocess.run(
        ["gh", "label", "create", name, "--repo", repo, "--color", color, "--description", description, "--force"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise GitHubError(f"gh label create failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}")


def ensure_label(repo: str, token: str, name: str, color: str, description: str) -> None:
    """Idempotent: create the label, or update it if it already exists —
    the GitHub REST API has no single upsert endpoint for labels."""
    payload = json.dumps({"name": name, "color": color, "description": description}).encode("utf-8")
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    create_request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/labels", data=payload, method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(create_request):
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 422:  # 422: label already exists — fall through to update
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubError(f"GitHub API returned {exc.code}: {body}") from exc

    update_request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/labels/{name}", data=payload, method="PATCH", headers=headers,
    )
    try:
        with urllib.request.urlopen(update_request):
            return
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise GitHubError(f"GitHub API returned {exc.code}: {body}") from exc


def create_repository(name: str, token: str, *, private: bool = True, description: str = "") -> dict:
    payload = json.dumps(
        {"name": name, "private": private, "description": description, "auto_init": False}
    ).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise GitHubError(f"GitHub API returned {exc.code}: {body}") from exc
