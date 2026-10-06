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
| Transcripts (S5) | `usage.ingest_transcripts`, and a session-end hook for that session's files | requests and tool calls of sessions telemetry missed; the 1-hour share of a cache write; each subagent's transcript (`<session>/subagents/agent-*.jsonl`) as a thread of its own (`requests.agent_name`) |

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

### Call outcomes

How each tool call ended (`usage/results.py`, REQ-000000358), kept with the call (`tool_calls.outcome`,
`exit_code`, `tests_passed`, `tests_failed`): the branch a workflow took after it. Claude Code marks a shell
command failed only when the command itself exits non-zero; a test run piped into `tail` or `grep` exits 0,
so its failures show only in the summary it printed. The transcript parser therefore reads, in order: a test
summary in the output (pytest, Bazel, Vitest), the `Exit code N` that starts a failed command's result, the
`status` of the Job a little-workshop call answers with, and the tool's own error flag. Outcomes: `ok`,
`failed`, `held` (`pending_confirmation`), `running` (a Job not done yet), `cancelled`, or none. Telemetry
carries no result text, so its calls are only `ok` or `failed`. `GET /v1/call_outcomes?signature=` sums them
per signature.

On this machine's transcripts (2026-10-04): 1,779 test runs printed a summary and 405 failed, 397 of them
reported as a success by the tool because the run was piped; 868 of 15,544 shell commands failed; a
`workshop_version_improvement_finish` mostly answered `running`, since a finish waits behind its policy
checks. When the parser learns to read something new, `transcripts.PARSER_VERSION` is raised and every
stored transcript is read once more (a file's mark carries the version).

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
| Pillar | `<config>/usage/config.json` (`intake_port`, `retention_days`, `plan_pressure_hold_percent`, `dashboard_url`), `pricing.json` | the owner |

- **User profile**: the telemetry environment (`CLAUDE_CODE_ENABLE_TELEMETRY=1`, the `OTEL_*` exporter
  variables for http/json to the intake, `OTEL_LOG_TOOL_DETAILS=1`), the four session hooks, a generated
  statusline (`<config>/usage/statusline.py`: context, cache, 5-hour and 7-day usage, posted when they change)
  and `cleanupPeriodDays` of at least 90. `usage.configure_claude` is a dry run that lists the changes; with
  `apply: true` it copies the settings file beside itself, then writes. It keeps every entry of the owner's,
  leaves a statusline of the owner's own and a longer retention alone, and replaces only its own hooks. The
  MCP tool and the `lws` command always prompt.
- **Session title** (REQ-000000413, REQ-000000415): the profile copies `usage/session_title.py` to
  `<config>/usage/session_title.py` and runs it on SessionStart and UserPromptSubmit. In a checkout whose top holds
  a `VERSION` file it names the session `<project> · <VERSION> · <branch>` (the project is the checkout's
  directory, a submodule's module name), only when the branch or the version differs from what it last applied to
  that session (`~/.local/state/little-workshop/session-titles/<session>.json`), so a name given in between stays
  until the next change. Hooks run in every session, the Windows app's WSL sessions included.
- **Status mod** (REQ-000000414, REQ-000000415): the profile writes the plugin `src/usage/claude_mod/` to
  `~/.claude/skills/little-workshop-status/`, which every terminal session loads by itself, with the Dashboard's
  socket and `dashboard_url` (default `http://localhost:8790`) written in. It reads `GET /status.json` for the
  checkout's project every 30 seconds and when a turn ends, while a surface draws the session. `/ws` answers on every
  surface, the phone included, with a short status report, one labelled line each: project, branch, version, server, the latest
  test run and the running jobs, then only when there are any the tasks needing you, the jobs failed in 2 h and those
  due within the hour, then the policies and the workshop's pillars and incidents of 2 h; each topic has its count
  and its items listed under it. The phone app draws no plugin pane (2026-10-06), so the report is
  its view; in the terminal `/ws` also opens a pane with every topic and links to the Dashboard. The Windows app's
  WSL sessions load no plugins: it shows the status page instead (`docs/pillars/dashboard.md`).
  `tools/test_claude_mod.py` validates and tests it with the `claude` CLI.
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

`sessions`, `requests`, `tool_calls`, `session_events`, `transcript_events`, `plan_pressure`, `day_rollups` (day ×
project × origin × model), `run_usage`, `findings`, `experiments`, `candidates`, `branch_finishes`,
`requirement_snapshots`, `doctor_reports`, `meta`. Requests, tool calls and transcript events older than `retention_days` (120,
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

Findings are habits and configuration, not automation: each says where tokens go and what fixes it
(`detectors.FIXED_BY`: a habit, a setting, an instruction, a hook, a route that already exists), and work a
Job or a trigger could take over is mined separately ([Automation mining](#automation-mining)). How each saving
is known (`detectors.SAVING_BASIS`, REQ-000000359), shown beside it on the page and in `/v1/findings`:

| Basis | Detectors | What the saving is |
|---|---|---|
| replayed | D6, D7 | the past occurrences replayed against their own threads with the fix in place (the replay of [Automation mining](#automation-mining)) |
| priced | D3, D4 | the recorded cost of the cache writes the fix removes |
| rough estimate | D1, D5 | a share of a measured cost (half of what long requests re-read; half of the cold cache writes), since it depends on how habits change |
| none | the others | not estimated |

- **D6, replayed.** The polls form runs of back-to-back turns that only poll or wait, per thread (a poll in a
  turn that also works is not waiting). Each run becomes the `workshop_job_wait` calls it needs, one per
  120 s it lasted (the wait's default timeout), each answering like the run's largest status read; the
  replay keeps that many of the run's turns and drops the rest, with what they added to the conversation. A
  lone poll is already one turn and saves nothing.
- **D7, replayed.** Each large result cut to 3,000 bytes (`FILTERED_BYTES`, about what the test-output filter
  leaves: the last 40 lines and the failures), the bytes taken out no longer re-read by the later requests of
  its thread up to a compaction. The evidence gives the saving per command.

On this machine's store (2026-10-04, 14 days), replaying cut D6 from $10.51 to $3.10 a week on
ethereal-lands, from $4.64 to $0.16 on little-workshop and from $1.02 to $0.14 on little-meals (most polls are
lone turns), and priced D7 on little-workshop for the first time at $3.06 a week (large `WebFetch` and
`workshop_requirements_suite_list` results).

D3 cannot name a miss's cause (the statusline's `last_miss_cause` is not collected), and D6's polling share
counts poll calls against requests because telemetry does not tie a tool call to its request; where the
transcript does, the rollup's `poll_requests` counts the requests that only polled, and a telemetry-only poll
is placed in the main thread's request before it.

### Applying an optimization

- **One project**: the catalog job *Optimize Claude usage* runs `usage.optimize` with the chosen findings.
  The pillar writes the task itself (`usage/optimize.py`: a fixed description plus a digest of the findings,
  the same text for the same findings), creates one `agent.run_task` Job (intent `task`, manual review; it said
  `improvement` until 2026-10-04, an intent the agent has no prompt for, REQ-000000362)
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
  user messages; a subagent's calls form episodes of their own). **Signature**: the tool plus its arguments with everything particular to one call taken out
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
  or an exit status. *No content creation*: no `Edit` or `Write`. *Bounded branching*: each call ends in one of a few
  known outcomes (succeeded or failed, recorded for at least 90% of the calls), not open-ended output only a
  reader can use; what Claude did next is not judged. All five: **deterministic** (a job, an MCP tool or a
  trigger). No content creation and at
  least three: **hybrid** (code around Claude). Otherwise **heuristic** (stays with Claude; optimize its context).
- **Value: the estimated net saving**, from replaying each past occurrence against the session it happened
  in (`usage/counterfactual.py`, REQ-000000356, REQ-000000357). Every request re-sends the whole
  conversation, so a call costs twice: the turns it takes, and what it adds, which every later request
  re-reads until the session compacts or is cleared. The stored data has, per thread (the main conversation
  and each subagent's), every request's context size and weighted cost in order, so for an occurrence that
  took turns *i1..ik*:
  - *before* = the cost of its turns + *A* × *R*, where *A* is what its own content added (the text its turns
    wrote + calls × overhead + result bytes ÷ bytes per token) and *R* is the sum, over the later requests up to
    the next compaction or clear (a context under 70% of the one before), of each request's own price per
    context token (its mix of cache reads, writes and misses);
  - *if Claude calls the replacement*: the first turn stays, the others go, and the replacement adds its turn's
    text + one call's overhead + a 400-byte answer, re-read the same way; less the new tool's name in every
    session's starting context (30 tokens a request);
  - *if a trigger or a schedule runs it* (only a pillar action a trigger may take over, `trigger_can_run`):
    all of it goes;
  - each with a *low* figure that assumes the replacement makes Claude check on it once more (a turn like the
    one after the occurrence, plus a short call re-read later): a call that returns before its work is done
    does that, and the low figure shows when that would cancel the saving.

  The candidate's value is the better of the two expected figures; one worth less than $0.10 a week
  (`MIN_WEEKLY_SAVING`) is not listed. The two constants are fitted on every stored turn and checked on the
  newest 30% they were not fitted on (`calibrate`; the overhead is the median growth of a one-call turn with a
  tiny result, the bytes per token a ratio of sums over turns with large results, so totals come out right).
  `GET /v1/estimate_method` and the page's "How the estimates are made" give the constants, the check and
  how long an addition stays. The data on 2026-10-04 (20,693 turns): 2.41 bytes per token (not the 4 often
  assumed), 97 tokens of overhead per call, the model's own text staying in the conversation (coefficient
  1.0), an addition re-read by a median of 144 later requests, and a check within 6.7% per turn and 0.0% in
  total. Only one episode used the multi-pillar redeploy, too few for a direct before/after comparison: the
  experiments after automating are that check. Where a call's result is the information Claude needs (a
  screenshot, a list), the "called" figure assumes a shorter answer would do, which the proposed form must
  make true.
- **Schedule**: daily when it happened on most days, else weekly on the weekdays it happened most, at night
  from 02:00 with the candidates one after another every 30 minutes, so the runs do not compete with the
  owner's sessions for plan usage. It is a recommendation shown with the candidate and written into its task;
  a candidate has no Job to schedule until it is automated.
- **Make it a task** (2026-10-04, REQ-000000373): the Dashboard opens New task filled with the fixed task on
  little-workshop for the candidate (`GET /v1/candidates/{id}/task`: title, intent `task`, manual review, and the
  description with the candidate's evidence); the user may change it, and once it starts the Dashboard tells the
  pillar (`POST /v1/candidates/{id}/task_started`), which marks the candidate accepted. `usage.automate` still
  starts the same task at once. **Dismiss** keeps it out of the list. Once the replacement exists, name it on the
  candidate (`POST /v1/candidates/{id} {"replacement": "<signature prefix>", "state": "done"}`): mining then
  records **tokens avoided** as the replacement's stored runs × what one run saves when Claude calls it (the
  median of the occurrences' estimates), and detector D10 watches that Claude takes the route. A run a trigger
  or a schedule makes is a Job, not a call, and is not counted.

The candidates the baseline showed (proposal §11.3) are built: `workshop_job_wait` (M18),
`deployment.redeploy_changed_pillars` with its trigger, `requirements.sync_markers`, the test-output filter and
the policies digest.

## Data explorer

Data kept before it has a use, each kind plotted on one card at the bottom of the Claude usage page
(`usage/explorer.py`, REQ-000000360, REQ-000000361). The transcript backfill keeps, besides requests and tool
calls:

- per request: its thinking tokens, its thinking time and the effort it ran at (`requests.thinking_tokens`,
  `thinking_ms`, `effort`);
- one `transcript_events` row per record of these kinds (keyed by the record's uuid): `turn` (a turn's total
  duration; the transcript does not split it into model and tool time), `compaction` (its trigger and the
  tokens before and after), `insertion` (an attachment Claude Code renders into the conversation, by type, with
  the bytes rendered; a tool list or a prompt snapshot is not rendered and not kept), `prompt` (how a prompt
  arrived: typed, queued while Claude worked, system, SDK), `denial` (a tool call refused, by kind) and
  `api_error` (by status: 429 is a rate limit).

A card is one function in `explorer.CARDS`, reading the store over the last 28 days (`GET /v1/explorer?days=`,
at most 120) and returning its title, what it shows and where its data comes from, a kind (bars stacked per
day, or lines), a unit, its series (the five largest names, the rest as "other"; a series may sit on a second
scale at the right) and a note summing up the period. The page shows one card at a time: arrows and dots on a
desktop, a swipe on a phone (CSS scroll-snap). Adding a card is adding a function; the page draws whatever
it is given. The eight cards: thinking tokens by effort, compactions by trigger, how long a turn takes,
what the harness adds to the conversation, how prompts arrive, tool calls denied, API errors beside plan
pressure, test runs passed and failed.

On this machine's transcripts (28 days to 2026-10-04): thinking was 39% of output tokens (23% at low effort,
51% at max); 57 compactions, the median from 171k to 27k tokens; a median turn took 1.4 minutes and the
slowest tenth over 16 minutes; the harness rendered about 770 kB a day into conversations (skill listings
and tool-list changes the most); 42% of the prompts with a recorded source were queued while Claude was
working; 74 tool calls were denied; 36 of 40 API errors were rate limits; 378 of 1,663 test runs failed.

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
| `usage.doctor` | Checks the intake, the user profile, `claude doctor`, the language servers and recent data; keeps the report. Programs are looked for on the PATH of the owner's interactive shell (`bash -ic 'command -v …'`), where nvm and `~/.bashrc` additions are, not the pillar's own short systemd PATH |
| `usage.configure_claude` | Shows, or with `apply: true` writes, the user profile |
| `usage.purge_project` | Removes a project's rows |
| `usage.analyze` | Rolls up, runs the detectors, opens, refreshes and resolves findings, measures the experiments that are due |
| `usage.measure_experiment` | Measures one running experiment now |
| `usage.optimize` | Starts the fixed optimization task for a project's chosen findings |
| `usage.mine_automation` | Mines the last 14 days of tool calls into candidates; updates tokens avoided |
| `usage.automate` | Starts the fixed task on little-workshop for one candidate at once (the Dashboard's "Make it a task" opens New task with it instead) |

## Proxies

- Dashboard: **System → Claude usage** (`/usage`), and a link from each project card.
  Each plot is drawn at its on-screen width with a colour legend (a comparison week dashed) and dates on its
  x axis; on a phone each table row stacks into label and value lines (REQ-000000355). The
  [Data explorer](#data-explorer) is its last section.
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

`GET /v1/version` (2026-10-04, REQ-000000379) changes whenever what the Claude usage page shows can have
changed — a request stored, a finding or a candidate added or decided — so the Dashboard's live page reads
one string instead of the whole summary to know.
