"""The version pillar's finish and start flows as checklists: steps reported in order, and the
questions put to the user (uncommitted changes, conflicts) answered through a scripted reporter."""

from __future__ import annotations

import subprocess

import pytest
from version_repo_helper import make_repo

from common.job_steps import StepFailed, StepReporter, reporting
from common.step_specs import steps_for
from version.commands.hotfix import cmd_hotfix_finish, cmd_hotfix_start
from version.commands.release import cmd_release_candidate_cut, cmd_release_finish, cmd_release_start
from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.gitops import current_branch, is_rebase_in_progress


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo, name, content, message):
    (repo / name).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


class Scripted(StepReporter):
    """Records every report and answers questions from `answers` (option ids, in order)."""

    def __init__(self, answers=()):
        super().__init__(None, "job", call=lambda *a, **k: {})
        self.events: list[tuple[str, str]] = []
        self.questions: list[tuple[str, str]] = []
        self.answers = list(answers)

    def report(self, key, state, *, detail=None, prompt=None):
        self.events.append((key, state))

    def ask(self, key, question, options):
        self.events.append((key, "attention"))
        self.questions.append((key, question))
        choice = self.answers.pop(0)
        assert choice in {o.id for o in options}
        self.events.append((key, "running"))
        return choice

    def finished(self):
        return [key for key, state in self.events if state == "succeeded"]

    def failed(self):
        return [key for key, state in self.events if state == "failed"]


def _declared(job_type):
    return [spec.key for spec in steps_for(job_type)]


def _dev_feature(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    assert cmd_topic_start(repo, prefix="feature", name="widgets").success
    _commit(repo, "widgets.py", "a = 1\n", "wip")
    return repo


# --- REQ-000000310: Every job has a checklist of its actions ---

def test_a_feature_finish_reports_every_declared_step_in_order(tmp_path):
    repo = _dev_feature(tmp_path)
    reporter = Scripted()
    with reporting(reporter):
        assert cmd_topic_finish(repo, prefix="feature", commit_label="feat").success
    assert reporter.finished() == _declared("version.feature_finish")
    assert reporter.failed() == []


def test_a_feature_start_reports_every_declared_step(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    reporter = Scripted()
    with reporting(reporter):
        assert cmd_topic_start(repo, prefix="feature", name="x", issue=4).success
    assert reporter.finished() == _declared("version.feature_start")


def test_a_hotfix_runs_through_its_declared_steps(tmp_path):
    repo = make_repo(tmp_path)
    start = Scripted()
    with reporting(start):
        assert cmd_hotfix_start(repo, "urgent").success
    assert start.finished() == _declared("version.hotfix_start")
    _commit(repo, "fix.py", "x = 1\n", "fix it")
    finish = Scripted()
    with reporting(finish):
        assert cmd_hotfix_finish(repo).success
    assert finish.finished() == _declared("version.hotfix_finish")


def test_a_release_runs_through_its_declared_steps(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    start = Scripted()
    with reporting(start):
        assert cmd_release_start(repo).success
    assert start.finished() == _declared("version.release_start")
    cut = Scripted()
    with reporting(cut):
        assert cmd_release_candidate_cut(repo).success
    assert cut.finished() == _declared("version.release_candidate_cut")
    finish = Scripted()
    with reporting(finish):
        assert cmd_release_finish(repo).success
    assert finish.finished() == _declared("version.release_finish")


def test_a_wrong_branch_fails_the_check_branch_step(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    reporter = Scripted()
    with reporting(reporter):
        result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert not result.success and "feature/*" in result.message
    assert reporter.failed() == ["check_branch"]


# --- REQ-000000311: An action that needs the user asks, and times out after 12 hours ---

def test_uncommitted_changes_ask_and_commit_when_the_user_says_so(tmp_path):
    repo = _dev_feature(tmp_path)
    (repo / "extra.py").write_text("y = 1\n", encoding="utf-8")
    reporter = Scripted(["commit"])
    with reporting(reporter):
        assert cmd_topic_finish(repo, prefix="feature", commit_label="feat").success
    assert reporter.questions[0][0] == "check_clean_tree" and "extra.py" in reporter.questions[0][1]
    assert (repo / "extra.py").exists()  # committed, then squashed into the merge
    assert current_branch(repo) == "dev"


def test_uncommitted_changes_can_be_left_alone_or_stop_the_job(tmp_path):
    repo = _dev_feature(tmp_path)
    (repo / "extra.py").write_text("y = 1\n", encoding="utf-8")
    stop = Scripted(["stop"])
    with reporting(stop):
        result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert not result.success and "uncommitted" in result.message
    assert stop.failed() == ["check_clean_tree"] and current_branch(repo) == "feature/widgets"


def test_a_rebase_conflict_asks_and_continues_once_the_user_resolved_it(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _commit(repo, "shared.py", "a = 1\n", "baseline")
    cmd_topic_start(repo, prefix="feature", name="widgets")
    _commit(repo, "shared.py", "a = 2\n", "feature change")
    _git(["checkout", "dev"], repo)
    _commit(repo, "shared.py", "a = 3\n", "dev change")
    _git(["checkout", "feature/widgets"], repo)

    class Resolving(Scripted):
        def ask(self, key, question, options):
            if key == "resolve_conflicts":
                (repo / "shared.py").write_text("a = 2\n", encoding="utf-8")
                _git(["add", "shared.py"], repo)
            return super().ask(key, question, options)

    reporter = Resolving(["continue"])
    with reporting(reporter):
        result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success, result.message
    assert "shared.py" in reporter.questions[0][1]
    assert reporter.finished() == _declared("version.feature_finish")


def test_a_rebase_conflict_asks_again_while_conflicts_remain_then_aborts(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _commit(repo, "shared.py", "a = 1\n", "baseline")
    cmd_topic_start(repo, prefix="feature", name="widgets")
    _commit(repo, "shared.py", "a = 2\n", "feature change")
    _git(["checkout", "dev"], repo)
    _commit(repo, "shared.py", "a = 3\n", "dev change")
    _git(["checkout", "feature/widgets"], repo)

    reporter = Scripted(["continue", "abort"])  # nothing was resolved: the first continue is refused
    with reporting(reporter):
        result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert not result.success and "aborted" in result.message
    assert [k for k, _ in reporter.questions] == ["resolve_conflicts", "resolve_conflicts"]
    assert not is_rebase_in_progress(repo) and current_branch(repo) == "feature/widgets"
    assert reporter.failed() == ["resolve_conflicts"]


def test_a_hotfix_merge_conflict_asks_and_abort_restores_the_branch(tmp_path):
    repo = make_repo(tmp_path)
    cmd_hotfix_start(repo, "urgent")
    _commit(repo, "VERSION", "9.9.9\n", "hotfix touches VERSION")
    _git(["checkout", "master"], repo)
    _commit(repo, "VERSION", "7.7.7\n", "production moved on")
    _git(["checkout", "hotfix/urgent"], repo)
    reporter = Scripted(["abort"])
    with reporting(reporter):
        result = cmd_hotfix_finish(repo)
    assert not result.success
    assert reporter.questions and reporter.questions[0][0] == "resolve_production_conflicts"
    assert current_branch(repo) == "hotfix/urgent"


def test_without_a_user_to_ask_a_dirty_tree_and_conflicts_fail_as_before(tmp_path):
    repo = _dev_feature(tmp_path)
    (repo / "extra.py").write_text("y = 1\n", encoding="utf-8")
    from version.gitops import DirtyWorkingTreeError

    with pytest.raises(DirtyWorkingTreeError):
        cmd_topic_finish(repo, prefix="feature", commit_label="feat")
