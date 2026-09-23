"""`support` command implementations — ported from `little-versions`'
`commands/support.py`. Deliberately restricted: support finish is
policy-driven and refuses rather than picking a merge target on its
own — support history must never silently diverge from the primary
lines."""

from __future__ import annotations

from pathlib import Path

from version.common import CommandResult, require_clean_tree
from version.gitops import branch_exists, create_and_checkout, current_branch, get_tag_commit, tag_exists


def cmd_support_start(repo_root: Path, version: str, force: bool = False) -> CommandResult:
    require_clean_tree(repo_root, force)

    tag = f"v{version}"
    if not tag_exists(repo_root, tag):
        return CommandResult(success=False, message=f"Production tag {tag!r} does not exist. Cannot create support branch.")

    branch_name = f"support/{version}"
    if branch_exists(repo_root, branch_name):
        return CommandResult(success=False, message=f"Branch {branch_name!r} already exists.")

    tag_commit = get_tag_commit(repo_root, tag)
    create_and_checkout(repo_root, branch_name, tag_commit)

    return CommandResult(success=True, message=f"Created {branch_name!r} from tag {tag!r}.", data={"branch": branch_name, "tag": tag})


def cmd_support_finish(repo_root: Path) -> CommandResult:
    cur = current_branch(repo_root)
    if not cur.startswith("support/"):
        return CommandResult(success=False, message=f"Not on a support/* branch. Current: {cur!r}")

    return CommandResult(
        success=False,
        message=(
            "Support finish is policy-driven. Merge support changes into dedicated "
            "maintenance branches only. Never let support history diverge silently "
            "from primary histories."
        ),
        data={"branch": cur},
    )
