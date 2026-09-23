# Build & Packages

## Description

Build & Packages makes real a non-goal `little-runner` explicitly
disclaimed ("not a general CI system"): build/test Job execution via
Deployment & Servers' worktrees (the same shared `common/worktree_client.py`
Requirements & Quality's `run_tests` uses), with fork-safety-gated PR
testing. It also absorbs `little-pypi` wholesale for the package-hosting
half: a real PEP 503 Simple Repository API and `.vsix` hosting, always-on
now that little-workshop is already a persistent daemon — package
*metadata* lives in this pillar's own `project_id`-scoped database
(a deliberate modernization of `little-pypi`'s pure-filesystem/no-DB
model, consistent with every other pillar's own convention), while
artifact *blobs* stay on disk, `little-pypi`'s own shape unchanged.

This is the only pillar optional in the same way Agentic Development and
Dashboard are — a project with no build/publish step never starts it
(`DEFAULT_ENABLED["build"] = False`).

## Purpose & goal

"A build artifact exists, is reproducible, and is reachable by anything
that needs to install it" (`docs/architecture.md`).

## Responsibilities

- **`build.run`** — provisions an isolated worktree of a given
  `commitish` through Deployment & Servers' real Pillar Interface, runs
  the project's own arbitrary build/test command inside it (`command:
  list[str]` — no assumption of `pytest`/Bazel/npm/anything else one
  pillar can't know in advance), and records a `BuildRun` (`status`,
  `exit_code`, combined stdout+stderr `log`, timestamps) — never
  touching the project's own working checkout, and always releasing the
  worktree again, success or failure.
- **Fork-safety-gated PR testing** — `build.run` accepts `from_fork:
  bool` (default `false`), persisted on the `BuildRun` it creates.
  `build.publish_pypi`/`build.publish_vsix` require a real
  `build_run_id` and are refused whenever that run's stored `from_fork`
  is `true` or its `status` isn't `succeeded` — checked against this
  pillar's own record of the run, not a caller-supplied claim, so a
  fabricated flag on the publish call itself can't bypass the gate. An
  untrusted PR's build can still run (unlike `little-runner`'s own
  outright refusal to even check it out) but can never turn into a
  publish.
- **Package hosting** — a real PEP 503 Simple Repository API
  (`build/pep503.py`): name normalization (`-`/`_`/`.` runs collapse to
  one `-`, lowercased), `GET /simple/` (every published project),
  `GET /simple/{name}/` (every file for one project, 404 if
  unpublished), `GET /packages/{filename}` (the real blob) — computed
  live from the database on every request, never regenerated-to-disk
  index files the way a pure-filesystem tool needs. `.vsix` (VS Code
  extension) hosting is its own namespace, deliberately never part of
  the PEP 503 index (`GET /vsix/{name}`, `GET /vsix/{name}/{version}`),
  matching `little-pypi`'s own real behavior.
- **`build.delete_package`** — removes the database row and the on-disk
  blob together, always both, never one without the other.
- **`lws build print-pip-config`/`write-pip-config`/`pip`/
  `print-vsix-urls`** — added 2026-09-17, closing M16's `little-pypi`
  parity gap. Pure CLI-side dev conveniences, not new pillar mechanism
  (the first three never call this pillar's PI at all; the fourth is a
  plain `GET /v1/packages?kind=vsix` read): a pip.conf snippet, a real
  `pip install --extra-index-url` shell-out, and vsix direct-download
  URLs. All four assume a real HTTP reverse proxy already sits in front
  of this pillar's own Unix-socket-only Pillar Interface at the given
  `--host`/`--port` — the same deployment-time-concern split
  `docs/pillars/dashboard.md`'s own browser-exposure section already
  documents; nothing here builds that proxy. **Deliberately not
  closed**: `little-pypi`'s own `publish`/`upload` auto-detect a
  wheel's project/version from its filename; `build.publish_pypi` still
  requires them as explicit params — an ergonomic gap, not a functional
  one (the capability exists, just not the auto-detection).

## Interfacing

- **Calls**: Deployment & Servers, for worktree provisioning during
  `build.run`.
- **Called by**: `lws build ...`, the `workshop_build_*` MCP tools
  (publish commands base64-encode a real local file's bytes into the
  Job's own `params` — the same "everything is a `POST /v1/jobs/<type>`
  JSON body" convention every other mutation in this system already
  uses, no separate multipart-upload machinery invented for this pillar
  alone), and Orchestration for every Job type above — including a
  recurring `ScheduledTask` (M9) for periodic builds, no different from
  any other caller of `build.run`.
- **Discovered through**: Core, like every pillar.
