"""little-workshop's own bookkeeping commits cite REQ-NONE: they affect no
requirement, yet a version hook can make them touch src/ (little-home's
src/package.json), which `commit-cites-requirement` judges. REQ-000000266."""

import subprocess

from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.gitops import NO_REQUIREMENT, no_requirement


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_no_requirement_appends_the_tag_as_its_own_paragraph():
    assert no_requirement("chore: bump version to 1.2.3") == "chore: bump version to 1.2.3\n\nREQ-NONE"
    assert NO_REQUIREMENT == "REQ-NONE"


def test_a_finish_bump_commit_cites_req_none_and_the_merge_does_not(tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="widgets")
    (repo / "widgets.py").write_text("a = 1\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "feat: widgets REQ-000000001"], repo)

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    bump = _git(["show", "-s", "--format=%B", "HEAD"], repo)
    assert bump.splitlines()[0] == "chore: bump version to 0.1.1"
    assert "REQ-NONE" in bump
    # The merge carries real changes: it never declares that no requirement is affected.
    merge = _git(["show", "-s", "--format=%B", "HEAD^"], repo)
    assert "REQ-NONE" not in merge


def test_the_agent_is_told_to_cite_requirements_or_req_none():
    """REQ-000000267: every agent run's prompt carries the common ground rules."""
    from agent import templates_util

    common = templates_util.read_prompt("common.md")
    assert "`REQ-nnnnnnnnn`" in common
    assert "`REQ-NONE` when it affects no requirement — never both" in common
