"""Declared steps of the Registry & Portfolio pillar's Job types (reported by `src/registry/`)."""

from __future__ import annotations

from common.job_steps import StepSpec

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "project.bootstrap": (
        StepSpec("check_name", "Check the project name is usable and not registered yet"),
        StepSpec("scaffold", "Scaffold the project files"),
        StepSpec("init_repo", "Create the git repository with its first commit and dev branch"),
        StepSpec("create_github", "Create the GitHub repository and push (when requested)"),
        StepSpec("labels", "Create the GitHub labels (when requested)"),
        StepSpec("register", "Register the project"),
        StepSpec("init_version", "Initialize version tracking and the gitflow branches"),
    ),
    "project.import": (
        StepSpec("check_path", "Check the project is new and the path is a git repository"),
        StepSpec("register", "Register the project"),
    ),
    "project.connect_github": (
        StepSpec("check_project", "Check the project is registered and is a git repository"),
        StepSpec("github_repo", "Create the GitHub repository and push, or adopt the existing origin"),
        StepSpec("labels", "Ensure the GitHub labels (when requested)"),
        StepSpec("record_repo", "Record the repository URL"),
    ),
    "project.remove": (
        StepSpec("check_project", "Check the project is registered"),
        StepSpec("purge_registry", "Delete the project's registry records"),
        StepSpec("purge_files", "Delete the project's files directory"),
    ),
    "registry.backlog_refine": (
        StepSpec("read_entry", "Read the backlog entry"),
        StepSpec("ask_claude", "Have Claude write the refined entry"),
        StepSpec("save", "Replace the entry's refined entry"),
    ),
    "registry.backlog_task_prompt": (
        StepSpec("read_entry", "Read the backlog entry and its refined entry"),
        StepSpec("ask_claude", "Have Claude write the task prompt"),
        StepSpec("save", "Replace the entry's task prompt, ending with the instruction to delete the entry"),
    ),
}
