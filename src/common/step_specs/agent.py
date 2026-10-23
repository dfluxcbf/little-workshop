"""Declared steps of the Agentic Development pillar's Job types (reported by `src/agent/`)."""

from __future__ import annotations

from common.job_steps import StepSpec

RUN_TASK = (
    StepSpec("check_checkout", "Check the project checkout is clean and read its version config"),
    StepSpec("prepare_branch", "Prepare the working branch"),
    StepSpec("run_claude", "Run Claude on the task"),
    StepSpec("detect_branch", "Find the branch the agent worked on"),
    StepSpec("screenshots", "Capture screenshots of the result"),
    StepSpec("publish", "Commit, push and open the pull request"),
    StepSpec("record_run", "Write the report and record the run"),
    StepSpec("auto_finish", "Finish the branch automatically (auto-approved runs only)"),
)

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "agent.run_task": RUN_TASK,
    # A confirmed incident's fix is an ordinary task run.
    "agent.confirm_incident": RUN_TASK,
    "agent.ci_check": (
        StepSpec("resolve_pr", "Look up the pull request's head commit"),
        StepSpec("post_pending", "Post a pending status to GitHub"),
        StepSpec("run_tests", "Run the test suite on the commit"),
        StepSpec("post_result", "Post the result to GitHub and record it"),
    ),
    "agent.session_start": (
        StepSpec("check_session", "Check dtach is installed, no session is running and the folder is trusted"),
        StepSpec("launch_session", "Launch the Remote Control session"),
    ),
    "agent.export_asset": (
        StepSpec("export", "Export each Blender file: 3D model, ten previews, UV layout and textures"),
    ),
    "agent.update_docs": (
        StepSpec("claim_comments", "Claim the branch's open review comments"),
        StepSpec("start_session", "Create the worktree and start the Claude session"),
        StepSpec("wait_session", "Wait for Claude to address the comments"),
        StepSpec("finalize", "Commit, push and mark each comment"),
    ),
}
