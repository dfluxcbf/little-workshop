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
  rewrites the set in a registered project's checkout, keeping the entries the project added to its settings,
  and removes a file the generator no longer writes (`RETIRED_FILES`: the UserPromptSubmit reminder
  `newest_request_reminder.py` and its settings entry, REQ-000000478; the rule stays in the briefing). The
  lifecycle skill's steps before a finish are the preflight and the finish, with no separate DoD run
  (REQ-000000481).
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

## After a bootstrap or an import (2026-11-07)

- **Bootstrap interview (REQ-000000519).** `interview: true` (the New project page's checkbox,
  `lws project bootstrap --interview`, the MCP tool's `interview`) starts, once the project is scaffolded and
  versioned, an `agent.run_task` Job of intent `interview` with manual review (`interview_task`). Its prompt
  (`agent/templates/prompts/interview.md`) has Claude ask the owner one question at a time over Remote Control,
  through the `grill-me` skill when the toolkit installed it, and write `docs/design.md` from the answers;
  requirements only for `full` governance. The result carries `interview_job_id`, or `interview_error` when it
  could not start (the bootstrap still succeeds).
- **Import follow-up (REQ-000000520).** After registering, `project.import` submits a
  `registry.sync_claude_files` Job with `write: true` (the generated files on an improvement branch when they
  differ) and runs the `branch_finish` preflight through Policy & Compliance's evaluate (nothing held). Both
  land in the import Job's result as `sync` and `preflight` (blocking topics and each verdict); an unreachable
  pillar is an `error` there, never a failed import. The import's own validation stays mechanical.

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
  Orchestration, submitted without the project's queue key, which the asking Job already holds, and waited
  for until it settles: REQ-000000532), the files written and committed there (`impr (-1): claude-files-sync`), and nothing more:
  the branch is the owner's to finish, and its finish runs `claude-config-present` and
  `claude-instructions-valid`. A checkout with uncommitted changes, or whose branch cannot be started, is
  skipped and says so.

`.claude/settings.json` is merged: the generated permission table, hooks and plugins, plus every entry the
project added. The Dashboard's catalog job *Apply common Claude configuration* runs it.

Each generated hook command is built by `claude_templates.hook_command`: it runs its script only when the
checkout holds it, and otherwise prints that the hook did not run and exits 0, so an older branch checked
out never fails every tool call with a hook error (REQ-000000494). `POST /v1/projects/{name}/claude_files/
regenerated` (`claude_sync.regenerated`) answers what a checkout's generated files should be, with
`.claude/settings.json` keeping the project's own entries of every side given: the Version pillar settles a
finish's rebase that stopped on them with it (REQ-000000487).

The files follow the project's governance (below): the Job and `regenerated` pass the Registry's setting;
`tools/sync_claude_files.py` reads it from the Registry, or takes `--governance`, and falls back to `full` for
little-workshop and `light` otherwise. Every generated Markdown file ends with an empty project section,
`<!-- project -->` … `<!-- /project -->`: what a checkout holds there is carried into the rewritten file, and the
guard lets Claude edit only that part of a generated file (REQ-000000497). The sync writes only the generated
paths and removes only `RETIRED_FILES` (files it generated once): a project's own rules, skills and agents are
never touched. A rule a session learns (a correction, claude-reflect's learned rules) goes in the project's own
`.claude/rules/learned.md`, which has no `paths` and so loads in every session; the briefing, the instructions
rule and the guard's refusal of a `CLAUDE.md` say so (REQ-000000498). claude-reflect's `reflect-mod` writes its
"Learned rules" block to `<repo>/CLAUDE.md` or `~/.claude/CLAUDE.md`, a path fixed in its code
(`mod/hooks/register.tsx`, `fileFor`) with no setting, and it writes through the file system rather than Claude's
Edit tool, so the guard cannot stop it: move such a block into `.claude/rules/learned.md` and delete the
`CLAUDE.md` (the policy `claude-config-present` fails a finish while one exists).

## Governance (2026-11-07)

`projects.governance` (`light` | `full`, added to an existing database as `light`) says how much requirement work
little-workshop asks of a project (REQ-000000495). `light` runs only the policies every project needs (secrets,
credentials, version, release and Claude configuration checks); `full` adds those marked `"requires": "full"`
(the requirement-marker policies, `python-test-coverage`, `merge-commit-message-format`), the requirement
instructions in the generated Claude files and agent prompts, and the Definition of Done (REQ-000000496). Change
it with the `project.set_governance` Job: `lws project set-governance --project-id <id> light|full`, the MCP tool
`workshop_project_set_governance` (always asks), or the Dashboard's Settings page. Every project starts `light`;
little-workshop itself is `full` (`lws project set-governance --project-id little-workshop full`, once).

## Host (2026-11-07)

`projects.host` (`wsl` | `windows`) says where a project's code and tools run (REQ-000000509): `windows` for a
checkout on a Windows drive under WSL's mount root. Bootstrap and import set it from the path; an existing
database gets it from each row's path. Everything that asks reads it through `common.windows.project_host`
(docs/architecture.md, "Windows-hosted projects"). For a `windows` project the Registry also (REQ-000000511)
runs its git with `git.exe`, pins `core.autocrlf=false` at bootstrap, import and the Claude files sync, and
gives a non-Unity project `* text=auto eol=lf` as the first `.gitattributes` rule (scaffolded at bootstrap,
added on the sync's branch otherwise); the `registry.normalize_eol` Job (`lws project normalize-eol
--project-id <id>`, always asks) does the same on an improvement branch and renormalizes every tracked file
once. Its generated hooks run through `.claude/hooks/run_hook.sh` and its `.mcp.json` asset server through
`cmd.exe /c lws.cmd`, so both work in WSL and in native Windows sessions (REQ-000000513).

## Toolkit (2026-11-07)

Public Claude Code skills, agents, commands and plugins, installed per project (`registry/toolkit.py`,
REQ-000000502 to REQ-000000506). Suggested sources and their licences: [../toolkit_sources.md](../toolkit_sources.md).

- **Sources**: a GitHub repository registered once (`toolkit_sources` table: repo, pinned commit, licence,
  default branch, items) by `registry.toolkit_add_source` (`repo`, or `seed: true` for the defaults:
  mattpocock/skills, DietrichGebert/ponytail, BayramAnnakov/claude-reflect, pbakaus/impeccable). The tree is read
  through `gh api` at the pinned commit: plugins from `.claude-plugin/marketplace.json` (local sources only),
  skills from `**/SKILL.md` (other harnesses' copies under hidden folders and test data skipped; a
  `.claude/skills/` copy wins), agents and commands from `agents/*.md` and `commands/*.md` (root or `.claude/`).
  Each item lists its description (frontmatter or `plugin.json`) and `runs_code`: its hooks, scripts and
  executables.
- **Install** (`registry.toolkit_install`: project, `selections` — `[{repo, items}]`, any number of sources — or
  the single `repo` and `items`, and `confirm_code`): a plugin through Claude Code's own `claude plugin
  marketplace add <repo>` and `claude plugin install <name>@<marketplace> --scope project` in the checkout (which
  writes `enabledPlugins`); a skill, agent or command copied at the pinned commit to `.claude/skills/<name>/`,
  `.claude/agents/<name>.md`, `.claude/commands/<name>.md`. A file the lock does not record is never overwritten.
  An item that runs code is refused, its files listed, without `confirm_code` (a selection is refused whole,
  before anything is written). Everything is recorded in `.claude/toolkit.lock.json` (per source: commit,
  licence, items and the files written).
- **One commit on the integration branch, no branch (REQ-000000533)**: every toolkit change (install, remove,
  update) is one commit straight on the project's integration branch (`.lvx/config.json`, `dev` by default),
  with subject `impr (-1): toolkit-<item|repo|n-items>` (`claude_sync.commit_on_integration`). Where that branch
  is checked out (the project's checkout or a worktree) the files are written there and only the paths written
  are committed, so other uncommitted work stays out of it; refused while `.claude/` has uncommitted changes
  there. When the checkout is on another branch, the commit is made in a temporary worktree of the integration
  branch under the git directory (`lws-worktrees/`), removed afterwards: the checkout is not switched or touched
  and gets the change when it takes the integration branch in. A failure leaves `.claude/` as it was and
  commits nothing. The status (`/v1/toolkit/status/{project}`) reads the integration branch's lock.
- **Remove** (`registry.toolkit_remove`; no items: all from the source) deletes exactly the recorded files, or
  uninstalls the plugin. **Update** (`registry.toolkit_update`) re-pins the source to its newest commit and lists
  the changed files; with a project, it brings the project's items to the new pin (from the project's own pin;
  changed code needs `confirm_code`). Plugins themselves are updated by Claude Code.
- **Settings**: the generated `.claude/settings.json` enables the language plugins plus every `plugin@marketplace`
  the lock records, and keeps the project's own entries.
- **Recommendations, never installs** (`GET /v1/toolkit/status/{project}`): the lock, plus the registered items
  suggested from the project's kind (code projects: grill-me, grill-with-docs, to-spec, implement-spec,
  diagnosing-bugs, tdd, handoff, writing-for-agents and ponytail's review, audit and debt skills; a project with a
  UI, a server manifest or `tests/ui/`: impeccable; Unity: none) and its open usage findings (`FINDING_ITEMS`:
  long sessions → handoff, files re-read in many sessions → writing-for-agents). claude-reflect is a source only:
  it is installed at user scope already, and writes learned rules to `.claude/rules/learned.md`.
- **Surfaces**: Dashboard System → Toolkit (`/toolkit`: sources with licence and items, a "runs code" badge, a
  project picker, a checkbox per item (recommended ones and every source's, "Select all recommended") and one
  "Install selected" for the whole selection, which asks once about the code the selection runs, and the
  installed items with remove and update); `lws toolkit install --also <repo> <item>...` adds another source's
  items to the same install; `lws toolkit
  sources|list|status|add-source|seed|install|remove|update`; MCP `workshop_toolkit_sources`, `_items`,
  `_status` (read-only) and `_add_source`, `_install`, `_remove`, `_update` (ask).

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
- `registry.backlog_task_prompt` — from the refined entry (not the primary entry or the notes); nothing
  about the backlog is added: the task never closes or deletes its entry (REQ-000000524).

**Status and priority (REQ-000000524).** Each entry has a `status` (`open`, `done`, `dropped`) and a
`priority` (`high`, `normal`, `low`), both the owner's; entries from before them are `open`/`normal`.
`PATCH /v1/backlog/{id}` sets them (`lws project backlog update --status --priority`,
`workshop_project_backlog_update`); `GET /v1/backlog?status=` filters (`lws project backlog list --status`,
default open) and lists the highest priority first. `POST /v1/backlog/{id}/task` records the Job of the task
launched from the entry (`task_job`), which the Dashboard uses to offer "Mark done" once that run succeeded.

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
backlog delete` or `workshop_project_backlog_delete` — removes the row with
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
