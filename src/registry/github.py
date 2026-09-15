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
