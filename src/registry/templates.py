"""Doc/build-file templates for a project `project.bootstrap` scaffolds.

Deliberately NOT a verbatim port of `bootstrap-little-project`'s
templates: those point at the root `little-requirements`/`little-versions`
policies and run `lvx init` — exactly the dependency this project exists
to replace. A project little-workshop bootstraps is little-workshop's
own from birth: no `requirements_policy.md`/`version_policy.md`
policy-pointer docs (there's no external policy file to point to —
Requirements & Quality/Version Control & Release pillars own that,
addressed by `project_id`, once they exist), no `.lvx/config.json`.
Everything else (docs/design.md, docs/architecture.md, docs/milestones.md,
README, .gitignore, VERSION, MODULE.bazel, BUILD.bazel, src/, tests/)
keeps the same recognizable shape as every sibling project.
"""

from __future__ import annotations


def design_md(name: str) -> str:
    return f"""# Design

## Purpose

TBD, purpose not yet defined for `{name}`.

## Goals

TBD.

## Non-goals

TBD.

## Core concepts

TBD.

## Data flow

TBD.
"""


def architecture_md(name: str) -> str:
    return f"""# Architecture

## Components

TBD, no components implemented yet for `{name}`.

## Technical decisions

TBD.

## Dependencies

TBD.
"""


def milestones_md() -> str:
    return """# Milestones

No milestones defined yet.

| ID | Objective | Requirements | Status |
|---|---|---|---|
"""


def readme_md(name: str) -> str:
    return f"""# {name}

TBD, purpose not yet defined.

See `docs/design.md` for details as they're filled in.

Managed by little-workshop's Registry & Portfolio pillar.
"""


def gitignore() -> str:
    return """.venv/
venv/
__pycache__/
*.pyc
.pytest_cache/
bazel-*
MODULE.bazel.lock
.review/
.claude/settings.local.json
.claude/worktrees/
"""


def module_bazel(module_name: str) -> str:
    return f"""module(
    name = "{module_name}",
    version = "0.1.0",
)
"""


def build_bazel() -> str:
    return """package(default_visibility = ["//visibility:public"])

# No build artifacts defined yet.
"""


def version_file() -> str:
    return "0.1.0\n"


PLACEHOLDER_FILENAME = ".gitkeep"
