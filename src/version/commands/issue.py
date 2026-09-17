"""`issue` command implementations — closes M16's `little-versions`
parity gap (`docs/requirements.md`'s M16 follow-up): standalone
issue↔branch linking, previously only settable once at a topic branch's
own `*_start` time with no way to view or change it afterward.
Functional parity with `lvx issue set/unset/list`, not literal
command-surface parity — this reuses `branches.py`'s own
`issue_automation` flag (already the single source of truth for which
branch types support issue linkage) rather than a second, separately
maintained list.
"""

from __future__ import annotations

from pathlib import Path

from version.branches import Branch, branch_type_policy
from version.common import CommandResult
from version.configstore import get_issue_link, list_issue_links, remove_issue_link, set_issue_link
from version.gitops import current_branch


def _is_linkable(branch_name: str) -> bool:
    try:
        branch = Branch.parse(branch_name)
    except ValueError:
        return False
    return branch_type_policy().get(branch.type.value, {}).get("issue_automation", False)


def cmd_issue_set(repo_root: Path, issue_number: int, branch: str | None = None) -> CommandResult:
    if branch is None:
        branch = current_branch(repo_root)
    if not _is_linkable(branch):
        return CommandResult(success=False, message=f"branch {branch!r} does not support issue linkage")
    set_issue_link(repo_root, branch, issue_number)
    return CommandResult(success=True, message=f"linked issue #{issue_number} to {branch!r}", data={"branch": branch, "issue": issue_number})


def cmd_issue_unset(repo_root: Path, branch: str | None = None) -> CommandResult:
    if branch is None:
        branch = current_branch(repo_root)
    existing = get_issue_link(repo_root, branch)
    if existing is None:
        return CommandResult(success=False, message=f"no issue linked to {branch!r}")
    remove_issue_link(repo_root, branch)
    return CommandResult(success=True, message=f"removed issue #{existing} link from {branch!r}", data={"branch": branch, "removed_issue": existing})


def cmd_issue_list(repo_root: Path) -> CommandResult:
    links = list_issue_links(repo_root)
    return CommandResult(success=True, message="ok", data={"links": links})
