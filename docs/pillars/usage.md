# Usage & Efficiency

The pillar that records what every Claude session on this machine uses, interprets it and helps reduce it.
Optional and on by default: switched off, nothing is collected and the rest of little-workshop is unaffected.
The reasons, the baseline measured before it existed and the owner's decisions are in
[../claude_usage_optimization.md](../claude_usage_optimization.md); this file says what is built
(`src/usage/`, pillar id `usage`). Milestones M19–M22 of [../milestones.md](../milestones.md).

Figures are estimates in tokens. "Weighted tokens" are list-price dollars used as one unit; on a subscription
they stand for plan usage and are never money spent.

## Sources

| Source | Arrives as | Gives |
|---|---|---|
| Run result (S1) | Agentic Development's `claude_usage` rows, read every ten minutes (`GET /v1/usage?since=`) | exact totals per run, query and headless doc update; kept in `run_usage` |
| OpenTelemetry (S2) | `POST /v1/logs` (OTLP http/json) | one `requests` row per `api_request` event, one `tool_calls` row per `tool_result` event |
| Claude hooks (S3) | `POST /v1/hooks/<event>` | the session's working directory, transcript path, end reason; compaction events |
| Statusline (S4) | `POST /v1/statusline` | the plan's 5-hour and 7-day usage (`plan_pressure`) |
| Transcripts (S5) | `usage.ingest_transcripts`, and a session-end hook for that session's file | requests and tool calls of sessions telemetry missed; the 1-hour share of a cache write |

The sources overlap on purpose. A request is keyed by its API request id, a tool call by its tool-use id and a
session by its session id, so storing something twice updates the row and counts nothing twice; a later source
only fills what an earlier one left empty. `POST /v1/metrics` is accepted and not stored: the log events carry
everything used. The intake answers every post with success, whatever it was sent.

Prompt and response text is never stored (decision 10 of the proposal is open, and off until decided). Commands
and file paths of tool calls are stored (`OTEL_LOG_TOOL_DETAILS=1`, decision 3).

The OTLP parser was written against a real export (Claude Code 2.1.289, 2026-10-04): attribute values arrive
as strings, and the resource attributes of a run are repeated on each record. That version posted no `http`
hook at session start, so the profile's `SessionStart` hook is a `command` hook that posts with `curl`; the
other three are `http` hooks. The transcript format is internal to Claude Code: `transcripts.py` skips what it
cannot read and has only been tested against lines written for the tests.

## Intake

The pillar binds only its Unix socket. Claude Code needs an HTTP URL, so at start the pillar
(`usage/proxy.py`):

1. takes its loopback port from `<config>/usage/config.json`, or chooses the first free one from 4318 upward
   and keeps it (decision 2);
2. unless it is already listening, starts the transient unit `little-workshop-usage-intake` with
   `systemd-run --user --socket-property=ListenStream=127.0.0.1:<port> systemd-socket-proxyd <usage.sock>`.

A proxy that cannot be started is reported by `usage.doctor`, never an error: the socket still serves the
proxies, and the transcript backfill still covers every session.

## Configuration

| Scope | Where | Applied by |
|---|---|---|
| User | `~/.claude/settings.json` | `usage.configure_claude` (`usage/profile.py`) |
| Launch | the environment of each Claude little-workshop starts | Agentic Development, from `GET /v1/config` (`agent/usage_client.py`) |
| Pillar | `<config>/usage/config.json` (`intake_port`, `retention_days`, `plan_pressure_hold_percent`), `pricing.json` | the owner |

- **User profile**: the telemetry environment (`CLAUDE_CODE_ENABLE_TELEMETRY=1`, the `OTEL_*` exporter
  variables for http/json to the intake, `OTEL_LOG_TOOL_DETAILS=1`), the four session hooks, a generated
  statusline (`<config>/usage/statusline.py`: context, cache, 5-hour and 7-day usage, posted when they change)
  and `cleanupPeriodDays` of at least 90. `usage.configure_claude` is a dry run that lists the changes; with
  `apply: true` it copies the settings file beside itself, then writes. It keeps every entry of the owner's,
  leaves a statusline of the owner's own and a longer retention alone, and replaces only its own hooks. The
  MCP tool and the `lws` command always prompt.
- **Launch environment**: task runs, queries, doc updates and project sessions get the same telemetry
  variables plus `OTEL_RESOURCE_ATTRIBUTES=lws.project=…,lws.job=…,lws.job_type=…,lws.run=…`. With the pillar
  off or unreachable the run starts unchanged.

## Attribution

Each session gets a project, the first rule that applies (`usage/attribution.py`):

1. the `lws.project` tag of a run little-workshop started;
2. the registered checkout containing its working directory (the longest wins; a git worktree elsewhere is
   traced through its `.git` file to its repository);
3. the registered project whose repository telemetry names;
4. the workspace: a folder holding at least two checkouts, named by the folder (`little-projects`);
5. `unattributed`.

Its origin is `job`, `query` (`agent.query`) or `interactive`; a request made by a subagent is rolled up under
`subagent`. Attribution is recomputed on every rollup, so a session seen first by telemetry gets its project
once a hook or its transcript supplies the directory.

## Store

`sessions`, `requests`, `tool_calls`, `session_events`, `plan_pressure`, `day_rollups` (day × project × origin
× model), `run_usage`, `findings`, `experiments`, `candidates`, `branch_finishes`, `requirement_snapshots`, `doctor_reports`,
`meta`. Requests and tool calls older than `retention_days` (120,
never under 35) are pruned; a day's rollup stays, so the store, not the raw transcripts, is the long-term
record (decision 4). `usage.purge_project` removes a project's rows.

## Metrics

- **Weighted tokens** per token type: tokens × the model's list price (`usage/pricing.py`; defaults for Fable,
  Opus, Sonnet and Haiku, overridable in `pricing.json`). A cache write is priced by its 1-hour share when the
  transcript gave it, else from Claude Code's cost estimate, else as a 5-minute write.
- **Cache hit ratio** = cache read ÷ (input + cache read + cache write).
- **Average context per request**, and the **long-context share**: context tokens in requests above 200k.
- **Startup context**: what a session's first main-thread request carried; reported as a median.
- **Plan pressure**: the highest 5-hour and 7-day percentage per day.

A week starts on Monday. `GET /v1/summary?week=&project=&origin=` rolls up first when the last rollup is more than a
minute old, then returns one week: the per-day series, totals,
per-project totals, the heaviest sessions, plan pressure, the previous week, the next week and the four-week
average where they have data, and per week the weighted tokens spent on little-workshop against the others.

## Findings

`usage.analyze` (`usage/detectors.py`) measures every project over the last 14 days. Each detector is
deterministic code with thresholds in `config.json` (`"thresholds": {...}`, defaults in
`detectors.DEFAULT_THRESHOLDS`); a flagged measurement becomes a finding with its evidence, an estimated weekly
saving (weighted tokens) and the recommended action.

| Id | Flags | Default threshold |
|---|---|---|
| D1 | context tokens sitting in requests above 200k; sessions with a very large context | share above 60%, or a session above 500k |
| D2 | median startup context grown over the 30 days before | more than 10% |
| D3 | cache rebuilds that neither a session's first request nor a compaction explains | more than 5% of main-thread requests |
| D4 | one-request job or query sessions that write a 1-hour cache | 3 sessions |
| D5 | one-request job or query sessions spread over directories | 3 directories |
| D6 | tool calls that only poll or wait (`workshop_job_status`, `sleep`, `timeout`) | more than 2% of requests, or more than 10 in one episode |
| D7 | a command whose results are large on average | above 20 kB over at least 5 calls |
| D8 | a file read in many sessions | 5 sessions |
| D9 | routine calls (redeploy, status, sync) made in an Opus or Fable session | 10 calls |
| D10 | a raw call repeated where a route replaces it (`detectors.ADOPTION`) | 3 calls in one session |
| D11 | an MCP server connected in many sessions and never called (project `all`) | 5 sessions in 30 days |
| D12 | job or query runs on a day the 5-hour window peaked | 80% |

A finding is keyed by detector and project: analysis refreshes it instead of opening another, leaves a
dismissed one dismissed, and resolves an open one that is no longer flagged. States: `open`, `accepted` (an
optimization is under way), `dismissed`, `resolved`. Findings are listed by estimated saving, largest first.

D3 cannot name a miss's cause (the statusline's `last_miss_cause` is not collected), and D6 counts poll calls
against requests because telemetry does not tie a tool call to its request; where the transcript does, the
rollup's `poll_requests` counts the requests that only polled.

### Applying an optimization

- **One project**: the catalog job *Optimize Claude usage* runs `usage.optimize` with the chosen findings.
  The pillar writes the task itself (`usage/optimize.py`: a fixed description plus a digest of the findings,
  the same text for the same findings), creates one `agent.run_task` Job (intent `improvement`, manual review)
  on the project's queue, marks the findings accepted and records an experiment per finding.
- **Every project**: a change every project would make the same way goes into
  `src/registry/claude_templates.py` or a policy once; the catalog job *Apply common Claude configuration*
  (`registry.sync_claude_files`) then carries it to each project on an improvement branch of its own, and the
  branch finish runs `claude-config-present` and `claude-instructions-valid`.
- **Experiments**: an experiment holds the finding's metric over the seven days before the optimization
  began. `usage.analyze` measures it again once 14 days have passed (a week for the change to land, a week to
  measure), `usage.measure_experiment` on demand. A metric that fell resolves the finding; one that did not
  reopens it. Every metric is "lower is better".

### What the generated Claude files add

Written by project bootstrap and by the sync (`docs/pillars/registry_portfolio.md`), checked by
`claude-config-present`:

- `permissions.deny` `Read(...)` rules for the template's heavy generated folders (`bazel-*`, `.venv*`,
  `node_modules`; a Unity `Library`, `Temp`, `Logs`, `obj`). Claude Code has no `.claudeignore`.
- `enabledPlugins` for the code-intelligence plugin of each language the checkout holds (`pyright-lsp`,
  `typescript-lsp`, `clangd-lsp`, `csharp-lsp`, from Anthropic's `claude-plugins-official` marketplace).
  `tools/install_language_servers.sh` offers to install the language servers and plugins, asking for each;
  `usage.doctor` reports the ones missing. Whether `enabledPlugins` alone enables a plugin that is not
  installed has not been checked on this machine (decision 9).
- `.claude/hooks/test_output_filter.py`, a `PreToolUse` hook for Bash after the scope guard: a plain test
  command (`pytest`, `bazel test`, `npm test`, `vitest`, `tools/run-unity-tests`, with no pipe, redirection or
  second command) is run through the filter. Up to 150 lines are printed whole; a longer run is reduced to
  the failing lines with context and the last 40 lines, the full output saved to a file the first line names.
  The command's exit status is kept.
- The `policies-digest` skill: its script prints the policies attached to the project, with their gate points
  and rules, read live from little-workshop.

No telemetry variable is generated into a project: Claude Code ignores `OTEL_*` exporter settings there.

## Automation mining

`usage.mine_automation` (`usage/mining.py`) looks for the work Claude repeats with the same shape, so code can
do it for zero tokens. Nothing is implemented from it: a person chooses.

- **Episode**: the tool calls of one prompt (`prompt.id` from telemetry; in a transcript, the calls between two
  user messages). **Signature**: the tool plus its arguments with everything particular to one call taken out
  (`usage/signatures.py`): `mcp:workshop_deployment_redeploy_pillar(commit, pillar)`,
  `Bash(python3 -m pytest <path>)`, `Bash(python3 tools/sync_policies.py)`. File contents never reach it.
- **Candidate**: a signature, or a run of two or three consecutive signatures, seen in at least 3 sessions or
  at least 10 times in 14 days, with at least one call code could make (`mining.actionable`): an MCP call or a
  shell command that does work. Looking around (`Read`, `Grep`, `cat`, `ls`, `git status`), writing content
  (`Edit`, `Write`), a script written on the command line (`Bash(python3 <inline>)`: a here-document, `-c`) and
  a `cd` are never candidates on their own. A shell signature names the first command that does work, past
  any `cd` or `export` before it. Each mining run signs the stored shell calls again from the command kept
  with them and drops the open candidates it no longer finds; one a person decided on is kept.
- **Score**: five checks. *Argument derivability*: a shell command or an MCP call, not prose Claude wrote.
  *Output stability*: the same shape in at least three sessions. *Checkability*: the outcome is a pillar state
  or an exit status. *No content creation*: no `Edit` or `Write`. *Bounded branching*: what follows it is one
  of a few things. All five: **deterministic** (a job, an MCP tool or a trigger). No content creation and at
  least three: **hybrid** (code around Claude). Otherwise **heuristic** (stays with Claude; optimize its context).
- **Value**: the weighted tokens its occurrences cost per week, taking each call as one request of its
  session's average cost. Candidates are ranked by it.
- **Schedule**: daily when it happened on most days, else weekly on the weekdays it happened most, at night
  from 02:00 with the candidates one after another every 30 minutes, so the runs do not compete with the
  owner's sessions for plan usage. It is a recommendation shown with the candidate and written into its task;
  a candidate has no Job to schedule until it is automated.
- **Make it a task** (`usage.automate`): one fixed `agent.run_task` improvement task on little-workshop with
  the candidate's evidence. **Dismiss** keeps it out of the list. Once the replacement exists, name it on the
  candidate (`POST /v1/candidates/{id} {"replacement": "<signature prefix>", "state": "done"}`): mining then
  records **tokens avoided** as the replacement's stored runs × the median cost of the episodes it replaced,
  and detector D10 watches that Claude takes the route.

The candidates the baseline showed (proposal §11.3) are built: `workshop_job_wait` (M18),
`deployment.redeploy_changed_pillars` with its trigger, `requirements.sync_markers`, the test-output filter and
the policies digest.

## Outcomes

`usage/outcomes.py` (REQ-000000350): spend alone cannot say whether an optimization made the work cheaper or
only shorter. On every tick the pillar notes each succeeded branch finish Orchestration still holds (once per
Job, in `branch_finishes`) and each registered project's count of done requirements for the day
(`requirement_snapshots`, from the Requirements report), because Jobs are deleted after a while and a
requirement's history is not kept. `GET /v1/outcomes?weeks=&project=` then gives, per week and project: the
weighted tokens; the agent task runs that finished (from the stored run results) with their cost per task; the
branches finished with the tokens per branch; and the requirements that turned to done (the done count at the
week's end less the count before it) with the tokens per requirement. A week with no such outcome, or no
reading, has no value rather than a zero. The summary carries the chosen week's rows (`outcomes`).

Plan pressure as an input to scheduling is in `docs/pillars/orchestration.md`, "Plan pressure"; model, effort
and budget per job type in `docs/pillars/agentic_development.md`, "Launch defaults per job type".

## Jobs

All are code; none uses Claude.

| Job type | What it does |
|---|---|
| `usage.ingest_transcripts` | Backfills from the transcripts, skipping unchanged files, then rolls up |
| `usage.rollup` | Reads new run usage, re-attributes, rebuilds the rollups, prunes; also runs every ten minutes on its own |
| `usage.doctor` | Checks the intake, the user profile, `claude doctor`, the language servers and recent data; keeps the report. Programs are looked for on the owner's login-shell PATH (`bash -lc 'command -v …'`), not the pillar's own short systemd PATH |
| `usage.configure_claude` | Shows, or with `apply: true` writes, the user profile |
| `usage.purge_project` | Removes a project's rows |
| `usage.analyze` | Rolls up, runs the detectors, opens, refreshes and resolves findings, measures the experiments that are due |
| `usage.measure_experiment` | Measures one running experiment now |
| `usage.optimize` | Starts the fixed optimization task for a project's chosen findings |
| `usage.mine_automation` | Mines the last 14 days of tool calls into candidates; updates tokens avoided |
| `usage.automate` | "Make it a task": starts the fixed improvement task on little-workshop for one candidate |

## Proxies

- Dashboard: **System → Claude usage** (`/usage`), and a link from each project card.
- MCP: `workshop_usage_summary`, `workshop_usage_findings`, `workshop_usage_candidates` (reads, every project;
  candidates come ten at a time with one example each, `limit` up to 50),
  `workshop_usage_doctor`, `workshop_usage_ingest_transcripts`, `workshop_usage_analyze`,
  `workshop_usage_mine_automation` (little-workshop's sessions), `workshop_usage_configure_claude` (asks).
- CLI: `lws usage summary | findings | candidates | analyze | mine-automation | doctor | ingest-transcripts |
  configure-claude [--apply]`.
- Events reported to Orchestration's triggers: `claude.session_ended`, `usage.finding_opened`.
- Job catalog: *Optimize Claude usage* (every project), *Apply common Claude configuration* and
  *Configure Claude telemetry* (little-workshop).
- `GET /v1/plan_pressure` answers whether unattended Claude jobs should wait; Orchestration asks it.

## First deployment

Built and unit-tested; not yet running on this machine. To bring it up: add `[pillars.usage]` with its
`command` to `~/.config/little-workshop/manifest.toml` (as the other pillars have), redeploy, check
`lws usage doctor`, run `lws usage ingest-transcripts` once, then apply the user profile with
`lws usage configure-claude --apply`. After that: `lws trigger install-defaults`, and scheduled tasks for
`usage.ingest_transcripts` and `usage.analyze` (daily), `usage.doctor` and `usage.mine_automation` (weekly),
with `lws schedule add`. The socket-activated proxy unit has only been checked as a command line:
its first real start is part of that deployment.
