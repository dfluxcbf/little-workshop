"""Declared steps of the Policy & Compliance pillar's Job types (keys as `src/policy/app.py` reports them).

Job types not listed (remove, approve/reject/restore draft, configure, set_projects) are one action each."""

from __future__ import annotations

from common.job_steps import StepSpec

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "policy.author": (
        StepSpec("validate_request", "Validate the request"),
        StepSpec("check_ears", "Check the policy text is in EARS form, refining it if needed"),
        StepSpec("check_enforceable", "Check the rule can be enforced mechanically"),
        StepSpec("check_unique", "Check no policy already holds this scope and topic"),
        StepSpec("check_conflicts", "Check the rule does not conflict with existing policies"),
        StepSpec("generate_script", "Generate the check script"),
        StepSpec("save_draft", "Save the result as a draft for review"),
    ),
    "policy.activate": (
        StepSpec("check_approval", "Check the request is complete and approved"),
        StepSpec("save_policy", "Save the policy"),
    ),
    "policy.draft_import": (
        StepSpec("import_drafts", "Validate and import the drafts"),
    ),
    "policy.check_project": (
        StepSpec("find_policy", "Find the policy"),
        StepSpec("check_changes", "Check whether anything changed since the last verdict"),
        StepSpec("prepare_sandbox", "Prepare the sandbox mounts"),
        StepSpec("run_check", "Run the check script in the sandbox"),
        StepSpec("record_result", "Record the verdict"),
    ),
    "policy.check_all": (
        StepSpec("find_policy", "Find the policy and its projects"),
        StepSpec("dispatch_checks", "Start one check job per project"),
    ),
}
