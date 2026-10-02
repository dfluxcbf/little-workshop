"""Policy `issue-form-installed` (agent pillar; gates branch_finish): where a project uses
issue-driven automation (a workflow triggered by issues, or the Claude Code action), the
project contains the .github/ISSUE_TEMPLATE/claude-task.yml issue form."""

import os
import re
from pathlib import Path

import pytest

PROJECT = Path(os.environ.get("POLICY_PROJECT_DIR", "/project"))
WORKFLOWS = PROJECT / ".github" / "workflows"
TEMPLATE = PROJECT / ".github" / "ISSUE_TEMPLATE" / "claude-task.yml"
FIX = (
    "Fix: add the issue form project.bootstrap scaffolds (.github/ISSUE_TEMPLATE/claude-task.yml) "
    "so issue-driven tasks carry what an agent needs."
)

# Signals that a project uses issue-driven automation: a workflow triggered by
# issue events, or one that runs the Claude Code action.
ISSUE_TRIGGER = re.compile(
    r"^\s*(issues|issue_comment)\s*:|\bon\s*:\s*\[?[^\n]*\bissues\b|"
    r"anthropics/claude-code-action|@claude",
    re.MULTILINE,
)


def _workflow_files():
    if not WORKFLOWS.is_dir():
        return []
    return sorted(p for p in WORKFLOWS.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))


def _uses_issue_driven_automation():
    for wf in _workflow_files():
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if ISSUE_TRIGGER.search(text):
            return True
    return False


def test_claude_task_issue_form_present():
    if not PROJECT.is_dir():
        pytest.fail(f"{PROJECT} is not mounted")
    if not _uses_issue_driven_automation() and not TEMPLATE.exists():
        pytest.skip("the project does not use issue-driven automation")
    assert TEMPLATE.is_file(), f"The project uses issue-driven automation but has no claude-task.yml. {FIX}"
    assert TEMPLATE.stat().st_size > 0, f"claude-task.yml is empty. {FIX}"
