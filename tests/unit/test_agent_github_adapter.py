"""agent.github_adapter — the GitHub CLI wrapper and its actor-lookup
technique. A real GitHub API mutation is exactly the kind of expensive,
non-deterministic external this project always fakes: every test here
injects a fake CommandRunner instead of shelling out to `gh` for real."""

import json

import pytest

from agent.github_adapter import (
    GitHubAdapter,
    GitHubError,
    actor_for_event,
    issue_url,
    parse_github_slug,
)
from common.shell import CommandResult


class FakeRunner:
    def __init__(self, *, result: CommandResult | None = None, by_argv0=None):
        self.result = result
        self.by_argv0 = by_argv0 or {}
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[0] in self.by_argv0:
            return self.by_argv0[argv[0]]
        return self.result if self.result is not None else CommandResult(0, "{}", "")


def ok(payload) -> CommandResult:
    return CommandResult(0, json.dumps(payload), "")


def failed(stderr="boom") -> CommandResult:
    return CommandResult(1, "", stderr)


# -- issue_url / parse_github_slug -----------------------------------------


def test_issue_url_format():
    assert issue_url("owner/name", 42) == "https://github.com/owner/name/issues/42"


@pytest.mark.parametrize(
    "repo_url,expected",
    [
        ("git@github.com:owner/name.git", "owner/name"),
        ("https://github.com/owner/name", "owner/name"),
        ("https://github.com/owner/name.git", "owner/name"),
        ("https://github.com/owner/name/", "owner/name"),
        (None, None),
        ("", None),
        ("git@gitlab.com:owner/name.git", None),
        ("/local/path/to/repo", None),
    ],
)
def test_parse_github_slug(repo_url, expected):
    assert parse_github_slug(repo_url) == expected


# -- GitHubAdapter.api -------------------------------------------------------


def test_api_builds_get_argv_with_headers():
    runner = FakeRunner(result=ok({"ok": True}))
    adapter = GitHubAdapter(runner=runner)
    result = adapter.api("repos/o/n/issues/1")
    assert result == {"ok": True}
    argv = runner.calls[0]
    assert argv[:4] == ["gh", "api", "repos/o/n/issues/1", "-X"]
    assert "GET" in argv
    assert "-H" in argv and "Accept: application/vnd.github+json" in argv


def test_api_with_fields_appends_dash_f_pairs():
    runner = FakeRunner(result=ok({}))
    adapter = GitHubAdapter(runner=runner)
    adapter.api("repos/o/n/issues/1/comments", method="POST", fields={"body": "hello"})
    argv = runner.calls[0]
    assert "-f" in argv
    assert "body=hello" in argv
    assert "POST" in argv


def test_api_empty_stdout_returns_empty_dict():
    runner = FakeRunner(result=CommandResult(0, "   ", ""))
    adapter = GitHubAdapter(runner=runner)
    assert adapter.api("repos/o/n") == {}


def test_api_failure_raises_github_error_with_stderr():
    runner = FakeRunner(result=failed(stderr="not found"))
    adapter = GitHubAdapter(runner=runner)
    with pytest.raises(GitHubError, match="not found"):
        adapter.api("repos/o/n")


def test_api_failure_falls_back_to_stdout_when_stderr_empty():
    runner = FakeRunner(result=CommandResult(1, "some stdout error", ""))
    adapter = GitHubAdapter(runner=runner)
    with pytest.raises(GitHubError, match="some stdout error"):
        adapter.api("repos/o/n")


# -- read methods -------------------------------------------------------


def test_issue_calls_correct_endpoint():
    runner = FakeRunner(result=ok({"number": 5}))
    adapter = GitHubAdapter(runner=runner)
    assert adapter.issue("o/n", 5) == {"number": 5}
    assert runner.calls[0][2] == "repos/o/n/issues/5"


def test_list_open_issues_without_label():
    runner = FakeRunner(result=ok([{"number": 1}]))
    adapter = GitHubAdapter(runner=runner)
    result = adapter.list_open_issues("o/n")
    assert result == [{"number": 1}]
    assert runner.calls[0][2] == "repos/o/n/issues?state=open&per_page=100"


def test_list_open_issues_with_label_appends_query_param():
    runner = FakeRunner(result=ok([]))
    adapter = GitHubAdapter(runner=runner)
    adapter.list_open_issues("o/n", label="claude-task")
    assert runner.calls[0][2].endswith("&labels=claude-task")


def test_list_comments_without_since():
    runner = FakeRunner(result=ok([]))
    adapter = GitHubAdapter(runner=runner)
    adapter.list_comments("o/n", 3)
    assert runner.calls[0][2] == "repos/o/n/issues/3/comments?per_page=100"


def test_list_comments_with_since_appends_query_param():
    runner = FakeRunner(result=ok([]))
    adapter = GitHubAdapter(runner=runner)
    adapter.list_comments("o/n", 3, since="2026-01-01T00:00:00Z")
    assert runner.calls[0][2].endswith("&since=2026-01-01T00:00:00Z")


def test_list_issue_events_endpoint():
    runner = FakeRunner(result=ok([]))
    adapter = GitHubAdapter(runner=runner)
    adapter.list_issue_events("o/n", 7)
    assert runner.calls[0][2] == "repos/o/n/issues/7/events"


# -- write methods -------------------------------------------------------


def test_comment_posts_body_field():
    runner = FakeRunner(result=ok({}))
    adapter = GitHubAdapter(runner=runner)
    adapter.comment("o/n", 1, "hello there")
    argv = runner.calls[0]
    assert "POST" in argv
    assert "body=hello there" in argv


def test_close_issue_success():
    runner = FakeRunner(result=CommandResult(0, "", ""))
    adapter = GitHubAdapter(runner=runner)
    adapter.close_issue("o/n", 9)
    argv = runner.calls[0]
    assert argv == ["gh", "issue", "close", "9", "--repo", "o/n", "--reason", "completed"]


def test_close_issue_failure_raises_github_error():
    runner = FakeRunner(result=failed(stderr="cannot close"))
    adapter = GitHubAdapter(runner=runner)
    with pytest.raises(GitHubError, match="cannot close"):
        adapter.close_issue("o/n", 9)


def test_open_pr_for_branch_returns_url_when_found():
    runner = FakeRunner(result=CommandResult(0, "https://github.com/o/n/pull/3\n", ""))
    adapter = GitHubAdapter(runner=runner)
    assert adapter.open_pr_for_branch("o/n", "feature/x") == "https://github.com/o/n/pull/3"


def test_open_pr_for_branch_returns_none_when_no_pr_exists():
    runner = FakeRunner(result=CommandResult(0, "", ""))
    adapter = GitHubAdapter(runner=runner)
    assert adapter.open_pr_for_branch("o/n", "feature/x") is None


def test_open_pr_for_branch_returns_none_on_failure():
    runner = FakeRunner(result=failed())
    adapter = GitHubAdapter(runner=runner)
    assert adapter.open_pr_for_branch("o/n", "feature/x") is None


def test_create_pr_success_returns_stripped_url():
    runner = FakeRunner(result=CommandResult(0, "https://github.com/o/n/pull/9\n", ""))
    adapter = GitHubAdapter(runner=runner)
    url = adapter.create_pr("o/n", head="feature/x", base="dev", title="t", body="b")
    assert url == "https://github.com/o/n/pull/9"
    argv = runner.calls[0]
    assert argv[:3] == ["gh", "pr", "create"]


def test_create_pr_failure_raises_github_error():
    runner = FakeRunner(result=failed(stderr="pr create failed"))
    adapter = GitHubAdapter(runner=runner)
    with pytest.raises(GitHubError, match="pr create failed"):
        adapter.create_pr("o/n", head="feature/x", base="dev", title="t", body="b")


# -- actor_for_event -------------------------------------------------------


class TimelineAdapter(GitHubAdapter):
    def __init__(self, events=None, *, raise_error=False):
        super().__init__(runner=None)
        self._events = events or []
        self._raise_error = raise_error

    def list_issue_events(self, repo, number):
        if self._raise_error:
            raise GitHubError("timeline unavailable")
        return self._events


def test_actor_for_event_finds_labeled_actor():
    adapter = TimelineAdapter([
        {"event": "labeled", "label": {"name": "claude-task"}, "actor": {"login": "alice"}},
    ])
    assert actor_for_event(adapter, "o/n", 1, "labeled", "claude-task") == "alice"


def test_actor_for_event_finds_assigned_actor():
    adapter = TimelineAdapter([
        {"event": "assigned", "assignee": {"login": "claude-bot"}, "actor": {"login": "bob"}},
    ])
    assert actor_for_event(adapter, "o/n", 1, "assigned", "claude-bot") == "bob"


def test_actor_for_event_returns_none_on_github_error():
    adapter = TimelineAdapter(raise_error=True)
    assert actor_for_event(adapter, "o/n", 1, "labeled", "x") is None


def test_actor_for_event_returns_none_when_no_matching_event():
    adapter = TimelineAdapter([{"event": "closed", "actor": {"login": "alice"}}])
    assert actor_for_event(adapter, "o/n", 1, "labeled", "claude-task") is None


def test_actor_for_event_ignores_label_mismatch():
    adapter = TimelineAdapter([
        {"event": "labeled", "label": {"name": "other-label"}, "actor": {"login": "alice"}},
    ])
    assert actor_for_event(adapter, "o/n", 1, "labeled", "claude-task") is None


def test_actor_for_event_ignores_assignee_mismatch():
    adapter = TimelineAdapter([
        {"event": "assigned", "assignee": {"login": "someone-else"}, "actor": {"login": "alice"}},
    ])
    assert actor_for_event(adapter, "o/n", 1, "assigned", "claude-bot") is None


def test_actor_for_event_picks_most_recent_matching_event():
    adapter = TimelineAdapter([
        {"event": "labeled", "label": {"name": "claude-task"}, "actor": {"login": "first"}},
        {"event": "labeled", "label": {"name": "claude-task"}, "actor": {"login": "second"}},
    ])
    assert actor_for_event(adapter, "o/n", 1, "labeled", "claude-task") == "second"


def test_actor_for_event_returns_none_when_actor_missing():
    adapter = TimelineAdapter([{"event": "labeled", "label": {"name": "claude-task"}}])
    assert actor_for_event(adapter, "o/n", 1, "labeled", "claude-task") is None
