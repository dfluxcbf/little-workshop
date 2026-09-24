"""The jobs a person can start from the Dashboard's New Job and Schedule job
pages: pick a project, pick one of these jobs, fill in its arguments.

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


def _build(project: str, values: dict) -> dict:
    steps = [BUILD_COMMAND, INSTALL_COMMAND] if values["install"] else [BUILD_COMMAND]
    # A login shell, so the build sees the same PATH (bazel, pipx) a terminal does.
    command = ["bash", "-lc", " && ".join(shlex.join(step) for step in steps)]
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
    values, error = parse(spec, form)
    if error:
        return None, error
    return spec.make(project, values), None
