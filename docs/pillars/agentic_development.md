# Agentic Development — pillar specification

Written during a final sweep of every project in this ecosystem for
content worth importing — this pillar absorbs the most of any single
source (`little-runner`'s `issue_flow.py`/`branching.py`/`report.py`/
`screenshots.py`/`sessions.py`, plus the ecosystem-level design in the
root `docs/features/github_integration.md`), and almost none of that
mechanism needed inventing here — it needed writing down at this pillar's
level of detail, the way `docs/pillars/policy_compliance.md` already is
for its pillar. See `docs/design.md`/`docs/architecture.md` for this
pillar's place in the whole system; this document is everything specific
to it.

## Description

Agentic Development is the pillar that runs a real Claude CLI session
against a real branch of a real project, under a set of safety
guarantees (branch isolation, a mandatory completion marker, a fixed
change-report shape, one session per project at a time) proven by
`little-runner` before this pillar existed. Implemented in M6, and one
of only two pillars (alongside Policy & Compliance) with a full design
specification of its own, separate from `docs/architecture.md`'s
Components-table summary.

## Purpose & goal

"Claude does real implementation work on a real branch, safely, and the
human stays in control of when it's finished" — unchanged from
`docs/architecture.md`. What this document adds is *how*, inherited
almost verbatim from `little-runner`'s own proven mechanism, generalized
from GitHub-issue-only triggers to any Job source.

## Responsibilities

- **`agent.run_task`** — resolves a `BranchingSpec` (`new`/`existing`/
  `auto`) against Version Control & Release's live branch-type policy,
  prepares the branch, runs the agent session (a real `claude -p`
  invocation), parses its completion marker and structured result file,
  publishes (push + PR, when the project has a real `origin`), and
  renders the seven-section change report. Runs in a background thread —
  a real session can take up to an hour — reporting `accepted` to
  Orchestration immediately and completing later via
  `POST /v1/jobs/{id}/complete`.
- **`agent.finish_task` / `agent.skip_task`** — the explicit actions that
  settle a manual-review run awaiting a human decision.
- **`agent_runs`** — this pillar's own table of every run and its state
  (`running`/`awaiting_finish`/`finished`/`failed`/`skipped`), reachable
  via `GET /v1/runs/{project_id}` and `GET /v1/runs/{project_id}/{run_id}`.
- **The GitHub adapter and poller** — optional, per-project: actor lookup
  via the issue timeline and cursor-based edge detection from polling
  (detailed below), for projects that want issue-triggered runs.
- Full detail on the `BranchingSpec`, the completion marker, review
  modes, the change report, concurrency, and the GitHub adapter is in the
  sections below — this pillar's actual design content, not summarized
  twice.

## Interfacing

- **Calls**: Version Control & Release — `new`-mode branch creation
  calls that pillar's own `version.<type>_start` Job directly over its
  Pillar Interface (a synchronous subroutine call, not routed through
  Orchestration a second time), and every branching mode validates
  against that pillar's live `GET /v1/config`. Registry & Portfolio —
  resolves a `project_id`'s repository path and `repo_url` via
  `common.registry_client`. Orchestration — reports its own async
  completion back via `POST /v1/jobs/{id}/complete`, the same pattern
  M5's self-redeploy worker established first.
- **Called by**: `lws`, the MCP Server, and Orchestration for
  `agent.run_task`/`agent.finish_task`/`agent.skip_task`. Policy &
  Compliance (M12) is planned to call into this pillar for every
  EARS-refinement, conflict-check, and test-script-generation step of
  its own four-gate policy-authoring pipeline — this pillar's `agent.run_task`
  is the one mechanism that pipeline is built on top of, not a new one.
- **Discovered through**: Core, like every pillar — including for
  resolving Orchestration's own socket from inside this pillar's
  background completion thread.

## Not every Job goes through this pillar

Worth stating explicitly, because the root `docs/features/`
documentation makes the same distinction on purpose: **issues are
deliberately not the interface for every kind of action.** Server
management (`server.launch`/`server.deploy`) is "a direct action with
typed inputs... not something that needs a change report or review" —
its own root feature doc says so outright. Only work that's genuinely
*implementation* — something with judgment calls, multiple reasonable
approaches, a diff worth reviewing — goes through this pillar's full
`agent.run_task` treatment. A typed, deterministic action with one
correct outcome (bring a server up, register a project) dispatches
directly to whichever pillar owns it, no agent involved, no report
produced, no review needed. Getting this distinction wrong in either
direction is a real cost: routing simple actions through an agent adds
latency and an unreviewable-feeling black box; routing real judgment
calls around the agent removes the review this pillar exists to
guarantee.

## The BranchingSpec

A Job specifies branching as one of three modes, exactly as
`little-runner`'s issue flow already does:

- **`new`** — a branch type (feature/improvement/hotfix) and a base.
  `lvx <type> start` is run *before* the agent starts — the base is
  validated against that type's actual allowed base (feature/improvement
  must start from the integration branch, hotfix from production), so a
  mismatched pair is rejected up front with a clear reason instead of
  failing midway through.
- **`existing`** — a branch name, checked out (creating a local tracking
  branch if it only exists on the remote) before the agent starts.
- **`auto`** — the checkout stays on the integration branch and the agent
  chooses and creates its own branch, reporting what it picked. The
  fallback for anything unset or unreadable — never an error.

**The agent never touches branches when `new` or `existing` is
specified** — the branch is already checked out before the agent starts,
and the prompt carries a hard rule forbidding `lvx`/`git checkout`/
`git branch`/`git merge`. This is the hard-coded invariant already named
in `docs/pillars/policy_compliance.md`'s "three tiers of rules" — restated
here because this is the pillar whose code actually enforces it.

## The completion marker

The agent's prompt requires a final status line — `done`, `partial`, or
`failed` — the same three-way signal `little-runner` already proved
(`LRUN_STATUS:` there). A missing or unreadable marker degrades to
`partial`, **never** to `done` — an ambiguous run can never trigger an
automatic finish. This is what "did it actually work" resolves to
mechanically; nothing here is inferred from the agent's prose summary.

## Review: two modes, both ending in a human decision

- **Manual (default)** — the branch is pushed, a PR opened, the Job
  reports awaiting-finish. Finishing later is an explicit action (a
  dashboard button, an MCP call, a comment-equivalent) — never automatic.
- **Auto-approve** — a Job-level parameter, not a policy: the full report
  is still produced, but the branch is finished automatically once the
  agent reports `done` *and* its recorded test run didn't fail. Refused
  outright otherwise. Auto-approve removes the round trip, never the
  evidence — the report is identical either way.

Both modes always produce the same artifact:

## The change report

A fixed, seven-section structure — Background, Goal, Technical decisions,
Executed tasks, Screenshot (server projects — the branch installed and
run on a scratch port, captured, never disturbing whatever's actually
serving traffic), Test validation results, Pull request link — posted as
both the triggering issue/ticket's comment and the PR body, so code
review starts from the same explanation a human reads first. The agent
writes this as a structured result file at a path *outside* the
repository (so it can never be committed by accident), not parsed from
free-form prose — a missing or malformed file degrades to a report built
from the agent's own summary, never to a failed run, since the work is
already done by the time this step runs.

## Concurrency: one task at a time per project

The same `queue_key` mutex Orchestration already provides for every Job
type — restated here because this is the specific case it was designed
around: a project never has two agent sessions on two branches at once;
a second trigger queues and starts the moment the first is done, skipped,
or failed. Different projects run fully in parallel.

## Watching a run: Remote Control

Every `agent.run_task` Job runs under Claude Code's Remote Control,
named for the project and task so it's identifiable in the Claude app —
this is the same mechanism `docs/pillars/policy_compliance.md`'s OQ-9
resolution reuses for a second purpose (delivering a time-sensitive
notification), not a coincidence: it's this pillar's own proven
capability, called from elsewhere exactly the way "a new capability maps
to an existing pillar's interface" already requires. The agent also gets
the user-messaging tool, so it can ask a genuine question mid-run — which
plan to take, which of two designs to use — instead of guessing.

## GitHub adapter: two techniques worth keeping exactly as proven

Only relevant when a project has the GitHub adapter attached (optional,
per `docs/design.md`'s Adapter concept). Two specific, non-obvious
techniques `little-runner` already had to solve, worth inheriting rather
than rediscovering:

- **Actor lookup via the issue timeline, not current state.** Polling
  only ever sees *current* labels/assignment, never *who* applied them —
  so authorization (is this sender the owner or an allowed actor) needs a
  timeline lookup (`GET /repos/{repo}/issues/{number}/events`) to find the
  real actor before dispatching anything.
- **Cursor files turn polling into edge detection.** A poll only sees a
  snapshot; the *new-since-last-time* edges a webhook would have
  delivered are reconstructed by diffing each tick's snapshot against a
  small per-project cursor file. This is the mechanism behind "no inbound
  ports, no stored token" — outbound-only polling can still behave like
  event delivery.

## `agent.ci_check`: M16 `little-runner` parity (2026-09-17)

`little-runner`'s M16 survey (`docs/requirements.md`'s M16 follow-up
section) flagged this tool as "not close" to the absorbed tools'
functional parity — true for its two genuinely load-bearing, no-
equivalent-anywhere concepts (branch stabilization, cross-project branch
auditing; both still open), but the survey undercounted: by the time
this pass ran, this pillar's own `poller.py`/`github_adapter.py`/`db.py`
session tracking already gave `lrun issue run/finish` and `lrun session
start/stop/list`'s *actor-lookup-and-dispatch* half full parity, just
never checked off explicitly. What this pass actually closed was the one
real remaining gap: `lrun ci run/status` (REQ-000000035's original test-
run-plus-commit-status idea), as a new `agent.ci_check` Job. Given a
`pr_number` and `suite_id`, it resolves the pull request's real head
(`GitHubAdapter.pull_request`, a `gh pr view` wrapper), posts a `pending`
commit status, calls Requirements & Quality's own `requirements.run_tests`
directly (a synchronous cross-pillar Pillar Interface call, the same
pattern `agent/version_client.py` already established, applied to a
second pillar via `agent/requirements_client.py`) against that head sha,
and posts the final `success`/`failure`/`error` status back —
`GitHubAdapter.post_status`'s own docstring states the principle
directly: a status is reporting, never the job itself, so only a genuine
infra failure (no GitHub remote, an unreadable PR, Requirements &
Quality unreachable) fails the Job; a real test failure is a `failure`
status on an otherwise-`succeeded` Job, mirroring how `run_tests` itself
already treats a failing test as data, not an error. Recorded in this
pillar's own new `ci_checks` table (`GET /v1/ci_checks/{project_id}`,
`GET /v1/ci_checks/{project_id}/latest`), queued on the same
`queue_key=project_id` mutex `agent.run_task` already uses, since both
share the same worktree-provisioning path.

`lws job queue show/release`, matching `lrun queue show/release`
(REQ-000000040's status/queue visibility), needed no new pillar code at
all — it is a pure `lws`-side convenience over Orchestration's own
existing `GET /v1/jobs?status=`/`POST /v1/jobs/{id}/stop` (`show` lists
and groups by `queue_key`; `release` stops the running holder so the
next queued Job dispatches).

`lrun session start/stop/list`'s other half — an ad-hoc, human-driven,
`dtach`-backed interactive terminal session, independent of any Job —
is also closed (2026-09-17), as `agent.session_start`/`session_stop`
(`agent/session_flow.py`), reversing the earlier deferral once the
underlying mechanics turned out to already have a home in this daemon's
own model, not a foreign one: Deployment & Servers already launches and
tracks long-running background processes started once and stopped
later (`deployment.deploy`/`server.launch`), and M5's self-redeploy
worker already established the `systemd-run --user --collect` detached-
dispatch pattern this reuses. What genuinely is new here is the PTY
itself — `agent.run_task`'s own Remote Control usage is headless
(`claude -p`, `flow.py`'s `_invoke_claude`); an *interactive*
`claude --remote-control` session needs a real terminal to attach to,
which `dtach` provides, kept alive independent of whatever launched it.
A transient `systemd --user` *scope* (not a *service*) tracks cgroup
population rather than one main PID, so the session survives this
pillar's own unit restarting — ported directly from `little-runner`'s
own hard-earned finding that a plain `dtach -n` fork still inherits its
launcher's cgroup despite reparenting in the process tree. Exactly one
session per project (a deterministic socket path,
`Paths.session_socket_path`); refused outright if one is already
running, or if the project's checkout isn't yet trusted by Claude Code
(`~/.claude.json`'s own trust-dialog state — a session launched fully
detached would otherwise hang at that prompt forever with no one there
to answer it). `GET /v1/sessions/{project_id}` reports whether one is
running; `lws agent session-list` loops that over Registry & Portfolio's
own project list for the old tool's "every project" view, the same
pattern `version.branch_audit`/`version.stabilize` already established.

## Left open

- Whether Agentic Development's own review-mode parameter (manual vs.
  auto-approve) and Policy & Compliance's action-gating
  `pending_confirmation` state should ever compose on the same Job (e.g.
  an auto-approved task that's also gated by a failing policy) isn't
  decided here — they're separate mechanisms answering separate
  questions (is the agent's *own* work good enough to finish
  automatically, vs. does a *policy* allow this action to happen at all)
  and nothing yet says how a Job subject to both actually resolves.

## Workspace session (2026-09-21)

`agent.session_start`/`agent.session_stop` and `GET /v1/sessions/{project_id}` also accept the reserved
id `workspace` (`common/workspace.py`), which is never looked up in Registry & Portfolio and resolves to
the projects root directory — the parent of the workshop checkout, or `LITTLE_WORKSHOP_WORKSPACE_DIR`.
It gives the user one Claude Code session above every project for cross-project work, with the same
one-session limit, trust check and detached lifetime as a project session.

## Incidents: an unhandled exception, awaiting the user's confirmation (2026-09-22)

`POST /v1/incidents` (called directly, never through Orchestration — a crash reporter
must survive Orchestration itself being unreachable) records an open incident: project,
raising component ("pillar" — one of little-workshop's own 10, or a child project's own
app), context (`METHOD /path`), exception type/message, and a truncated traceback.
`GET /v1/incidents` (optional `status` filter) and `GET /v1/incidents/{id}` are plain
reads. Two Jobs settle an open incident:

- `agent.confirm_incident` — starts a real `agent.run_task` (the exact same dispatch path
  `agent.run_task` itself uses) on the incident's own project, `intent="bug"`, title and
  description built from the exception, `review_mode` taken from the caller (default
  `auto_approve`) — then links the incident to the new run.
- `agent.dismiss_incident` — marks it dismissed; no task is ever created.

An incident is never auto-dispatched — the same "propose, a human decides" discipline
policy drafts and redeploy confirmations already follow.

**Reporting side**: `common/incident.py` is installed into all 10 of little-workshop's
own FastAPI apps (`install_incident_reporting`, one line each) as ASGI middleware — an
unhandled exception is reported once, then re-raised unchanged, so the pillar's own error
response is exactly what it would have been otherwise. `no_auto_incident()` (a context
manager) marks a block whose exceptions are part of normal, expected control flow, never
worth a bug task. `tools/incident_reporter.py` is a dependency-light, vendorable copy of
the same idea for a *child* project's own app (little-meals, little-home, ...) — copied
into that project's own tree, matching this project's "no shared Python import across
repos" convention, posting over little-workshop's own Agentic Development Unix socket
(`LITTLE_WORKSHOP_AGENT_SOCKET` override; defaults to the well-known
`~/.local/state/little-workshop/sockets/agent.sock`). Delivered as a tested, ready-to-adopt
module in this pass; wiring it into each child project's own repository is that project's
own follow-up commit, not part of little-workshop's own codebase.

## Playwright as standard: the agent drives a real browser (2026-09-24)

Playwright is this ecosystem's standard for anything a user sees or touches, so every Claude Code
session this pillar launches on a project — each `agent.run_task` run, and each ad-hoc session
(`agent.session_start`) — gets the **Playwright MCP server** attached through `claude --mcp-config`
(`common/playwright.py`). The agent can then launch the project's app (the project's
`[server.screenshot].command` starts this branch on a scratch port) and navigate, click, type, resize to
a phone viewport and take snapshots with the `browser_*` tools.

- **Pinned, not `@latest`**: `@playwright/mcp@0.0.82`, run through `npx -y`, so a run is reproducible and
  an upstream release can't change agent behaviour overnight. Bumping it is a one-line change.
- **Headless, in-memory profile** (`--headless --isolated`): a run is unattended under systemd, and nothing
  one run does in the browser (logins, cookies) leaks into the next.
- **Uses the host's own Chromium**: `--executable-path` points at the newest `chromium-*` the host's
  Playwright install already downloaded (`~/.cache/ms-playwright`, or `PLAYWRIGHT_BROWSERS_PATH`), so no
  second ~150 MB browser download is needed. Without one, the MCP server uses its own; the prompt tells the
  agent how to install it (`npx -y @playwright/mcp@0.0.82 install-browser chrome-for-testing`).
- **Snapshots outside the checkout**: `--output-dir` is under the state directory
  (`playwright-mcp/run-<run_id>` for a run, `sessions/playwright-mcp/<project>` for a session), never the
  project checkout, whose leftovers `_publish` would otherwise commit.
- **Never a failed run for lack of Node**: without `npx` on `PATH`, the session is launched as before,
  without the server, and the prompt says so. A run's own `playwright_mcp: false` param (also on the
  `workshop_agent_run_task` MCP tool) turns it off for that run.

The prompt (`templates/prompts/ui_playwright_mcp.md`, or `ui_playwright.md` without the server) asks the
agent, for any UI/UX change, to check it in the browser (desktop and phone viewports), never touch whatever
is serving on the project's real port, and cover the behaviour with pytest-playwright tests under
`tests/ui/` using relative URLs — which Requirements & Quality then runs against the branch's own app
(`docs/pillars/requirements_quality.md`, "UI tests with Playwright").

## Documentation review: comments and the update-doc job (2026-09-28)

The backend of the Dashboard's Docs pages (`docs/pillars/dashboard.md`).

- **Docs** (`GET /v1/docs/{project}`, `/file?path=`, `/image?path=`): the
  project checkout's markdown files (tracked or untracked, never ignored),
  its current branch and branches, and one file's source or an image it
  shows. Paths are confined to the project; `.git/` and `.review/` are off
  limits. `agent.docs_checkout` (a Job) switches the checkout's branch —
  git itself refuses when local changes would be overwritten.
- **Comments** live in the project, in `.review/comments.json`
  (`agent/review_store.py`), never committed: `.review/` goes into the
  repository's `info/exclude` on the first save, and the update-doc job
  adds it to `.gitignore` in its commit (new projects get it from
  `project.bootstrap`'s `.gitignore` template). Each comment has a UUID,
  the `branch` it was written on, its file, an anchor (source line range
  plus the exact highlighted text and ~32 characters either side, so it
  is found again after lines move), its markdown body, `open`/`done`,
  and — once done — the `change` the job made (`edited`: the new lines
  and their text; `removed`: where the section was) and the job's `note`.
  `GET/PUT/DELETE /v1/reviews/{project}[/comments/{id}]` are direct reads
  and writes, not Jobs — the same exception `/v1/incidents` makes: the
  Dashboard autosaves two seconds after typing stops, which as Jobs would
  bury the Jobs page. For one file, a done comment's `change` is located
  in the file as it is now (`found: false` when its text is gone).
- **`agent.update_docs`** (`agent/doc_update.py`), params `branch`,
  `remote_control`; the Dashboard's queue key is `{project}:docs:{branch}`
  and it refuses a second job for the same project and branch.
  1. Start (synchronous): refuse if one is already running for that
     project and branch; claim every open, non-empty comment on the branch
     (`job_id` set — read-only until the job ends; comments written later
     are not part of it). Nothing to claim fails the Job.
     Certified comments (`certified: true`, set by the reviewer with Ctrl+I) are
     not claimed and are read-only; their text is never sent to Claude. The job
     file's `certified_sections` lists only their file, lines, highlighted text and
     source, and the prompt tells Claude the section is well written by the
     reviewer's standards: not to change it unless absolutely necessary, and then
     without losing or altering its content, saying so in the note of the comment
     that forced it.
  2. The comments go to `update-doc-job.json` (id, path, lines,
     highlighted text, the source lines verbatim, the comment) and are
     repeated in the prompt (`templates/prompts/update_docs.md`), which
     tags the file with `@`.
  3. Without Remote Control, Deployment & Servers provisions a detached
     worktree at the branch's tip and Claude runs headless (`claude -p`,
     which has no trust prompt to hang on) as its own transient
     `systemd --user` service. With Remote Control, print mode cannot
     register with the Claude app, so an interactive
     `claude --worktree lws-docs-<job> --remote-control <name> ... -- "<prompt>"`
     runs under `dtach` (in a `systemd --user` scope) from the project
     folder, which Claude Code must already trust: Claude creates and owns
     the worktree under `.claude/worktrees/`, is told first to reset it to
     the branch's tip, and nothing is pre-approved, so permission requests
     are answered from the app. The job files live in `doc-jobs/<job>/` in
     the pillar's state. Either way the session survives this pillar
     restarting, and a restart resumes watching it (`resume_doc_updates`).
     Claude commits but never pushes; publishing refuses commits that are
     not based on the branch's tip.
  4. The done signal is Claude writing `.review/update-doc-result.json`
     (per comment: `edited` with the new lines, `removed`, or `unchanged`
     with a note). Without it — the session was interrupted, or hung —
     the work counts as done once the worktree and the session's
     transcript have been idle for 5 hours.
  5. Finalize: stop the session, commit anything left, push `HEAD` to the
     branch, fast-forward the local branch (merge in the checkout when
     it is on that branch, else move the ref); if neither took it, the
     commit is kept on `docs-review/<job>`. Then each claimed comment is
     updated individually by id — `done` with its change, whose text is
     read from the committed file (never copied from Claude), or back to
     plain `open` — and the worktree is removed. Claude never edits the
     comments file.
  6. Stop (`POST /v1/jobs/{id}/stop`, this pillar's only stoppable Job):
     the session is killed and the worktree removed with every change in
     it; the comments are released untouched.

## Shared project standards copy (2026-10-01)

Every `agent.run_task` run is given a **temporary, read-only copy of little-workshop's
`shared_project_standards/`** (`src/agent/standards.py`; REQ-000000286..289).

- **What is copied**: every Markdown file at the root of `shared_project_standards/` plus the Markdown files
  of the run's own project folder (`<project id, hyphens as underscores>_standards/`). Other projects'
  folders and non-Markdown files are left out. A project with no folder gets the root files alone.
- **Where it lives**: `tempfile.mkdtemp("lws-standards-<run_id>-")/shared_project_standards`, outside every
  checkout so `_publish` can never commit it. `AgentFlow.run_task` removes the directory in a `finally`, so
  it is gone whether the run finishes, fails or is stopped. The original is never modified.
- **How Claude learns of it**: the prompt template `standards.md`, filled into `{standards_instructions}` of
  `common.md`, names the path, says to read what applies, never edit or commit it, and to prefer the
  project's own documents on a disagreement.
- **Source**: the registered `little-workshop` checkout's `shared_project_standards/`
  (`AgentFlow.standards_root` overrides it for tests). If it cannot be found or copied the run proceeds without
  the copy; a missing source never fails a task.
- **Opt-out**: the `include_standards` param, default true. The New task form shows it as a checkbox, checked by
  default; the choice is carried through scheduling and retry; `workshop_agent_run_task` takes
  `include_standards: false`. The "Update Standards Documentation" job sets it false, since it edits the
  original folder.
