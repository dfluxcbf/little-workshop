"""Declared steps of the Build & Packages pillar's Job types.

The keys are the ones `src/build/runner.py` and `src/build/app.py` report. `build.delete_package`
is one action and keeps the default step."""

from __future__ import annotations

from common.job_steps import StepSpec

RUN = (
    StepSpec("provision", "Provision a clean checkout of the commit"),
    StepSpec("run_command", "Run the build command"),
    StepSpec("release", "Release the checkout"),
    StepSpec("save_result", "Save the build run and its log"),
)

PUBLISH = (
    StepSpec("check_input", "Check the package's name, version and file name"),
    StepSpec("check_build_run", "Check the build run succeeded and was not triggered from a fork"),
    StepSpec("store_file", "Store the package file"),
    StepSpec("register", "Register the package in the index"),
)

STEPS: dict[str, tuple[StepSpec, ...]] = {
    "build.run": RUN,
    "build.publish_pypi": PUBLISH,
    "build.publish_vsix": PUBLISH,
}
