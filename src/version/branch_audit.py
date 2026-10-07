"""Read-only branch-hygiene audit, scoped to one project at a time —
M16 `little-runner` parity: `lrun branch-audit run`. Ported from the
root `branch_audit.py`/`stabilize_branches.py` this ecosystem already
uses, but walking one project's own real checkout (Registry & Portfolio's
project boundary) rather than every submodule directly, since that's
this daemon's actual authority for "every project" — a caller wanting
the old tool's `--all` loops this Job over Registry's own project list.

Never merges, deletes, commits, or pushes anything — the same
`docs/policies/branching_policy.md` rule ("Automation never merges,
deletes, or force-pushes a branch on its own initiative") the original
tool states in its own docstring.

Violation kinds, unchanged from the original classifier:
  naming        - a branch that isn't the integration/production branch
                  and doesn't match a recognized branch-type prefix.
  stale_merged  - a branch that is a fully-merged ancestor of the
                  branch(es) it targets and still exists.
  abandoned     - an unmerged feature/improvement/hotfix branch with no
                  commits for longer than `stale_branch_days`.
  detached_head - HEAD is not a named branch (outside an in-progress
                  rebase/merge, which is left alone as a tool-managed
                  state, exactly like the original).
  stray_worktree - more than one `git worktree` entry.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from version import gitops
from version.branches import Branch
from version.common import get_integration_branch, get_production_branch
from version.configstore import load_config


@dataclass
class Violation:
    kind: str  # naming | stale_merged | abandoned | detached_head | stray_worktree
    branch: str = ""
    detail: str = ""
    recommended_action: str = ""


def local_branches(repo_root: Path) -> list[str]:
    result = gitops.run(["git", "branch", "--format=%(refname:short)"], repo_root)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def remote_branches(repo_root: Path) -> list[str]:
    result = gitops.run(["git", "branch", "-r", "--format=%(refname:short)"], repo_root, check=False)
    names = []
    for line in result.stdout.splitlines():
        ref = line.strip()
        if not ref or ref == "origin/HEAD" or "->" in ref:
            continue
        if ref.startswith("origin/"):
            names.append(ref[len("origin/"):])
    return names


def worktree_count(repo_root: Path) -> int:
    result = gitops.run(["git", "worktree", "list", "--porcelain"], repo_root)
    return sum(1 for line in result.stdout.splitlines() if line.startswith("worktree "))


def last_commit_age_days(repo_root: Path, ref: str) -> int | None:
    result = gitops.run(["git", "log", "-1", "--format=%ct", ref], repo_root, check=False)
    ts = result.stdout.strip()
    if result.returncode != 0 or not ts:
        return None
    return int((time.time() - int(ts)) // 86400)


def audit(repo_root: Path) -> list[Violation]:
    if gitops.is_merge_in_progress(repo_root) or gitops.is_rebase_in_progress(repo_root):
        return []  # in-progress rebase/merge — not audited this run, matching the original tool

    violations: list[Violation] = []
    integration = get_integration_branch(repo_root)
    production = get_production_branch(repo_root)
    stale_days = load_config(repo_root).get("stale_branch_days", 30)

    gitops.run(["git", "fetch", "origin", "--prune"], repo_root, check=False)

    try:
        gitops.current_branch(repo_root)
    except gitops.GitError:
        violations.append(Violation(
            kind="detached_head", detail="HEAD is not on a named branch.",
            recommended_action=f"git checkout {integration} (or whichever branch this should be).",
        ))

    wt_count = worktree_count(repo_root)
    if wt_count > 1:
        violations.append(Violation(
            kind="stray_worktree", detail=f"{wt_count} worktrees registered (expected 1).",
            recommended_action="git worktree list; remove any not actively in use with git worktree remove.",
        ))

    locals_ = set(local_branches(repo_root))
    remotes_ = set(remote_branches(repo_root))
    all_names = (locals_ | remotes_) - {integration, production}

    for branch in sorted(all_names):
        ref = branch if branch in locals_ else f"origin/{branch}"

        try:
            branch_type = Branch.parse(branch).type.value
        except ValueError:
            branch_type = "unknown"

        if branch_type in ("release", "support"):
            continue  # long-lived by design, exempt from staleness/stale-merged checks

        targets = [integration] if branch_type != "hotfix" else [integration, production]
        merged = all(gitops.is_merged(repo_root, ref, target) for target in targets)

        if branch_type == "unknown":
            action = "delete (already merged)." if merged else (
                "does not match a recognized branch-type prefix — review and either "
                "rename/finish properly, or delete if it's stray."
            )
            violations.append(Violation(kind="naming", branch=branch, detail="classified as: unknown", recommended_action=action))
            continue

        if merged:
            violations.append(Violation(
                kind="stale_merged", branch=branch, detail=f"already merged into {', '.join(targets)}.",
                recommended_action=f"delete: git branch -d {branch} (and the remote copy, if any).",
            ))
            continue

        age = last_commit_age_days(repo_root, ref)
        if age is not None and age > stale_days:
            violations.append(Violation(
                kind="abandoned", branch=branch, detail=f"no commits for {age} days (threshold: {stale_days}).",
                recommended_action="finish it, or delete it if abandoned.",
            ))

    return violations


def audit_as_dicts(repo_root: Path) -> list[dict]:
    return [asdict(v) for v in audit(repo_root)]
