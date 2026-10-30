"""Declared steps of the Deployment & Servers pillar's Job types.

The keys are the ones `src/deployment/flow.py` reports as it works. Types not listed
(stop, set_manifest, seed_confirmation, confirm) are one action and keep the default step."""

from __future__ import annotations

from common.job_steps import StepSpec

DEPLOY = (
    StepSpec("check_managed", "Check the server is managed by Deployment"),
    StepSpec("fetch_branch", "Fetch origin and locate the branch"),
    StepSpec("install", "Build and install the new version in a scratch worktree"),
    StepSpec("stop_old", "Stop the running version"),
    StepSpec("launch", "Launch the new version and wait for it to listen"),
    StepSpec("expose", "Expose the server through Tailscale"),
    StepSpec("confirm_gate", "Open the confirmation window (automatic rollback if unconfirmed)"),
)

REDEPLOY_PILLAR = (
    StepSpec("smoke_check", "Check out the commit and check it imports cleanly"),
    StepSpec("install", "Copy the new version into its install location"),
    StepSpec("refresh_cli", "Refresh the installed lws command"),
    StepSpec("stop_old", "Stop the running pillar"),
    StepSpec("launch", "Launch the new pillar"),
    StepSpec("confirm_gate", "Open the confirmation window (automatic rollback if unconfirmed)"),
)

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "deployment.deploy": DEPLOY,
    "deployment.redeploy_pillar": REDEPLOY_PILLAR,
}
