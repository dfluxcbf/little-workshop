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

## Left open

- Whether Agentic Development's own review-mode parameter (manual vs.
  auto-approve) and Policy & Compliance's action-gating
  `pending_confirmation` state should ever compose on the same Job (e.g.
  an auto-approved task that's also gated by a failing policy) isn't
  decided here — they're separate mechanisms answering separate
  questions (is the agent's *own* work good enough to finish
  automatically, vs. does a *policy* allow this action to happen at all)
  and nothing yet says how a Job subject to both actually resolves.
