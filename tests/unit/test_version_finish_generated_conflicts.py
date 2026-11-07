"""Finishing a branch whose rebase stops on files little-workshop generates: the version pillar writes
them again from the generator and goes on; a conflict in any other file still stops the finish."""

import json
import subprocess

import pytest
from version_repo_helper import make_repo

from registry import claude_sync
from registry import claude_templates as ct
from version import generated
from version.commands.git_hooks import COMMIT_MSG_HOOK
from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.gitops import current_branch, is_rebase_in_progress

PROJECT = "little-widget"


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo, files: dict, message):
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def _settings(extra_allow: str) -> str:
    settings = ct.settings(PROJECT, "bazel")
    settings["permissions"]["allow"].append(extra_allow)
    return json.dumps(settings, indent=2) + "\n"


@pytest.fixture
def registry_answers(monkeypatch):
    """Registry & Portfolio answering in-process, as its endpoint does."""
    calls = []

    def fetch(project_id, checkout, sides):
        calls.append((project_id, len(sides)))
        return claude_sync.regenerated(checkout, project_id, sides)

    monkeypatch.setattr(generated, "fetch_from_registry", fetch)
    return calls


def _two_branches(repo, a_files: dict, b_files: dict):
    """feature/a and feature/b cut from the same dev tip, neither touching VERSION; feature/a finishes first."""
    cfg_path = repo / ".lvx" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["project_id"] = PROJECT
    _git(["checkout", "dev"], repo)
    _commit(repo, {".lvx/config.json": json.dumps(cfg), ct.SETTINGS_PATH: _settings("Bash(make base*)"),
                   ct.SHARED_BRIEFING_PATH: "old briefing\n", ".githooks/commit-msg": "#!/bin/sh\nexit 0\n"}, "chore: base")
    cmd_topic_start(repo, prefix="feature", name="a")
    _commit(repo, a_files, "feat: a")
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="b")
    _commit(repo, b_files, "feat: b")
    _git(["checkout", "feature/a"], repo)
    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success, result.message
    assert (repo / "VERSION").read_text() == "0.1.1\n"  # the finish wrote the version once
    _git(["checkout", "feature/b"], repo)


# --- REQ-000000487: generated-file conflicts are settled by regenerating them --------------------

def test_conflicts_in_generated_files_are_regenerated_and_the_finish_completes(tmp_path, registry_answers):
    repo = make_repo(tmp_path)
    _two_branches(
        repo,
        {ct.SETTINGS_PATH: _settings("Bash(make a*)"), ct.SHARED_BRIEFING_PATH: "briefing a\n",
         ".githooks/commit-msg": "#!/bin/sh\necho a\n", "a.py": "a = 1\n"},
        {ct.SETTINGS_PATH: _settings("Bash(make b*)"), ct.SHARED_BRIEFING_PATH: "briefing b\n",
         ".githooks/commit-msg": "#!/bin/sh\necho b\n", "b.py": "b = 1\n"},
    )

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert current_branch(repo) == "dev" and not is_rebase_in_progress(repo)
    assert _git(["status", "--porcelain"], repo).stdout == ""
    assert (repo / "VERSION").read_text() == "0.1.2\n"
    assert (repo / "a.py").exists() and (repo / "b.py").exists()
    wanted = ct.claude_files(PROJECT, "bazel", **claude_sync.options_in(repo, PROJECT))
    assert (repo / ct.SHARED_BRIEFING_PATH).read_text() == wanted[ct.SHARED_BRIEFING_PATH]
    allow = json.loads((repo / ct.SETTINGS_PATH).read_text())["permissions"]["allow"]
    assert {"Bash(make a*)", "Bash(make b*)"} <= set(allow)  # the project's own entries of both sides
    assert (repo / ".githooks" / "commit-msg").read_text() == COMMIT_MSG_HOOK
    assert registry_answers == [(PROJECT, 2)]


def test_a_conflict_in_a_projects_own_file_still_stops_the_finish(tmp_path, registry_answers):
    repo = make_repo(tmp_path)
    _two_branches(repo, {ct.SHARED_BRIEFING_PATH: "briefing a\n", ".claude/AGENTS.md": "a\n"},
                  {ct.SHARED_BRIEFING_PATH: "briefing b\n", ".claude/AGENTS.md": "b\n"})

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert not result.success and "conflict" in result.message.lower()
    assert is_rebase_in_progress(repo)
    assert _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.split() == [".claude/AGENTS.md"]


def test_without_the_registry_a_generated_conflict_is_left_for_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(generated, "fetch_from_registry", lambda *args: None)
    repo = make_repo(tmp_path)
    _two_branches(repo, {ct.SHARED_BRIEFING_PATH: "briefing a\n"}, {ct.SHARED_BRIEFING_PATH: "briefing b\n"})

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert not result.success and is_rebase_in_progress(repo)
    assert _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.split() == [ct.SHARED_BRIEFING_PATH]


def test_a_retired_pre_commit_hook_in_conflict_is_removed(tmp_path, registry_answers):
    repo = make_repo(tmp_path)
    old = "#!/bin/sh\nlws version sync-version-files --project-id 'x'\n"
    _two_branches(repo, {".githooks/pre-commit": old + "# a\n"}, {".githooks/pre-commit": old + "# b\n"})

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert not (repo / ".githooks" / "pre-commit").exists()


def test_a_branch_still_tracking_the_old_state_file_finishes_after_dev_untracked_it(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _commit(repo, {".lvx/state.json": "{}\n"}, "chore: the old tracked state")
    cmd_topic_start(repo, prefix="feature", name="old")
    _commit(repo, {".lvx/state.json": '{"finish_in_progress": {}}\n', "old.py": "x = 1\n"}, "chore: save finish state")
    _git(["checkout", "dev"], repo)
    _git(["rm", "-q", "--cached", ".lvx/state.json"], repo)
    _commit(repo, {".gitignore": ".lvx/state.json\n"}, "chore: untrack the state")
    _git(["checkout", "feature/old"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert _git(["ls-files", ".lvx/state.json"], repo).stdout == "" and (repo / "old.py").exists()
