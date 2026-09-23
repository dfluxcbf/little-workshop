"""Thin wrapper around the authenticated `gh` CLI — ported from
`little-runner`'s `github.py`. JSON in, JSON out, no tokens stored.

Only relevant when a project has the GitHub adapter attached
(docs/design.md's Adapter concept). Carries the one technique the
pillar spec calls out by name: actor lookup via the issue *timeline*,
not current state — polling only ever sees current labels/assignment,
never *who* applied them.
"""

from __future__ import annotations

import json
from typing import Any

from common.shell import CommandRunner, DEFAULT_RUNNER


class GitHubError(RuntimeError):
    pass


def issue_url(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/issues/{number}"


def parse_github_slug(repo_url: str | None) -> str | None:
    """`git@github.com:owner/name.git` or `https://github.com/owner/name`
    -> `owner/name`. None for anything else (no GitHub remote, or a
    remote on a different host)."""
    if not repo_url:
        return None
    match = None
    if "github.com:" in repo_url:
        match = repo_url.split("github.com:", 1)[1]
    elif "github.com/" in repo_url:
        match = repo_url.split("github.com/", 1)[1]
    else:
        return None
    return match.removesuffix(".git").strip("/") or None


class GitHubAdapter:
    def __init__(self, runner: CommandRunner = DEFAULT_RUNNER):
        self.runner = runner

    def api(self, endpoint: str, *, method: str = "GET", fields: dict[str, Any] | None = None) -> Any:
        argv = ["gh", "api", endpoint, "-X", method, "-H", "Accept: application/vnd.github+json"]
        for key, value in (fields or {}).items():
            argv += ["-f", f"{key}={value}"]
        result = self.runner.run(argv)
        if not result.ok:
            raise GitHubError(f"gh api {endpoint} failed: {(result.stderr or result.stdout).strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def issue(self, repo: str, number: int) -> dict[str, Any]:
        return self.api(f"repos/{repo}/issues/{number}")

    def list_open_issues(self, repo: str, *, label: str | None = None) -> list[dict[str, Any]]:
        endpoint = f"repos/{repo}/issues?state=open&per_page=100"
        if label:
            endpoint += f"&labels={label}"
        return self.api(endpoint)

    def list_comments(self, repo: str, number: int, *, since: str | None = None) -> list[dict[str, Any]]:
        endpoint = f"repos/{repo}/issues/{number}/comments?per_page=100"
        if since:
            endpoint += f"&since={since}"
        return self.api(endpoint)

    def list_issue_events(self, repo: str, number: int) -> list[dict[str, Any]]:
        """Timeline events (labeled/assigned/...), oldest first."""
        return self.api(f"repos/{repo}/issues/{number}/events")

    def comment(self, repo: str, number: int, body: str) -> None:
        self.api(f"repos/{repo}/issues/{number}/comments", method="POST", fields={"body": body})

    def close_issue(self, repo: str, number: int) -> None:
        result = self.runner.run(["gh", "issue", "close", str(number), "--repo", repo, "--reason", "completed"])
        if not result.ok:
            raise GitHubError(f"could not close issue #{number}: {(result.stderr or result.stdout).strip()}")

    def open_pr_for_branch(self, repo: str, branch: str) -> str | None:
        result = self.runner.run(["gh", "pr", "list", "--repo", repo, "--head", branch, "--json", "url", "--jq", ".[0].url"])
        return result.stdout.strip() or None if result.ok else None

    def create_pr(self, repo: str, *, head: str, base: str, title: str, body: str) -> str:
        result = self.runner.run([
            "gh", "pr", "create", "--repo", repo, "--head", head, "--base", base, "--title", title, "--body", body,
        ])
        if not result.ok:
            raise GitHubError(f"could not create PR: {(result.stderr or result.stdout).strip()}")
        return result.stdout.strip()

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        result = self.runner.run([
            "gh", "pr", "view", str(number), "--repo", repo, "--json", "headRefName,headRefOid,state,url",
        ])
        if not result.ok:
            raise GitHubError(f"could not read pull request #{number} of {repo}: {(result.stderr or result.stdout).strip()}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubError(f"unreadable pull request data for #{number}: {exc}") from exc

    def post_status(self, repo: str, sha: str, *, state: str, context: str, description: str = "") -> None:
        """Best-effort — REQ-000000035's own note applies here too: a
        status is reporting, never the job itself, so a failure to post
        it is never allowed to fail the run that produced it."""
        try:
            self.api(f"repos/{repo}/statuses/{sha}", method="POST", fields={
                "state": state, "context": context, "description": description[:139],
            })
        except GitHubError:
            pass


def actor_for_event(adapter: GitHubAdapter, repo: str, number: int, event_type: str, match: str) -> str | None:
    """Who actually performed a labeled/assigned event, from the issue's
    timeline — the issue list only shows current state, not who changed
    it. None (fall back to the issue's own author) if it can't be found."""
    try:
        events = adapter.list_issue_events(repo, number)
    except GitHubError:
        return None
    for event in reversed(events):  # most recent first
        if event.get("event") != event_type:
            continue
        if event_type == "labeled" and (event.get("label") or {}).get("name") != match:
            continue
        if event_type == "assigned" and (event.get("assignee") or {}).get("login") != match:
            continue
        return (event.get("actor") or {}).get("login")
    return None
