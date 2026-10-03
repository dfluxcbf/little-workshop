"""Declared steps of the Version Control & Release pillar's Job types.

The keys are the ones `src/version/commands/` reports as it works; the steps
marked `needs_attention` are the ones that may stop and ask the user."""

from __future__ import annotations

from common.job_steps import StepSpec


def _s(key: str, title: str, attention: bool = False) -> StepSpec:
    return StepSpec(key, title, needs_attention=attention)


def prefixed(specs: tuple[StepSpec, ...], prefix: str, group: str) -> tuple[StepSpec, ...]:
    """`specs` as steps of a larger Job: keys get `prefix`, titles lead with `group`."""
    return tuple(StepSpec(prefix + s.key, f"{group}: {s.title}", s.needs_attention) for s in specs)


TOPIC_START = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on the integration branch"),
    _s("create_branch", "Create and check out the new branch"),
    _s("link_issue", "Link the issue to the branch"),
)

HOTFIX_START = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on the production branch"),
    _s("create_branch", "Create and check out the hotfix branch"),
    _s("link_issue", "Link the issue to the branch"),
)

TOPIC_FINISH = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on the right type of branch"),
    _s("save_state", "Save the finish state"),
    _s("rebase", "Rebase the branch onto the integration branch (version-number conflicts are resolved automatically)"),
    _s("resolve_conflicts", "Resolve the remaining rebase conflicts (needs your attention if there are any)", True),
    _s("squash", "Squash the branch into one commit"),
    _s("merge", "Merge the branch into the integration branch"),
    _s("bump_version", "Change the version number and run the version hooks"),
    _s("delete_branch", "Delete the finished branch"),
)

HOTFIX_FINISH = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on a hotfix branch"),
    _s("check_branches", "Check the production and integration branches exist"),
    _s("save_state", "Save the finish state"),
    _s("merge_production", "Merge the hotfix into the production branch"),
    _s("resolve_production_conflicts", "Resolve merge conflicts in production (needs your attention if there are any)", True),
    _s("bump_version", "Change the version number and run the version hooks"),
    _s("tag", "Tag the production release"),
    _s("merge_integration", "Merge the hotfix back into the integration branch"),
    _s("resolve_integration_conflicts", "Resolve merge conflicts in integration (needs your attention if there are any)", True),
    _s("delete_branch", "Delete the finished branch"),
)

RELEASE_START = (
    _s("check_branch", "Check we are on the integration branch"),
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("create_branch", "Create and check out the release branch"),
    _s("set_version", "Set the release version"),
)

RELEASE_FINISH = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on a release branch"),
    _s("check_branches", "Check the production and integration branches exist"),
    _s("check_candidate", "Check the promoted release candidate"),
    _s("finalize_version", "Set the final release version"),
    _s("save_state", "Save the finish state"),
    _s("merge_production", "Merge the release into the production branch"),
    _s("resolve_production_conflicts", "Resolve merge conflicts in production (needs your attention if there are any)", True),
    _s("tag", "Tag the production release"),
    _s("bump_version", "Bump the production minor version and run the version hooks"),
    _s("merge_integration", "Merge production back into the integration branch"),
    _s("resolve_integration_conflicts", "Resolve merge conflicts in integration (needs your attention if there are any)", True),
    _s("next_patch", "Establish the next patch version on the integration branch"),
    _s("delete_branch", "Delete the finished branch"),
)

RELEASE_CANDIDATE_CUT = (
    _s("check_clean_tree", "Check for uncommitted changes (needs your attention if there are any)", True),
    _s("check_branch", "Check we are on a release branch"),
    _s("set_version", "Set the release candidate version"),
    _s("tag", "Tag the release candidate"),
)

RELEASE = (
    _s("check_conditions", "Check the release conditions (merge state, size, tests, policies)"),
    *prefixed(RELEASE_START, "start_", "Start the release"),
    _s("changelog", "Write the changelog"),
    *prefixed(RELEASE_FINISH, "finish_", "Finish the release"),
    _s("relaunch", "Relaunch the project's server"),
)

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "version.feature_start": TOPIC_START,
    "version.improvement_start": TOPIC_START,
    "version.hotfix_start": HOTFIX_START,
    "version.feature_finish": TOPIC_FINISH,
    "version.improvement_finish": TOPIC_FINISH,
    "version.hotfix_finish": HOTFIX_FINISH,
    "version.release_start": RELEASE_START,
    "version.release_candidate_cut": RELEASE_CANDIDATE_CUT,
    "version.release_finish": RELEASE_FINISH,
    "version.release": RELEASE,
}
