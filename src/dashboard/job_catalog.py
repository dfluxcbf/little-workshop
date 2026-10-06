"""The jobs a person can start or schedule from the Dashboard's New Job page:
pick a project, pick one of these jobs, fill in its arguments.

This list is the whole menu. A new user-triggerable job is added here as one
more `JobSpec` (its arguments as `JobArg`s, and `make` turning the filled-in
arguments into the one Orchestration Job it runs) — never as a free-form job
template, and never drafted by Claude.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

# Every project little-workshop manages builds with Bazel and installs with
# `bazel run //:install` (each manifest.deployment.toml's `install`).
BUILD_COMMAND = ["bazel", "build", "//..."]
INSTALL_COMMAND = ["bazel", "run", "//:install"]
# What a job runs against: the project checkout's current commit.
COMMITISH = "HEAD"


@dataclass(frozen=True)
class JobArg:
    name: str
    label: str
    # "flag": a checkbox. "multi": pick any of a per-project list, fetched from
    # /jobs/catalog/choices/<name>?project=… when the job is chosen.
    # "number": a whole number, 0 or more. "choice": one of `options`.
    kind: str
    help: str = ""
    required: bool = False
    default: str = ""
    options: tuple[tuple[str, str], ...] = ()  # "choice": (value, label) pairs
    group: str = ""  # consecutive args sharing a group are shown under that heading


@dataclass(frozen=True)
class JobSpec:
    key: str
    label: str
    description: str
    make: Callable[[str, dict], dict]  # (project, argument values) -> the Job: type, target_pillar, project_id, queue_key, params
    args: tuple[JobArg, ...] = field(default_factory=tuple)
    projects: tuple[str, ...] = ()  # the only projects offering this job; empty = every project

    def offered_for(self, project: str) -> bool:
        return not self.projects or project in self.projects


def _build(project: str, values: dict) -> dict:
    steps = [BUILD_COMMAND, INSTALL_COMMAND] if values["install"] else [BUILD_COMMAND]
    # A login shell, so the build sees the same PATH (bazel, pipx) a terminal does.
    command = ["bash", "-lc", " && ".join(shlex.join(step) for step in steps)]
    # include_standards is off: the task edits the original folder, and a copy would only mislead it.
    # The project's own queue: an install must never overlap a Launch of the same project.
    return {"type": "build.run", "target_pillar": "build", "project_id": project, "queue_key": project,
            "params": {"commitish": COMMITISH, "command": command}}


def _run_tests(project: str, values: dict) -> dict:
    return {"type": "requirements.run_tests", "target_pillar": "requirements", "project_id": project, "queue_key": None,
            "params": {"commitish": COMMITISH, "suite_ids": values["suites"]}}


def _release(project: str, values: dict) -> dict:
    # The project's own queue: nothing else may branch, build or launch it mid-release.
    return {"type": "version.release", "target_pillar": "version", "project_id": project, "queue_key": project,
            "params": dict(values)}


WORKSHOP_PROJECT = "little-workshop"
STANDARDS_FOLDER = "shared_project_standards"

STANDARDS_TITLE = "Update Standards Documentation"
STANDARDS_DESCRIPTION = f"""Update the `{STANDARDS_FOLDER}/` folder at the root of this repository so it matches what little-workshop's standards and policies say today. Start with `{STANDARDS_FOLDER}/README.md`: it states the folder's rules and the layout you must keep.

Review, in full, before writing anything:

1. Every little-workshop standard: `docs/governance_policy.md`, `docs/requirements_policy.md`, `docs/version_policy.md`, `docs/architecture.md`, `docs/design.md`, `docs/milestones.md`, `docs/open_questions.md`, `docs/pillars/*.md`, the agent prompt templates under `src/agent/templates/prompts/`, `src/dashboard/job_catalog.py`, and the super-repo's `/home/crist/little-projects/docs/policies/*.md`.
2. Every policy registered in Policy & Compliance: run `lws policy list` and `lws policy projects list` for each policy's rule, scope, gate points and the projects it is attached to; its code in `tools/policy_scripts/` (one script per policy plus `policies.json`); and any pending drafts in `docs/pending_policy_drafts/`.
3. Every registered project (`lws project list`): its `.claude/AGENTS.md` (or a `CLAUDE.md` where one remains) and `.claude/rules/`, `README.md`, `docs/*_policy.md`, and any project-specific policy. A project on a Windows drive (`/mnt/c/...`) is read where it is.

Then update every document in `{STANDARDS_FOLDER}/`:

- Rewrite each shared standard so it states the rule in force now. Correct what changed; remove what no longer holds; add what is new. Keep each file's purpose and name.
- Rewrite `policy_register.md` from the live policy list, including the attachment matrix and any discrepancy between a policy's documented scope and where it is actually attached.
- Give every registered project a `<project name with - replaced by _>_standards/` folder. A policy scoped to specific projects, and any project-specific convention, goes in that project's folder, never in the shared files. Create a folder for a project that lacks one; keep the folder of a project that is no longer registered but say so in its README.
- Keep `{STANDARDS_FOLDER}/README.md`'s index tables in step with the files that exist, and set every changed file's "Last reviewed" line to today's date.

Rules:

- Markdown files only. No scripts, JSON or generated data in the folder.
- Compile, do not originate: each file summarises a rule and names its authoritative source. If a source and the folder disagree, the source wins. State only what you verified in the sources this run; record an inconsistency between sources in the relevant file's discrepancy notes instead of picking a side silently.
- Do not edit anything outside `{STANDARDS_FOLDER}/`: no source documents, no code, no policy, no other project's checkout.
- When nothing changed for a file, leave it untouched, including its date.
"""


def _update_standards(project: str, values: dict) -> dict:
    # The project's own queue: it edits the checkout, so nothing else may branch it meanwhile.
    return {"type": "agent.run_task", "target_pillar": "agent", "project_id": project, "queue_key": project,
            "params": {"title": STANDARDS_TITLE, "intent": "docs", "review_mode": "manual", "description": STANDARDS_DESCRIPTION,
                       "include_standards": False}}


def _optimize_usage(project: str, values: dict) -> dict:
    # The usage pillar writes the task from the chosen findings and starts it on the project's queue.
    return {"type": "usage.optimize", "target_pillar": "usage", "project_id": project, "queue_key": None,
            "params": {"finding_ids": values["findings"]}}


def _apply_claude_config(project: str, values: dict) -> dict:
    return {"type": "registry.sync_claude_files", "target_pillar": "registry", "project_id": None, "queue_key": "claude-files-sync",
            "params": {"write": values["write"]}}


def _configure_claude_telemetry(project: str, values: dict) -> dict:
    return {"type": "usage.configure_claude", "target_pillar": "usage", "project_id": None, "queue_key": "usage",
            "params": {"apply": values["apply"]}}


_CONDITIONS = "Conditions — all must hold before the release branch is started"

CATALOG: tuple[JobSpec, ...] = (
    JobSpec(
        key="build", label="Build project",
        description="Build the project in a throwaway checkout of its current commit.",
        make=_build,
        args=(JobArg("install", "Install project after building", "flag",
                     help="Runs `bazel run //:install` once the build succeeds. A running server keeps the old code until you Relaunch it."),),
    ),
    JobSpec(
        key="run_tests", label="Run tests",
        description="Run the tests the chosen suites cover, and record the results under each suite.",
        make=_run_tests,
        args=(JobArg("suites", "Test suites", "multi", required=True,
                     help="Each suite's tests are found by listing the project's tests, without running them."),),
    ),
    JobSpec(
        key="release", label="Release",
        description=("Release the latest committed dev: check the conditions, start the release branch, write the "
                     "changelog, finish the release. A running app is relaunched once everything is done."),
        make=_release,
        args=(
            JobArg("min_delta", "Minimum delta (lines changed)", "number", default="0", group=_CONDITIONS,
                   help="Lines added plus lines removed on dev since the last release."),
            JobArg("combine", "How the two minimums combine", "choice", required=True, group=_CONDITIONS,
                   options=(("and", "Both minimums must hold (and)"), ("or", "Either minimum is enough (or)"))),
            JobArg("min_patch_bumps", "Minimum patch bumps", "number", default="0", group=_CONDITIONS,
                   help="Patch versions dev moved on since the last release — one per finished feature or improvement."),
            JobArg("all_tests", "All tests pass", "flag", group=_CONDITIONS,
                   help="Runs every test suite once, at dev's latest commit."),
            JobArg("all_requirements", "All requirements pass", "flag", group=_CONDITIONS,
                   help="Every approved requirement must pass on that same test run. Drafts are ignored."),
            JobArg("all_policies", "All policies pass", "flag", group=_CONDITIONS,
                   help="Runs every enabled policy that applies to the project."),
        ),
    ),
    JobSpec(
        key="update_standards", label=STANDARDS_TITLE,
        description=(f"Has Claude review every little-workshop standard, every policy and every project's own rules, then "
                     f"update the documents in {STANDARDS_FOLDER}/. Opens a branch for your review, like any task."),
        make=_update_standards, projects=(WORKSHOP_PROJECT,),
    ),
    JobSpec(
        key="optimize_usage", label="Optimize Claude usage",
        description=("Has Claude address the chosen Claude-usage findings in this project: a fixed task with the findings' "
                     "evidence, on a branch for your review. Each finding is measured again afterwards."),
        make=_optimize_usage,
        args=(JobArg("findings", "Findings to address", "multi", required=True,
                     help="The open findings of this project, ranked by what a fix is estimated to save."),),
    ),
    JobSpec(
        key="apply_claude_config", label="Apply common Claude configuration",
        description=("Compares every project's generated Claude files with little-workshop's templates. With the box "
                     "ticked, each project that differs gets them on an improvement branch of its own, committed and "
                     "left for you to finish; the branch finish runs claude-config-present and claude-instructions-valid."),
        make=_apply_claude_config, projects=(WORKSHOP_PROJECT,),
        args=(JobArg("write", "Write the files on a branch in each project", "flag",
                     help="Unticked, the job only reports which projects differ."),),
    ),
    JobSpec(
        key="configure_claude_telemetry", label="Configure Claude telemetry",
        description=("Shows what the Claude profile would change in ~/.claude: the telemetry environment, the session "
                     "hooks (the session title among them), the statusline, the transcript retention and the "
                     "little-workshop status mod."),
        make=_configure_claude_telemetry, projects=(WORKSHOP_PROJECT,),
        args=(JobArg("apply", "Write the profile", "flag",
                     help="Keeps a backup of the settings file and every entry of your own. Unticked: a dry run."),),
    ),
)

BY_KEY: dict[str, JobSpec] = {spec.key: spec for spec in CATALOG}


def parse(spec: JobSpec, form: Mapping[str, Sequence[str]]) -> tuple[dict | None, str | None]:
    """The spec's argument values from a submitted form (fields named `arg_<name>`)."""
    values: dict = {}
    for arg in spec.args:
        raw = [v.strip() for v in form.get(f"arg_{arg.name}", []) if v and v.strip()]
        if arg.kind == "flag":
            values[arg.name] = "on" in raw
        elif arg.kind == "number":
            text = raw[0] if raw else (arg.default or "0")
            if not text.isdigit():
                return None, f"{arg.label} must be a whole number, 0 or more."
            values[arg.name] = int(text)
        elif arg.kind == "choice":
            allowed = [value for value, _ in arg.options]
            chosen = raw[0] if raw else arg.default
            if chosen not in allowed:
                return None, f"Choose {arg.label.lower()}."
            values[arg.name] = chosen
        else:
            values[arg.name] = list(dict.fromkeys(raw))
            if arg.required and not values[arg.name]:
                return None, f"Choose at least one of: {arg.label.lower()}."
    return values, None


def build_job(project: str, job_key: str, form: Mapping[str, Sequence[str]]) -> tuple[dict | None, str | None]:
    """(job template, None), or (None, what is missing)."""
    if not project:
        return None, "Choose a project."
    spec = BY_KEY.get(job_key)
    if spec is None:
        return None, "Choose a job."
    if not spec.offered_for(project):
        return None, f"{spec.label} is not available for {project}."
    values, error = parse(spec, form)
    if error:
        return None, error
    return spec.make(project, values), None
