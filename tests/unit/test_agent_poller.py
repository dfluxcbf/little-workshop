import pytest

from agent import db
from agent.github_adapter import GitHubAdapter
from agent.poller import detect_issue_triggers, poll_tick


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


def test_detect_issue_triggers_finds_newly_labeled():
    issues = [{"number": 1, "labels": [{"name": "claude-task"}], "assignees": []}]
    cursor = {"labeled": [], "assigned": [], "comment_cursor": {}}
    triggers, new_cursor = detect_issue_triggers(issues, cursor, "claude-task", [])
    assert len(triggers) == 1
    assert triggers[0].number == 1
    assert triggers[0].action == "labeled"
    assert new_cursor["labeled"] == [1]


def test_detect_issue_triggers_ignores_already_seen_label():
    issues = [{"number": 1, "labels": [{"name": "claude-task"}], "assignees": []}]
    cursor = {"labeled": [1], "assigned": [], "comment_cursor": {}}
    triggers, _ = detect_issue_triggers(issues, cursor, "claude-task", [])
    assert triggers == []


def test_detect_issue_triggers_finds_newly_assigned():
    issues = [{"number": 2, "labels": [], "assignees": [{"login": "claude-bot"}]}]
    cursor = {"labeled": [], "assigned": [], "comment_cursor": {}}
    triggers, new_cursor = detect_issue_triggers(issues, cursor, "claude-task", ["claude-bot"])
    assert len(triggers) == 1
    assert triggers[0].action == "assigned"
    assert triggers[0].match == "claude-bot"


def test_detect_issue_triggers_labeled_takes_priority_over_assigned():
    issues = [{"number": 3, "labels": [{"name": "claude-task"}], "assignees": [{"login": "claude-bot"}]}]
    cursor = {"labeled": [], "assigned": [], "comment_cursor": {}}
    triggers, _ = detect_issue_triggers(issues, cursor, "claude-task", ["claude-bot"])
    assert len(triggers) == 1
    assert triggers[0].action == "labeled"


def test_detect_issue_triggers_label_removed_then_reapplied_retriggers():
    cursor = {"labeled": [1], "assigned": [], "comment_cursor": {}}
    issues_after_removal = [{"number": 1, "labels": [], "assignees": []}]
    _, cursor_after_removal = detect_issue_triggers(issues_after_removal, cursor, "claude-task", [])
    assert cursor_after_removal["labeled"] == []

    issues_relabeled = [{"number": 1, "labels": [{"name": "claude-task"}], "assignees": []}]
    triggers, _ = detect_issue_triggers(issues_relabeled, cursor_after_removal, "claude-task", [])
    assert len(triggers) == 1


class FakeAdapter(GitHubAdapter):
    def __init__(self, issues, events_by_number):
        super().__init__(runner=None)
        self._issues = issues
        self._events = events_by_number

    def list_open_issues(self, repo, *, label=None):
        return self._issues

    def list_issue_events(self, repo, number):
        return self._events.get(number, [])


def test_poll_tick_resolves_real_actor_via_timeline(conn):
    issues = [{"number": 5, "labels": [{"name": "claude-task"}], "assignees": []}]
    events = {5: [{"event": "labeled", "label": {"name": "claude-task"}, "actor": {"login": "real-user"}}]}
    adapter = FakeAdapter(issues, events)

    triggers = poll_tick(conn, adapter, "little-meals", "owner/little-meals", trigger_label="claude-task", claude_logins=[])
    assert len(triggers) == 1
    assert triggers[0].sender == "real-user"


def test_poll_tick_persists_cursor_across_calls(conn):
    issues = [{"number": 5, "labels": [{"name": "claude-task"}], "assignees": []}]
    adapter = FakeAdapter(issues, {})

    first = poll_tick(conn, adapter, "little-meals", "owner/little-meals", trigger_label="claude-task", claude_logins=[])
    assert len(first) == 1

    second = poll_tick(conn, adapter, "little-meals", "owner/little-meals", trigger_label="claude-task", claude_logins=[])
    assert second == []


def test_poll_tick_missing_actor_resolves_to_none(conn):
    issues = [{"number": 5, "labels": [{"name": "claude-task"}], "assignees": []}]
    adapter = FakeAdapter(issues, {})  # no timeline events recorded

    triggers = poll_tick(conn, adapter, "little-meals", "owner/little-meals", trigger_label="claude-task", claude_logins=[])
    assert triggers[0].sender is None
