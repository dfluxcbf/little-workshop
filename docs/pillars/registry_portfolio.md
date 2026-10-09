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
  `git init`, `dev` branch, optional GitHub repo creation and push,
  optional GitHub label creation (`registry/github.py`'s
  `BOOTSTRAP_LABELS`, guarded by `BootstrapParams.ensure_labels`), and an
  optional real cross-pillar `version.init` call into Version Control &
  Release (M16 `bootstrap-little-project` parity gap:
  `registry/version_client.py`, best-effort — reported back via
  `BootstrapResult.version_initialized`, never raised if that pillar is
  unreachable), then register. `project.import` (`POST /v1/jobs/project.import`, M8) — for
  an *existing*, already-checked-out repository: deliberately
  mechanical, validating only that there's something real to register
  (the path exists, is a git repository), reading its real `origin`
  remote if one exists, never scaffolding or judging policy conformance.
  That judgment belongs to whoever drives the import (a human Claude Code
  session, or Agentic Development's own dispatch — reaching this pillar
  through the identical `workshop_project_import` MCP tool either way)
  and, later, to Policy & Compliance (M12).
- **Claude Code files in a new project** (REQ-000000293..298):
  `project.bootstrap` also writes, for both templates (`registry/claude_templates.py`),
  `.claude/settings.json`, `.claude/hooks/lws_scope_guard.py` and `.claude/AGENTS.md`, and no
  `CLAUDE.md`: Claude Code reads `AGENTS.md` only where a project has no `CLAUDE.md` or `CLAUDE.local.md`,
  so the unity layout's working rules moved from its root `CLAUDE.md` into `.claude/AGENTS.md`. A
  registered project may keep its briefing as a root `AGENTS.md` instead, which Claude Code reads the same
  way (ethereal-lands does; `claude-config-present` accepts either). `.claude/settings.local.json` and
  `.claude/worktrees/` are gitignored. The settings pre-allow the read-only, routine and policy-gated
  (finish, release, deploy, publish; the policy gate is their check) `little-workshop` MCP tools and `lws`
  commands, with every `lws` command that takes `--project-id` bound to the new project's own id; make the
  tools and commands that stand in for a human decision, register or remove a project, or administer
  little-workshop (policy confirm, agent finish/skip, scheduling, bootstrap/import, policy authoring) ask; and
  deny the raw `git merge/tag/push/cherry-pick`, `systemd-run`/`systemctl --user`, `twine` and `gh` forms of
  what a policy gates, so those go through the MCP server or `lws`. A permission rule cannot match an MCP tool's
  arguments, so the PreToolUse hook `lws_scope_guard.py` (registered for Bash and every `little-workshop` tool)
  denies a call that names another project, or leaves `project_id` out of a tool where that means every
  project. The project name becomes that id inside rules and a shell command, so `project.bootstrap` rejects a
  name that is not a usable id (letters, digits, `.`, `_`, `-`). little-workshop's own `.claude/settings.json`
  is the same table bound to `little-workshop`, plus its platform administration, held equal by a unit test;
  the `little-projects` super-repository, whose sessions manage every project, allows all of it instead.
  Since 2026-10-02 the generated set (`claude_files()`) also holds `.claude/little-workshop.md` (the briefing
  section every project shares, imported by its `AGENTS.md`), the path-scoped rule
  `.claude/rules/claude-instructions.md` and the `workshop-lifecycle` skill, and the guard also runs for the
  file-edit tools: it refuses the denied raw commands with the route to take, and hand edits of generated
  files or a `CLAUDE.md` (`docs/pillars/policy_compliance.md`, "the second pass"). `tools/sync_claude_files.py`
  rewrites the set in a registered project's checkout, keeping the entries the project added to its settings.
- **`project.connect_github`** (`POST /v1/jobs/project.connect_github`,
  REQ-000000279..282, 2026-09-25) — the GitHub half of `project.bootstrap`
  for a project that is *already registered*, so a missing repo, remote,
  labels or `repo_url` never needs a remove + re-import (which would
  cascade-purge every pillar's data). No `origin` remote: create the repo
  (token, else `gh`), add `origin`, push `master`/`dev`. An `origin`
  already there: adopt it, never create or push. Then ensure
  `BOOTSTRAP_LABELS` and record `repo_url` (normalised to
  `https://host/owner/name`) in the row and `info.registry.json`.
  Idempotent. `workshop_project_connect_github` / `lws project connect-github`.
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
anything about the project itself. As of M16, it does make one
best-effort outbound call of its own — `project.bootstrap` calling
Version Control & Release's `version.init` — made *after*, never inside,
`job_bootstrap`'s own lock (Version's own callback into this pillar's
`GET /v1/projects/{name}`, via `common.registry_client`, would deadlock
against that same lock otherwise).

- **Called by**: Version Control & Release, Deployment & Servers, and
  Agentic Development, all through the identical `common.registry_client`
  helper (`resolve_project`/`resolve_project_path`) — discovered through
  Core, never hardcoded — every time they need a `project_id`'s real
  on-disk path (or, for Agentic Development, its `repo_url` too, to know
  whether/where to open a pull request). `lws`, the MCP Server, and
  Orchestration (dispatching `project.bootstrap`/`project.import`/
  `project.remove` Jobs) also call it directly.
- **Calls**: `project.bootstrap` shells out to `git`/GitHub's REST API/`gh`
  directly (`registry/gitops.py`, `registry/github.py`), not through
  another pillar. As of M16, it also makes one real cross-pillar Pillar
  Interface call of its own: `version_client.init_version_tracking`
  resolves Version Control & Release's socket through Core and posts a
  `version.init` Job, best-effort (never raised if unreachable).
- **Discovered through**: Core, like every pillar. Its socket is one of
  Core's very first registry entries, since Version Control & Release
  and Deployment & Servers both need it resolvable from the moment they
  themselves start.

## Secrets and token validity (2026-09-22)

`GET /v1/secrets` — System health's own secrets panel (the Dashboard's Pillars page).
Today, one entry: the host's `gh` CLI credential (`registry/github.py`'s
`github_token_status`) — `gh auth status`'s own exit code for valid/invalid, and, only
when the credential carries one, its expiration date read from GitHub's own
`github-authentication-token-expiration` response header on a real `gh api /user` call.
The token itself is never read, stored, or returned — only whether it authenticates and
when it expires.

## Claude files sync (2026-10-04)

REQ-000000338. The generated Claude files of a project (`registry/claude_templates.py`) are compared with a
checkout and written by `registry/claude_sync.py`; `tools/sync_claude_files.py` is its command line. The Job
`registry.sync_claude_files` does the same for every registered project (or the one in `project_id`):

- Without `params.write` it reports, per project, which files differ.
- With `write: true`, each project that differs and has a clean tree gets an improvement branch
  `improvement/claude-files-sync` from the Version pillar (a `version.improvement_start` Job through
  Orchestration), the files written and committed there (`impr (-1): claude-files-sync`), and nothing more:
  the branch is the owner's to finish, and its finish runs `claude-config-present` and
  `claude-instructions-valid`. A checkout with uncommitted changes, or whose branch cannot be started, is
  skipped and says so.

`.claude/settings.json` is merged: the generated permission table, hooks and plugins, plus every entry the
project added. The Dashboard's catalog job *Apply common Claude configuration* runs it.

## Backlog (2026-10-04)

REQ-000000369..371. Each registered project has a backlog: what its owner wants built or fixed, kept
before it becomes a task (`registry/backlog.py`, table `backlog_entries`). An entry holds:

- the **primary entry**, as the owner first wrote it (editable);
- the owner's **refinement notes** (editable);
- one **refined entry**, written by Claude and replaced at every refinement;
- one **task prompt**, written by Claude and replaced at every generation;

with when each changed. Reads and edits are direct calls: `GET/POST /v1/backlog` (`project_name`),
`GET/PATCH/DELETE /v1/backlog/{id}`, and `GET /v1/backlog/version`, which changes with every addition,
edit and deletion (the Dashboard's live pages watch it). Two Jobs ask Claude, through Agentic
Development's `agent.query` (this pillar runs no Claude itself), without holding the pillar's lock:

- `registry.backlog_refine` — from the primary entry, the current refined entry and the notes;
- `registry.backlog_task_prompt` — from the refined entry (not the primary entry or the notes); the
  instruction to delete the entry once the work is done (`lws project backlog delete --project-id <p>
  <id>`, or the MCP tool `workshop_project_backlog_delete`) is appended here, word for word.

Since 2026-10-05 both read the project's checkout as Claude writes (REQ-000000370, 371, 397): the query
asks for `read_checkout`, so the agent runs Claude in the checkout with only the tools that read
(`docs/pillars/agentic_development.md`, "A query that reads a project's checkout"). The refined entry says how that
part of the project works today, naming its files, and settles from the code what the code answers; the
task prompt names the files to change and the tests to add or run. The requirements still come from the
owner alone: what Claude takes from the code says where and how, never what is wanted. Each Job waits
for the longer query (600 s), and Orchestration for each Job (630 s). Before, Claude had only the
entry's text, so a refined entry could not say where a change went and asked the owner what the code
would have answered.

Each is refused for a missing entry (and the prompt for an entry not refined yet), and an entry deleted
while Claude works keeps nothing of the answer. Deleting an entry — from the Dashboard, `lws project
backlog delete`, `workshop_project_backlog_delete`, or Claude at the end of a task — removes the row with
all four fields; the database overwrites deleted content (`secure_delete`) and folds its write-ahead log
back, so no copy outlives it in the pillar's files. Removing a project removes its backlog. The Claude
sessions that refined or prompted an entry are recorded at the agent under `backlog:<id>`; deleting the
entry has the agent delete their transcripts too (`POST /v1/query-sessions/forget`, REQ-000000387), and
the answer says how many (`transcripts_deleted`, None when the agent could not be reached).

An entry also records what Claude is doing for it (REQ-000000385): `refining_since` / `refining_job` and
`prompting_since` / `prompting_job` while a Job asks Claude, cleared when it ends, and `refine_error` /
`prompt_error`, why the last attempt failed, cleared by the next one. Writing them is not an edit of the
entry (its update time stays), and the Dashboard's backlog pages read them instead of Jobs.

`lws project backlog list|show|add|delete --project-id <p>` and `workshop_project_backlog_list|get|add|delete`
act only on the named project's backlog (an entry of another project is refused). Reading and adding are
pre-allowed in a project's session; deleting asks, being irreversible.

## Asset review configuration (2026-10-09)

`projects.assets_dir` (nullable, added to an existing database on start) is the checkout folder whose assets the
Dashboard shows; the `project.configure_assets` Job (`assets_dir`, `mcp`) validates it lies inside the checkout,
records it and merges the `asset-review` server into the checkout's `.mcp.json` (an empty folder removes it). The
whole feature is described in `dashboard.md` ("Assets"). REQ-000000426.
