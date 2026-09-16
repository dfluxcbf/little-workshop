# Registry & Portfolio

## Description

Registry & Portfolio is this system's source of truth for "what projects
exist." A `projects` row is what "this project is registered" actually
means — the `~/.config/little-workshop/projects/<name>/` directory every
pillar's project-scoped files live under is a *consequence* of that row,
never a parallel source of truth (`docs/architecture.md`'s Data model
boundary note). It owns the registration itself, not everything about a
project: a project's git history lives in the project's own repository,
its Jobs live in Orchestration, its branches in Version Control &
Release — Registry & Portfolio's row is the one thing every other pillar
agrees a `project_id` actually refers to.

Absorbs `bootstrap-little-project` wholesale, `little-projects`' informal
registry role, `configure_submodules.sh`, and `little-tickets`
(previously undefined-purpose) as `tickets`.

## Purpose & goal

"There is always one true answer to 'what projects exist, and what's the
state of the work tracked against them'" (`docs/architecture.md`). Built
first among the non-substrate pillars (M2) because every other pillar
that operates on a specific project — Version Control & Release,
Deployment & Servers, Agentic Development — needs somewhere to resolve
`project_id` into a real repository path before it can do anything.

## Responsibilities

- **`projects`** — `name` (primary key, the same string used as
  `Job.project_id` and the project-files directory name), `path`,
  `repo_url`, `status` (`active`/`removed`), timestamps.
- **Two registration paths, one outcome**: `project.bootstrap`
  (`POST /v1/jobs/project.bootstrap`, M2) — deterministic: scaffold,
  `git init`, `dev` branch, optional GitHub repo creation and push, then
  register. `project.import` (`POST /v1/jobs/project.import`, M8) — for
  an *existing*, already-checked-out repository: deliberately
  mechanical, validating only that there's something real to register
  (the path exists, is a git repository), reading its real `origin`
  remote if one exists, never scaffolding or judging policy conformance.
  That judgment belongs to whoever drives the import (a human Claude Code
  session, or Agentic Development's own dispatch — reaching this pillar
  through the identical `workshop_project_import` MCP tool either way)
  and, later, to Policy & Compliance (M12).
- **`project.remove`** — hard delete, immediate, cascading, no soft-delete,
  no undo (resolved 2026-09-15): purges this pillar's own `projects` row
  and project-files directory. The actual git repository on disk is left
  untouched — the user's code, not this pillar's data. As of M8, purging
  is only implemented for Registry & Portfolio's own rows; the
  architecture's "removal triggers every other pillar to purge its own
  data" is each *other* pillar's own future milestone to implement, per
  `registry/removal.py`'s own docstring — not retrofitted here.
- **`project_sets`** — named groupings of projects, moved here from
  Policy & Compliance's original design since grouping is a registry
  concern even though Policy & Compliance remains its only planned
  consumer.
- **`tickets`** — portfolio/ecosystem-level tracking per project,
  including for projects with no GitHub remote (which can't participate
  in an issue-driven workflow at all).

## Interfacing

Registry & Portfolio is a leaf in the call graph for reads: many pillars
call *into* it to resolve a project; it never calls back out to resolve
anything about the project itself.

- **Called by**: Version Control & Release, Deployment & Servers, and
  Agentic Development, all through the identical `common.registry_client`
  helper (`resolve_project`/`resolve_project_path`) — discovered through
  Core, never hardcoded — every time they need a `project_id`'s real
  on-disk path (or, for Agentic Development, its `repo_url` too, to know
  whether/where to open a pull request). `lws`, the MCP Server, and
  Orchestration (dispatching `project.bootstrap`/`project.import`/
  `project.remove` Jobs) also call it directly.
- **Calls**: nothing, as of M8 — no other pillar's Pillar Interface.
  `project.bootstrap` shells out to `git`/GitHub's REST API/`gh` directly
  (`registry/gitops.py`, `registry/github.py`), not through another
  pillar.
- **Discovered through**: Core, like every pillar. Its socket is one of
  Core's very first registry entries, since Version Control & Release
  and Deployment & Servers both need it resolvable from the moment they
  themselves start.
