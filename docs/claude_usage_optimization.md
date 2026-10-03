# Claude usage and efficiency: design proposal

Status: **proposal, for review** (2026-10-03). Nothing here is built. If it is accepted, its concepts move into
`docs/design.md`, its implementation into `docs/architecture.md` and a pillar specification, and its phases into
`docs/milestones.md`; this file then becomes their history. Owner decisions are collected in
[§16](#16-decisions-for-the-owner).

This proposal combines three inputs: the notes on Claude Code token usage and `/doctor` collected beforehand,
Claude Code's own documentation (read on 2026-10-03, [sources](#17-sources)), and little-workshop as it is today,
including the Claude Code data already on this machine ([§3](#3-what-the-data-on-this-machine-already-says)).

## 1. Summary

- **Collect from every Claude session on this machine, not only little-workshop's.** Use four overlapping sources:
  the result JSON of each `claude -p` run little-workshop launches (it already receives this and throws it away);
  OpenTelemetry export, switched on once in the user-level `~/.claude/settings.json`; user-level Claude Code
  hooks that post session start and end to little-workshop; and the local transcripts as a backfill. Duplicates
  are removed by API request id.
- **A new optional pillar, Usage & Efficiency (`usage`), which the owner decided is essential,** owns the intake, the store, the analysis and the
  desired Claude configuration. The Dashboard, the MCP server and `lws` show what it holds, like every other
  pillar.
- **Findings come from deterministic detectors, not from Claude.** Optimizations are applied through routes
  that already exist. A change for one project is an `agent.run_task` on a branch with review. A change for every
  project goes into `claude_templates.py` and the policies, then reaches each project through
  `sync_claude_files.py`. Each applied change is measured before and after.
- **Automation mining** looks for work Claude repeats with the same shape and few judgment calls, ranks it by
  the tokens it costs, and proposes it as a job, an MCP tool, a hook or a trigger. Each run of the resulting code
  costs no tokens.
- **Workshop triggers** generalize git hooks: "when event X happens in little-workshop, run job Y". They are
  owned by Orchestration, next to scheduled tasks and pipelines, and are bound by the same safety rules.

The data already on this machine shows that **caching is not the problem**: the hit ratio is 98–99%. The
problem is **how large the context is on every request, times how many requests there are**. Long sessions put
about 400k tokens of context into each request, and that re-read accounts for most of the usage
([§3](#3-what-the-data-on-this-machine-already-says)).

### Your questions, and where they are answered

| Question | Short answer | Section |
|---|---|---|
| How can little-workshop handle configuration dependencies? | It keeps one desired-state profile per scope (user, project, launch, machine). It checks the profile on a schedule and applies it only after you confirm. | [§6](#6-configuration-dependencies) |
| How can it collect the data it needs? | Run results, OTLP export, user-level hooks and a transcript backfill, all received by one local intake | [§7](#7-collecting-the-data) |
| How can it compile the data into something useful? | Per-request rows, then per-session and per-day rollups, then named metrics, then detectors that produce findings | [§8](#8-compiling-the-data) |
| Where can I see the interpreted data? | A Dashboard page "Claude usage", a card on each project, MCP tools, `lws usage`, and a generated statusline | [§9](#9-seeing-the-interpreted-data) |
| How can I see the data per project? | Each request is attributed by job tag, then working directory, then repository, then the workspace bucket | [§7.3](#73-attribution-to-a-project) |
| Can it get data from sessions that have nothing to do with little-workshop? | Yes, for every session on this machine: user-level settings apply to every session, and transcripts cover the rest. It cannot see other machines or claude.ai chat. | [§7.2](#72-sessions-independent-of-little-workshop) |
| Which jobs should run the optimization automatically? | `usage.ingest_transcripts`, `usage.analyze`, `usage.doctor` and `usage.mine_automation` run on a schedule; "Optimize Claude usage" and "Apply common Claude configuration" are started by you | [§10](#10-jobs) |
| How do I optimize one project? | Choose findings, then run a fixed-template agent task on that project. It goes through a branch, review and policies, and is measured before and after. | [§10.2](#102-applying-an-optimization-to-one-project) |
| How do I apply a common optimization to every project? | Change `claude_templates.py` or a policy once, then sync every project and check it with a policy | [§10.3](#103-applying-a-common-optimization-to-every-project) |
| How do optimizations keep using little-workshop? | Generated files and policies hold each change in place, and an adoption detector notices when Claude goes around a route | [§14](#14-keeping-the-optimization-inside-the-framework) |
| How do we find work that can be automated into little-workshop? | Mine repeated tool-call episodes, give each a determinism score and a token cost, and propose the high scorers | [§11](#11-automation-mining) |
| How do we take work Claude does and make it run without Claude? | Turn the deterministic episodes into jobs, tools or triggers. Every later run of that code costs no tokens. The Dashboard also recommends how often to run each task and a sequential night-time schedule ([§9](#9-seeing-the-interpreted-data)). | [§11.4](#114-what-the-mining-produces) |
| How do we take token usage into account? | Record tokens per job, set budgets per job type, schedule around plan usage, and rank work by tokens saved | [§12](#12-token-usage-as-an-input) |
| How do we tell deterministic work from work that needs judgment? | A rubric: are the arguments derivable, is the output stable, can it be checked, does it write new prose? | [§11.2](#112-deterministic-hybrid-or-heuristic) |
| Can little-workshop do what git hooks do, beyond git? | Yes: workshop triggers (an event starts a job), next to the git hooks and Claude hooks that exist already | [§13](#13-workshop-triggers-hooks-beyond-git) |

## 2. The collected notes, checked against the documentation

Most of the collected notes are right in spirit. Several details are wrong, and the details matter, because
little-workshop would write them into configuration.

| The notes say | Claude Code's documentation says | What it means for little-workshop |
|---|---|---|
| Enable telemetry with `OTEL_SDK_DISABLED=false` and `OTEL_EXPORTER_OTLP_ENDPOINT` | The switch is `CLAUDE_CODE_ENABLE_TELEMETRY=1`, plus `OTEL_METRICS_EXPORTER` and/or `OTEL_LOGS_EXPORTER` (`otlp`, `prometheus`, `console`, `none`), `OTEL_EXPORTER_OTLP_PROTOCOL` (`grpc`, `http/json`, `http/protobuf`) and the endpoint | The profile in [§6](#6-configuration-dependencies) uses the documented variables. little-workshop sets this configuration up itself, at install time, so nobody writes these variables by hand |
| Telemetry can be set up per project | **Claude Code ignores `OTEL_*` exporter variables in a project's `.claude/settings.json` and `.claude/settings.local.json`**, so a repository cannot capture content. They work in `~/.claude/settings.json`, in managed settings or in the environment. | Telemetry is a **user-scope** setting. `claude_templates.py` cannot carry it. Runs that little-workshop starts get it from the environment. The user-scope profile covers every Claude session on this machine, so usage is seen for the whole machine and then split by project ([§7.3](#73-attribution-to-a-project)). |
| Input, output, cache-read and cache-creation token counters | `claude_code.token.usage` with `type` = `input`, `output`, `cacheRead`, `cacheCreation`, plus attributes `model`, `query_source` (main, subagent, auxiliary), `effort`, `agent.name`, `skill.name`, `mcp_server.name`. Each `claude_code.api_request` event carries all four counts, `cost_usd`, `duration_ms`, `request_id` and `query_source`. | Per-request events are the main record. Metrics add session counts, active time and lines changed. |
| Cost counters show real spend | `claude_code.cost.usage` and `cost_usd` are **client-side estimates at list price** (unless managed settings define `modelPricing`), not the bill | The owner is on a Max plan and pays for no additional usage, so dollars here are never money spent. On a subscription, dollars are only a common unit for comparing token types and models. The real limit is the plan's 5-hour and 7-day usage ([§12](#12-token-usage-as-an-input)). |
| Cache reads cost 10% of input | 0.1× for Sonnet 5.5 and Haiku 4.5. Opus 5.5 is 0.05× ($0.20 per million tokens against $4) and Fable 5.1 is 0.025× ($0.25 against $10). | The pricing table is per model and configurable |
| Cache creation costs 25% of input | A cache write costs **1.25×** input with the 5-minute TTL and **2×** with the 1-hour TTL | A one-shot run that writes a 1-hour cache pays double for nothing ([§3](#3-what-the-data-on-this-machine-already-says), F4) |
| The cache lives for 5 minutes | 5 minutes on an API key. On a subscription, within plan usage, the **main conversation gets 1 hour**; subagents and compaction get 5 minutes. Both can be set: `promptCacheTtl` / `CLAUDE_CODE_PROMPT_CACHE_TTL`, and `subagentPromptCacheTtl` / `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` (`5m` or `1h`). | Choose the TTL per kind of run: `5m` for one-shot `-p` calls |
| Structure requests so cached content is reused | Caching is automatic and matches an exact prefix. It is per model and **scoped to one machine and working directory**. The cache is invalidated by: switching model, changing effort (on most models), turning on fast mode, changing the set of loaded tools, compaction, many images, and upgrading Claude Code. Editing files or CLAUDE.md mid-session, changing permission mode and invoking skills keep it. | Detector D3 names miss causes. Runs that little-workshop starts use a stable working directory. |
| Use `.claudeignore` | **Not a Claude Code feature** (it exists only as feature requests). Hard exclusion is `permissions.deny` with `Read(...)` rules. Build output belongs in `.gitignore`. Instruction files can be skipped with `claudeMdExcludes`. | The project profile writes deny rules for heavy generated paths |
| Code intelligence plugins send symbols instead of whole files | Real: official LSP plugins, for example `pyright-lsp`, `typescript-lsp`, `clangd-lsp` and `csharp-lsp`. Each needs its language-server binary on `PATH`. They give Claude an `LSP` tool (definitions, references) and diagnostics after each edit. | Per-language plugin and binary checks ([§6](#6-configuration-dependencies)). little-workshop handles them itself: its install installs the missing plugins and language servers, and project bootstrap enables the plugin |
| `/doctor` checks `.claudeignore`, plugins, settings, the OpenTelemetry endpoint and proxies | `/doctor` checks **installation health, invalid settings files, unused extensions, duplicate subagent names, and checked-in CLAUDE.md content Claude can derive from the codebase**, and proposes fixes it applies after you confirm. `claude doctor` in a shell is a read-only version that does not start a session. `/doctor prompt-audit` audits instruction files for stale or conflicting content (v2.1.283+, runs through Claude, costs tokens). The documentation says nothing about checking an OpenTelemetry endpoint. | `usage.doctor` runs `claude doctor`. Telemetry is checked by watching for data at the intake, and by `claude --debug-file` showing `[3P telemetry]` errors. |
| Batch operations | In Claude Code terms: set model and effort at the start of a session and leave them; make independent tool calls in parallel in one turn; avoid turns that only wait | Detectors D6 and D9; a blocking job wait ([§11](#11-automation-mining)) |
| (not in the notes) | `/usage` (session tokens plus a `Prompt cache (main)` line with hit ratio, misses and their likely cause); `/context` (what fills the window); `/insights` (an HTML report on your habits, which costs tokens); the statusline JSON (`cost`, `context_window`, `prompt_cache`, `rate_limits.five_hour` and `seven_day`); `claude -p --output-format json` (`total_cost_usd`, `usage`, `modelUsage`, `num_turns`, `session_id`); hooks, including the `http` type, with `session_id`, `cwd` and `transcript_path`; and transcripts at `~/.claude/projects/<dir>/<session>.jsonl`, whose format is internal and kept 30 days | These are the sources in [§7](#7-collecting-the-data) |

The documentation's own advice for reducing tokens fits little-workshop's existing direction: clear between
tasks, keep CLAUDE.md under 200 lines and move workflows into skills, use path-scoped rules, preprocess with
hooks, put verbose work in subagents, pick the model to fit the work, and prefer CLI tools to MCP where possible.
The policies `claude-instructions-valid` (200 lines per file, 250 at startup) and `claude-config-present`, the
generated skills and the `test-runner` subagent already apply part of it.

## 3. What the data on this machine already says

A one-off read of the transcripts still on disk (2026-08-30 to 2026-10-03, every session on this machine: 332
sessions and 21,715 API requests). The transcript format is internal to Claude Code, so these numbers are best
effort, and they are tokens, not a bill.

| Where | Sessions | API requests | Cache-read tokens | Average context per request | Cache hit ratio | Median startup context | Share of context tokens in requests over 200k |
|---|---:|---:|---:|---:|---:|---:|---:|
| `little-projects` (root) | 80 | 12,971 | 5,379 M | 415k | 99.3% | 45k | 93% |
| ethereal-lands | 25 | 3,535 | 1,456 M | 412k | 98.6% | 44k | 90% |
| little-workshop | 27 | 2,250 | 482 M | 214k | 98.6% | 45k | 72% |
| little-mindcheck | 33 | 883 | 401 M | 454k | 99.4% | 44k | 95% |
| little-meals | 35 | 793 | 259 M | 327k | 99.1% | 46k | 91% |
| little-home | 8 | 591 | 112 M | 190k | 99.3% | 50k | 66% |
| `agent.query` (temporary directories) | 109 | 118 | 1.6 M | 14k | **40%** | 29k | 0% |

What it says:

- **F1. Caching works.** Interactive sessions read 98–99% of their input from cache. Changing cache settings is
  not where the savings are.
- **F2. Context size times request count is the cost.** Across everything: 8.2 billion cache-read tokens, 75
  million cache-write tokens and 17 million output tokens. Priced at Sonnet 5.5 list rates as a common unit,
  cache reads are about 78% of the total, cache writes 14% and output 8%. A request re-reads the whole
  conversation. At the root, the median request carries 421k tokens and one in ten carries more than 836k. The
  biggest lever is therefore shorter sessions and work that does not need a turn at all.
- **F3. Every session starts at about 44k tokens** (system prompt, tools, MCP tool names, briefing, skill list),
  in every project. Even `agent.query`, which runs in an empty directory, starts at 29k. That is the floor each
  new session pays, and it is worth watching for growth (detector D2).
- **F4. `agent.query` defeats its own cache.** `src/agent/query.py:33` runs each call in a new
  `/tmp/lws-agent-query-*` directory. The cache is scoped per directory, so consecutive policy-authoring calls
  share nothing (40% hit ratio). Each call is a single request, yet it writes a 1-hour cache at 2× input price,
  and nothing reads it again.
- **F5. Agent runs throw usage away.** `src/agent/flow.py:180` and `src/agent/query.py:35` discard the parsed
  JSON (`_raw`), which carries `total_cost_usd`, `usage`, `modelUsage` and `num_turns`. Doc updates write the
  same JSON into `.review/session.log` and never read it back (`src/agent/doc_update.py:248`).
- **F6. Claude repeats deterministic work by hand.**
  - `workshop_deployment_redeploy_pillar` was called 240 times, one pillar at a time, although
    `workshop_deployment_redeploy_pillars` exists and orders the redeploys itself.
  - `workshop_requirements_create` and `link_suite` were called about 73 times.
  - 360 requests only poll (`workshop_job_status`, `sleep`, `timeout`); together they re-read about 158 million
    tokens of context.
  - `python3 -m pytest` ran 174 times at the root with full output in context.
- **F7. Some documents are read again in many sessions.** For example, `docs/policies/automation_policy.md` was
  read in 9 root sessions. Each is a candidate for a skill or a short digest.
- **F8. Nothing is collected today.** User settings have no telemetry environment, no hooks and no statusline.
  Transcripts are deleted after 30 days (`cleanupPeriodDays` is not set), so this history disappears unless it
  is imported.

### Where little-workshop itself made things harder

The owner's reading of the same data, split by what each stored session worked on (best-effort numbers from
transcripts):

- **Building it is the main cost.** About 56% of all context tokens were spent in sessions that worked on
  little-workshop. Only 28% went to the five projects it exists to serve. That is an investment, and it pays off
  only if work on those projects gets cheaper. Nothing measures that today ([§9](#9-seeing-the-interpreted-data)).
- **Its own mechanics create extra turns.**
  - Jobs run in the background, so Claude checks their status over and over: about 360 requests that only
    checked or waited.
  - little-workshop runs its own pillars, so every change to it means redeploying them, which Claude did one
    pillar at a time (240 calls).
  - Together that is roughly 2–3% of usage. Both have code fixes: a "wait for job" tool, and an automatic
    redeploy after a branch finishes.
- **A small fixed cost on every request.** The briefings, skill list and MCP tool names add about 15k tokens to
  each session's start (44k against 29k in an empty folder). Claude re-reads them on every request, which is
  at most about 4% of usage. The 250-line policy keeps this from growing.
- **Its own Claude calls were set up badly.** `agent.query` runs each call in a new folder, so it gets only a 40%
  cache hit rate. That is tiny in absolute terms.
- **What it has not touched yet is the biggest cost.** Long sessions re-send about 400k tokens of context on
  every request. That happens in the project folders too (little-mindcheck averages 454k per request), so it is
  a session-length problem, not a root-only one. Nothing in little-workshop addresses it yet.

Verdict: it did not make things more difficult. It built the place where automation lives and the means to
enforce it. What is missing is the feedback loop: knowing what to automate next and whether a change helped.
Without that, we cannot say whether little-workshop has paid for itself yet. The risk to watch is
little-workshop becoming the project that spends the tokens it was meant to save.

## 4. Goals and non-goals

Goals:

- One local, durable record of Claude usage for every session on this machine, attributed to a project and to
  the job that caused it.
- Interpreted views (findings with a recommended action), not only raw counters.
- Optimizations applied through little-workshop's own routes and checked by its own policies, measured before
  and after.
- A steady flow of automation candidates: work Claude repeats that code could do for zero tokens.
- Token usage as an input to scheduling and to the choice of model and budget.
- Recommend when to run automated work. The Dashboard suggests a schedule for each task and how often it should
  run, preferring quiet hours (the middle of the night) with tasks run one after another. Even before work is
  automated, a candidate that is only recommended for automation already gets a recommended schedule.

Non-goals:

- Not a billing system. Figures are estimates, and on a subscription they are not the bill.
- No external observability stack by default: no OpenTelemetry Collector, Prometheus or Grafana. An optional
  forward to one is allowed.
- Prompt and response text is not stored by default.
- Nothing edits instruction files, settings or code without a branch and a human decision. The existing
  invariant "automation never merges, deletes or finishes on its own initiative" holds unchanged.
- Claude Code itself is not changed. Only its documented interfaces are used. Transcripts are read only as a
  fallback.

## 5. Architecture

```
  every Claude session on this machine
  (CLI, VS Code, desktop, claude -p, Remote Control; started by you or by little-workshop)
     │ OTLP http/json        │ http hooks             │ statusline          │ result JSON
     │ (user settings env)   │ SessionStart/End,      │ (rate limits,       │ (claude -p runs started
     │                       │ PreCompact/PostCompact │  prompt cache)      │  by little-workshop)
     ▼                       ▼                        ▼                     ▼
  ┌───────────────────────────── Usage & Efficiency pillar (usage) ───────────────────────────┐
  │ intake (socket proxy)    ←── transcript backfill (~/.claude/projects, scheduled)           │
  │ store: sessions, requests, tool calls, rollups, findings, candidates, experiments          │
  │ analyzers: attribution · metrics · detectors · automation mining                           │
  │ desired Claude environment (user / project / launch / machine profiles)                    │
  └──────────────┬───────────────────────────────┬───────────────────────────────┬─────────────┘
                 │ PI (Unix socket)              │ Jobs through Orchestration    │
                 ▼                               ▼                               ▼
     Dashboard · MCP · lws          agent.run_task (per project)     registry: sync Claude files (every project)
     (thin proxies)                 version branch, review, policies policy check_all, usage.configure_claude
                                                 │
                                                 ▼
                                   changed project → measured again (experiment)
```

**Why a new pillar.** Each pillar has one goal, owns its storage and can be switched off
(`docs/design.md`, Core concepts). "Observe and improve how Claude is used, across every session" is a goal of
its own:

- **Agentic Development** runs tasks. It is redeployed often, and the intake has to stay up while that happens.
- **The Dashboard** holds no domain data (its `statistics.db` is a cache of counts) and can be switched off.
- **Optional, like Build & Packages:** with the pillar switched off nothing is collected, and the rest of
  little-workshop is unaffected.

**Intake without a network port on the pillar.** No pillar binds a network port, the Dashboard included: each
one binds a Unix socket, and the Dashboard reaches a browser through a reverse proxy in front of its socket
(`src/dashboard/app.py`). Claude Code's exporter and hooks need an HTTP URL, so the intake follows that model:

- The pillar serves its intake routes on its own socket: OTLP/HTTP JSON at `/v1/logs` and `/v1/metrics`, and
  the hook and statusline endpoints.
- A `systemd --user` socket unit listens on `127.0.0.1:<port>` and passes connections to that socket through
  `systemd-socket-proxyd`, the same primitive the pillars already run under.

The `http/json` protocol keeps parsing in the standard library, with no protobuf and no collector. An optional
`forward_to` setting can copy the OTLP stream to an external collector later. The proxy and its port are
decision 2 in [§16](#16-decisions-for-the-owner).

**Alternative considered, not accepted by the owner:** run an OpenTelemetry Collector with Prometheus and Grafana. It works, but it adds
three services. That contradicts the design goal of being smaller than an assembly of tools, and it would still
not do attribution, detectors or jobs.

## 6. Configuration dependencies

Optimization depends on configuration in four places. Each has a different owner and mechanism, so
little-workshop keeps one **desired-state profile per scope**. It checks each profile on a schedule and changes
anything only after you confirm, the same pattern as `tools/sync_claude_files.py` (show the difference, then
apply it, keeping your own entries).

| Scope | Where | What it carries | Who applies it | How it is checked |
|---|---|---|---|---|
| **User** | `~/.claude/settings.json` (applies to every session on this machine) | `env`: `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER=otlp`, `OTEL_LOGS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>`, `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`, `OTEL_METRICS_INCLUDE_REPOSITORY=true`, `OTEL_LOG_TOOL_DETAILS=1` (decision 3). `hooks`: `SessionStart`, `SessionEnd`, `PreCompact` and `PostCompact` as `http` hooks to the intake. `statusLine` (decision 5, on). `cleanupPeriodDays`, raised (decision 4). | **The little-workshop install** applies it, after showing the difference and asking, with the free port it picked (decision 2). `usage.configure_claude` repairs or updates it later (dry run by default; applies only after explicit confirmation; keeps a backup) | `usage.doctor`: profile present, `claude doctor` clean, data received recently |
| **Project** | each project's `.claude/settings.json`, generated by `src/registry/claude_templates.py` | Only what project scope allows: `permissions.deny` `Read(...)` rules for heavy generated paths (`bazel-*`, `node_modules/`, `.venv*/`, Unity `Library/` and `Temp/`), the LSP plugin for the project's language (`enabledPlugins`), a `PreToolUse` hook that cuts test-runner output down to failures, and `claudeMdExcludes` where needed. **No `OTEL_*`**: Claude Code ignores those here. | **Project bootstrap** (`workshop_project_bootstrap`) writes them when a project is set up. At any later moment `lws` or the MCP can add them to one project, or to every project as a job (`tools/sync_claude_files.py --write`, [§10.3](#103-applying-a-common-optimization-to-every-project)) | `claude-config-present`, extended with the new generated entries |
| **Launch** | the environment and flags of each Claude that little-workshop starts (`src/agent/claude_invoke.py`, `session_flow.py`, `doc_update.py`) | The telemetry environment (so runs report even if the user profile is missing), `OTEL_RESOURCE_ATTRIBUTES=lws.project=<id>,lws.job=<job id>,lws.job_type=<type>,lws.run=<run id>`, a budget per job type (`--max-budget-usd`, `--max-turns`), `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` for one-shot calls, and a stable working directory for `agent.query`. Runs started through `systemd-run` pass these with `--setenv`. | Code in the agent pillar | Unit tests; detectors D4 and D5 |
| **Machine** | binaries on `PATH` | Language servers for the LSP plugins: `pyright-langserver` (little-workshop, little-meals, little-mindcheck), `typescript-language-server` (little-home), `clangd` with a Bazel-generated `compile_commands.json` (little-spectrum), `csharp-ls` (ethereal-lands, decision 8) | **The little-workshop install** installs them, and the Claude Code LSP plugins that are missing, in the style of `tools/check_system_deps.sh`: each step is shown and asked first, never silent. `usage.doctor` reports anything that is later missing, with an install hint | `usage.doctor` |

The user profile is the only part that edits a file outside every repository, so it is treated as a decision
that stands in for a human: it always asks first, as Remote Control trust does today. Without the user
profile, the pillar still receives everything that little-workshop launches (launch scope) and the transcript
backfill. Interactive sessions then lose their live data, not their history.

## 7. Collecting the data

### 7.1 Sources

| Source | Covers | Precision | Stability | Notes |
|---|---|---|---|---|
| **S1. Run result** (`claude -p --output-format json`) | every `agent.run_task`, `agent.query` and headless doc update | exact per run: `total_cost_usd`, `usage`, `modelUsage`, `num_turns`, `duration_ms`, `session_id` | documented | The quickest win: the data already arrives (F5) |
| **S2. OpenTelemetry** (OTLP http/json) | every session that has the user profile or the launch environment | per API request (`claude_code.api_request`): the four token counts, estimated cost, model, `query_source`, `request_id`, `prompt.id`; per tool call (`claude_code.tool_result`): tool, success, duration, input and result sizes, and with `OTEL_LOG_TOOL_DETAILS=1` the command, file path, skill or subagent; plus `skill_activated`, `mcp_server_connection`, `api_error`, and metrics such as `active_time` and `lines_of_code` | documented contract | The main live source. It cannot tell a working directory, so attribution comes from the repository attributes and from S3. |
| **S3. Claude hooks** (type `http`, user scope) | every session with the user profile | `session_id`, `cwd`, `transcript_path`; `SessionStart` `source` (and on resume, `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd`); `SessionEnd` `reason` (`clear`, `resume`, `logout`, `prompt_input_exit`, `other`); compaction events | documented | Links a session to its directory and transcript. A failed post is a non-blocking error for Claude Code. |
| **S4. Statusline** (optional) | interactive sessions with the generated statusline | `rate_limits.five_hour` and `seven_day` `used_percentage`, `prompt_cache` (hit ratio, misses, `last_miss_cause`), `context_window.used_percentage` | documented | The only source for plan pressure. It posts only when a value changes. |
| **S5. Transcripts** (`~/.claude/projects/*/*.jsonl`) | every session still on disk, including those from before the profile and those where the intake was down | per request `usage` (including 1-hour and 5-minute cache-write split), `requestId`, `cwd`, `gitBranch`, `entrypoint`, tool calls with full inputs | **internal format**, may change with any release | A backfill with a tolerant parser that records the Claude Code version. It must run before the 30-day cleanup. |

The sources overlap on purpose. `request_id` is the same id in S2 and S5 (`requestId`), and `session_id` is the
same in S1, S2, S3 and S5, so ingesting twice never counts twice. The pattern is that of the `seen` table in
`src/dashboard/statistics.py`.

### 7.2 Sessions independent of little-workshop

**Yes.** User-scope settings apply to every Claude Code session this user starts on this machine: CLI, VS Code
extension, desktop app, `claude -p`. So once the user profile is in place, S2 and S3 arrive from sessions that
never touch little-workshop. S5 covers what came before, and anything missed while the intake was down.

What stays invisible:

- claude.ai chat
- sessions on other machines, unless the profile is applied there and the intake is reachable
- cloud sessions, unless their environment configuration carries the variables
- usage on other devices that counts against the same plan

The organization analytics APIs (Claude Console, Enterprise Analytics) serve API and Enterprise organizations.
They do not apply to a personal subscription, so they would only be an optional adapter.

Privacy:

- Prompts and responses are not logged (`OTEL_LOG_USER_PROMPTS` and `OTEL_LOG_ASSISTANT_RESPONSES` stay off).
  - What turning them on would mean (decision 10): each request's event would also carry the text you typed
    (`OTEL_LOG_USER_PROMPTS`) and Claude's reply text (`OTEL_LOG_ASSISTANT_RESPONSES`), stored in the pillar's
    database next to the token counts.
  - What it would give: repeated wording becomes visible, so a prompt you keep retyping could become a skill or
    a template, and a long reply could be tied to the tokens it cost.
  - What it would risk: anything you paste into a prompt is stored in readable form, such as a key, a
    password, personal text, or the content of little-home and little-mindcheck. It would also be in every
    backup of that database and in any collector the optional `forward_to` setting copies the stream to. The
    store stays local and is never committed, but whoever can read your files can read it.
  - If you choose to turn it on, the usual mitigations are a shorter retention for text than for counts, and
    leaving selected projects out.
- `OTEL_LOG_TOOL_DETAILS=1` records commands and file paths. Automation mining needs them (decision 3).
- Everything stays in the pillar's own database, with a retention setting that never goes below one full month, so week comparisons work ([§9](#9-seeing-the-interpreted-data)). It is never committed.
- Removing a project purges its rows, like every pillar's cascading delete.

### 7.3 Attribution to a project

Each request gets a `project` and an `origin`. The project is the first rule that applies:

1. **Job tag.** `lws.project`, from `OTEL_RESOURCE_ATTRIBUTES`, on runs that little-workshop starts. These are
   exact, and also carry `job`, `job_type` and `run`.
2. **Working directory** (from S3 or S5). The longest registered checkout path that contains it wins.
   little-workshop worktrees (`~/.local/state/little-workshop/worktrees/<id>`) are resolved through Deployment's
   worktree records. `.claude/worktrees/<name>` resolves to the project that contains it.
3. **Repository** (`vcs.repository.name` / `vcs.repository.url.full` on S2, with
   `OTEL_METRICS_INCLUDE_REPOSITORY=true`), matched against the registry's repository URLs.
4. **Workspace.** Sessions in `little-projects` itself (the root, F2's biggest bucket) count as the
   `little-projects` workspace. Optionally they can be split further by the `project_id` of the little-workshop
   MCP calls they make.
5. Otherwise `unattributed`.

The `origin` is one of: `interactive` (with the entry point `cli`, `claude-vscode` or `claude-desktop`), `job`
(with the job type), `query` (`agent.query`), or `subagent` (from `query_source`).

## 8. Compiling the data

### 8.1 Store

| Table | One row per | Main columns |
|---|---|---|
| `sessions` | session | id, project, origin, entry point, cwd, first and last seen, end reason, model(s), job and run ids, Claude Code version |
| `requests` | API request | request id, session, time, model, input, cache read, cache write (1-hour and 5-minute), output, estimated cost, `query_source`, agent or skill name, duration |
| `tool_calls` | tool call | session, prompt id, tool, normalized signature ([§11](#11-automation-mining)), input and result bytes, duration, success |
| `day_rollups` | day × project × origin × model | the token sums, requests, sessions, weighted cost, hit ratio, startup context p50, long-context share, polling share |
| `findings` | detector result | detector, project, period, evidence, estimated saving, recommended action, state (open, accepted, dismissed, resolved) |
| `candidates` | automation candidate | signature, occurrences, sessions, tokens spent, determinism score, proposed form, state |
| `experiments` | applied optimization | finding or candidate, project, the change (branch or commit), metric values 7 days before and after, with the tokens Claude still spends on the replacement |

### 8.2 Metrics

- **Weighted tokens**: tokens of each type × a per-model price from a configurable table. The defaults are the
  published list prices, the same idea as Claude Code's `modelPricing`. It is the one unit that compares cache
  reads, cache writes and output across models. On a subscription it stands in for plan consumption; it is not
  money spent.
- **Cache hit ratio** = cache read ÷ (input + cache read + cache write). The same definition as the statusline's
  `prompt_cache.hit_ratio`.
- **Startup context**: input plus cache tokens of a session's first main-thread request.
- **Average context per request**, and the **long-context share**: the fraction of context tokens in requests
  above 200k. 200k is the threshold the statusline's `exceeds_200k_tokens` uses.
- **Turns per task** and **tokens per outcome**: per finished agent run, per finished branch, per requirement
  turned to passing.
- **Miss rate**: cache rebuilds that compaction does not explain. Claude Code uses the same definition for
  `prompt_cache.misses`.
- **Polling share**: requests whose only tool call is a status poll or a sleep.
- **Tool-output weight**: `tool_result_size_bytes` per tool and per normalized command.
- **Plan pressure**: the highest 5-hour and 7-day usage percentage per day (S4).

### 8.3 Detectors

Each detector is deterministic code with a threshold you can configure. It produces a finding with evidence
and a recommended action. Thresholds start from this machine's baseline.

| Id | Signal | Recommended action | Fix that needs no Claude |
|---|---|---|---|
| D1 | Long-context share above 60% for a project, or sessions above 500k context | Clear between tasks; set a lower auto-compact window (`/autocompact`, `--autocompact`); hand verbose work to subagents; move recurring steps into jobs | Partly: a statusline warning, plus jobs that replace turns |
| D2 | Startup context grew by more than 10% over the project's 30-day median | Find what was added (MCP server, briefing, rules without `paths`, plugins) | Yes, for the part that `claude-instructions-valid` measures |
| D3 | Miss rate above 5% of requests, grouped by cause (`tools_changed`, `system_prompt_changed`, `ttl_expired_5m`, model or effort switch) | Keep model and effort fixed for the session; avoid toggling MCP servers mid-task | Sometimes (a stable MCP configuration) |
| D4 | One-request `-p` sessions writing a 1-hour cache | `CLAUDE_CODE_PROMPT_CACHE_TTL=5m` for that kind of run | Yes |
| D5 | Same-prompt one-shot runs spread over many working directories | Use a stable working directory | Yes |
| D6 | Polling share above 2%, or more than 10 polls in one episode | Use a blocking wait tool, or have the job notify | Yes: `workshop_job_wait` |
| D7 | One command's results average more than 20 kB | A `PreToolUse` filter hook, or a subagent such as `test-runner` | Yes: a generated hook |
| D8 | The same file read in at least 5 sessions in 14 days | Turn it into a skill or a digest that loads when needed | Partly (writing the digest is a task) |
| D9 | Opus or Fable used for an episode whose signature is routine (redeploy, status, sync) | Use Sonnet or Haiku for that work: set the subagent model or the job's model | Yes: model defaults per job type |
| D10 | A raw sequence keeps appearing after a tool or job replaces it (for example, single-pillar redeploys after `redeploy_pillars` existed) | Update the route in the guard, skill or briefing | Yes: `claude_templates.ROUTES` |
| D11 | MCP tools loaded upfront, or a configured server never used in 30 days | Disable the server, or rely on deferred tool search | Yes |
| D12 | Plan pressure above 80% of the 5-hour window during scheduled agent jobs | Move heuristic jobs to quieter hours ([§12](#12-token-usage-as-an-input)) | Yes |

## 9. Seeing the interpreted data

- **Dashboard, System → Claude usage.** Built like the Statistics page (`docs/pillars/dashboard.md`,
  "Statistics"), with a project or origin filter, one time period for all plots, and chips:
  - weighted tokens per day by token type
  - average context per request
  - startup context per project
  - cache hit ratio
  - long-context share
  - plan pressure
  - the top sessions by weighted tokens
  - **Findings**, each with its evidence, a button that opens the matching catalog job
    ([§10](#10-jobs)) and **before / after** numbers: the tokens a specific task used before the optimization
    against the tokens it uses after it. The "after" figure includes the tokens Claude still spends to keep track
    of the new jobs and trigger them, so it shows the net saving and not only the part that moved to code.
  - **Automation candidates**, each with **Make it a task**, **Dismiss** and **Schedule**, which offers
    recommended settings (see below)
- **Scheduling recommendations.** For each automation candidate, even before it is automated, the Dashboard
  recommends how often to run it, from how often its episodes occurred. Work that does not need to run beside
  other work is recommended for the middle of the night, one task after another in sequence, so it runs while
  you sleep and does not compete with your own sessions for the plan's usage
  ([§12](#12-token-usage-as-an-input)). Accepting a recommendation creates the scheduled task in Orchestration.
- **Weeks.** A week starts on Monday, because the weekly token allowance resets then. The Dashboard page and
  each project's "Claude usage" card show one chosen week, and you can go back as far as data is stored. The
  store keeps at least one full month. A week's graph can **Compare to previous week**, **Compare to next
  week** and **Compare with last 4 weeks average**, each only when that data exists. The comparisons are lines
  on the same graph, so differences are visible at a glance. The current week may be incomplete, and it is still
  shown against the previous week to see how it is going.
- **Each project's page** gets a "Claude usage" card (the current week, with the week selector above).
  **Each agent run** shows its own tokens, turns and estimated cost (from S1).
- **Does little-workshop pay for itself?** One chart compares the tokens spent building little-workshop with
  the tokens spent on every other project, week by week ([§3](#3-what-the-data-on-this-machine-already-says)).
  Building it is an investment. It pays off only if work on the other projects gets cheaper, and this chart,
  with the before / after numbers above, is how that is checked.
- **MCP tools**, read-only and allowed in every project: `workshop_usage_summary`, `workshop_usage_findings`,
  `workshop_usage_candidates`. A session can then check its own project's numbers before it optimizes anything.
- **`lws usage summary | findings | candidates --project-id <id>`**.
- **Inside a session**: an optional generated statusline (context %, cache warm or cold, 5-hour usage %), next
  to Claude Code's own `/usage`, `/context` and `/insights`.

## 10. Jobs

### 10.1 Job types

| Job type | Pillar | Started by | Uses Claude? | What it does |
|---|---|---|---|---|
| `usage.ingest_transcripts` | usage | daily schedule; after `SessionEnd` | no | Backfills S5 into the store; dedupes by request id |
| `usage.rollup` | usage | the pillar's own tick (10 min) | no | Builds the per-day rollups from new rows |
| `usage.analyze` | usage | daily schedule | no | Runs the detectors and opens or resolves findings |
| `usage.doctor` | usage | weekly schedule; on demand | no | Checks the profiles against reality; runs `claude doctor` and records its output; checks language-server binaries; checks that each recently active project sent data |
| `usage.configure_claude` | usage | you (always asks) | no | Applies the user profile to `~/.claude/settings.json` after showing the difference |
| `usage.mine_automation` | usage | weekly schedule | no | Mines episodes into candidates ([§11](#11-automation-mining)) |
| `usage.measure_experiment` | usage | 7 days after an optimization lands (scheduled when the branch finishes) | no | Records the before and after metrics |
| `registry.sync_claude_files` | registry | you, or a trigger after `claude_templates.py` changes | no | Runs `sync_claude_files.py --write` on every project, each on its own improvement branch |

New catalog entries (`src/dashboard/job_catalog.py`, typed `JobSpec`s, never free-form):

- **Optimize Claude usage** (every project). Arguments: the open findings to address (`multi`, fetched per
  project) and the period. It runs `agent.run_task` with intent `improvement`, `review_mode` `manual`, and a
  fixed description. The description embeds a findings digest that the pillar generates deterministically, so
  the agent does not spend tokens rediscovering the evidence.
- **Apply common Claude configuration** (little-workshop only). It runs `registry.sync_claude_files` across
  every project, then `policy.check_all` for `claude-config-present` and `claude-instructions-valid`.
- **Configure Claude telemetry** (little-workshop only). It runs `usage.configure_claude`.

### 10.2 Applying an optimization to one project

A finding leads to **Optimize Claude usage** on that project. That runs `agent.run_task` on a branch made by
the version pillar, with the policies gating the finish and a human review. After it merges,
`usage.measure_experiment` compares 7 days before and after. A finding is resolved only when its metric
actually moves. What a per-project change touches:

- that project's `AGENTS.md`, `.claude/rules/`, skills and subagents
- its deny paths and test-output hook, through the project's own settings entries, which
  `sync_claude_files.py` preserves
- its code, when a deterministic tool belongs in the project

### 10.3 Applying a common optimization to every project

A change that every project should share is made **once, in little-workshop**:

- `claude_templates.py`: generated settings, guard, skills, rules, statusline, test-output hook
- `tools/policy_scripts/`: the policies that hold the change in place
- `claude_invoke.py` defaults: model and effort per job type, TTL, budgets
- the user profile

That change itself goes through little-workshop's own improvement branch, review and policies. Then
**Apply common Claude configuration** carries it to every project, and `workshop_policy_check_all` confirms it
everywhere. The rule for where a change belongs: if every project would make the same edit, it is a template
or a policy, not one agent task per project.

## 11. Automation mining

The principle already stands in `docs/pillars/agentic_development.md` ("Not every Job goes through this
pillar"): a typed, deterministic action with one correct outcome goes straight to the pillar that owns it, with
no agent involved. Mining finds the actions that Claude still performs by hand.

### 11.1 Episodes and signatures

- An **episode** is the sequence of tool calls between two user prompts (one `prompt.id` in S2, or the same
  span in S5).
- Each call is **normalized** into a signature: tool name plus normalized arguments. Paths become
  project-relative patterns; ids, hashes and numbers become placeholders; file contents are dropped. For
  example: `mcp:workshop_deployment_redeploy_pillar(pillar=<pillar>)`, or `Bash(python3 -m pytest <path>)`.
- A **candidate** is a signature, or a short sequence of signatures, that recurs in at least *K* sessions or
  *M* times within the period (defaults: 3 sessions, 10 times, 14 days).

### 11.2 Deterministic, hybrid or heuristic

Each candidate gets a determinism score from five checks:

| Check | Deterministic when | Heuristic when |
|---|---|---|
| Argument derivability | Every argument comes from repository or pillar state, or from an earlier tool result in the same episode | Arguments are text Claude wrote |
| Output stability | The same inputs produce the same sequence across sessions | The sequence branches on reading and interpretation |
| Checkability | A test, a policy or a pillar state can verify the outcome | Only a human can judge the outcome |
| Content creation | No Write or Edit of new prose or code | Writes new code, documentation or EARS text |
| Branching | At most a few known outcomes (succeeded, failed, refused) | Open-ended |

- **Deterministic** (passes every check) becomes a job, an MCP tool, a pipeline step or a trigger.
- **Hybrid** keeps Claude only for the core of the work and surrounds it with code: a preprocessing hook, a
  context digest written before the agent starts, `--json-schema` output that code then acts on, or a
  `test-runner`-style subagent.
- **Heuristic** stays with Claude, and only its context is optimized: a skill instead of exploration, a subagent
  for verbose steps, the right model.

Each candidate's **value** is the weighted tokens its episodes cost per week, times the expected frequency.

### 11.3 Candidates already visible on this machine

The owner has accepted all of these candidates. They are scheduled in [§15](#15-phases): the `agent.query`, polling and redeploy ones in phase 0, the policies digest and the test-output hook in phase 2, `requirements.sync_markers` and the redeploy trigger in phase 3.

| Pattern (F6, F7) | Class | Proposed form |
|---|---|---|
| Redeploy pillars one by one after an improvement finishes on little-workshop | deterministic | A trigger after `version.improvement_finish` on little-workshop runs `deployment.redeploy_pillars` for the pillars whose `src/<pillar>/` changed, in `REDEPLOY_AFTER` order, still confirm-gated as today |
| `requirements_create` plus `link_suite` for test markers | deterministic | A `requirements.sync_markers` job that reads `# --- REQ-nnnnnnnnn:` sections and creates and links what is missing. `requirement-marker-registered` already detects the gap. |
| Polling job status with `sleep` or `timeout` | deterministic | A `workshop_job_wait(job_id, timeout)` long-poll tool. Each removed poll saves one full context re-read. |
| Running the unit suite and reading all of its output | hybrid | The suite runs as code and returns a failure digest; Claude reads only the digest. This is the `test-runner` pattern, generated as a hook into every project. |
| Rereading `automation_policy.md` and other policy documents | hybrid | A generated "policies digest" skill, or `policy-preflight` answering the question directly |
| Policy authoring through `agent.query` | heuristic, but its cost can be cut | A stable working directory, a 5-minute TTL, a minimal launch (`--strict-mcp-config`, `--disable-slash-commands`), and S1 usage recorded (F4, F5) |

### 11.4 What the mining produces

A candidate record holds:

- evidence: example episodes, sessions and tokens spent
- the proposed form
- an estimated weekly saving

You choose **Make it a task**, which opens an improvement task on little-workshop with a fixed template, or
**Dismiss**. Nothing is implemented automatically. After the job or tool exists, every run of it costs zero
tokens. The pillar records "tokens avoided" as the number of times the replacement ran × the median tokens of
the episodes it replaced. Detector D10 watches that Claude actually uses it.

## 12. Token usage as an input

- **Record it.** Tokens and estimated cost per job, from S1, plus S2's attribution for interactive work.
- **Budget it.** Each job type declares defaults: model and effort (today one default,
  `claude-sonnet-5-5` with effort `medium`, in `src/agent/claude_invoke.py:12`), `--max-budget-usd`, and
  `--max-turns` where a bound is natural. The agent run records how much of its budget it used.
- **Schedule around it.** Before dispatching a job that uses Claude, Orchestration asks the usage pillar for
  the current plan pressure (S4, or the latest rate-limit error). Above a threshold (decision 6), the job waits
  in its queue for a quieter time. Deterministic jobs never wait for this.
- **Rank by it.** Findings and candidates are ordered by weighted tokens: what a fix saves, not how
  interesting it is.
- **Report outcomes, not only spend.** Tokens per finished task, per finished branch and per requirement turned
  to passing show whether an optimization made the work cheaper or only shorter.

## 13. Workshop triggers: hooks beyond git

git hooks run code at fixed points in git, and Claude Code hooks run code at fixed points in a Claude session.
little-workshop already uses both: `.githooks/pre-commit` and `commit-msg`, and the generated `PreToolUse`
guard. The same idea one level up is a **trigger**:

```
Trigger { id, name, on: { event, filter }, job_template, enabled }
```

- **Ownership.** Orchestration owns triggers next to `ScheduledTask` and `Pipeline`, with the same
  `job_template` shape. A schedule fires on time; a trigger fires on an event.
- **Events.** The first version needs no event bus. Most events worth reacting to are a Job settling (a branch
  finish, a release, a policy check, a build), and Orchestration already sees every Job settle. Later, the
  pillars that own other events can emit them, for example:
  - `job.settled` (type, project, outcome)
  - `version.branch_finished`, `version.release_finished`
  - `policy.result` (failed)
  - `registry.project_registered`
  - `claude.session_ended` (from S3)
  - `usage.finding_opened`
- **Safety:**
  - A Job created by a trigger has `created_by: "trigger:<id>"`.
  - Gated job types still stop in `pending_confirmation`.
  - The hard invariant holds: a trigger cannot target a job type that merges, finishes or deletes. The check
    happens when the trigger is created, not when it fires.
  - A Job created by a trigger cannot fire that same trigger again, and a chain of triggers has a depth limit.
- **Examples:**
  - After an improvement or feature finishes on little-workshop: redeploy the pillars it changed.
  - After a commit on little-workshop that changes `claude_templates.py`: propose `registry.sync_claude_files`.
  - After `claude.session_ended`: ingest that session's transcript.
  - After `usage.finding_opened` for D4, D5 or D11: open the matching configuration change as a draft.
  - After `registry.project_registered`: run `usage.doctor` for the new project's language servers.

| Layer | Fires on | Example | Tokens |
|---|---|---|---|
| git hook | commit, push | `commit-msg` runs the commit-gating policies | 0 |
| Claude Code hook | tool call, session start and end, compaction | the guard routes raw `git merge` to `workshop_version_*`; `SessionEnd` posts to the intake | 0 |
| Workshop trigger | any pillar event | improvement finished, then redeploy the changed pillars | 0 |
| Claude | a judgment is needed | implementing a feature, interpreting a failure | yes |

## 14. Keeping the optimization inside the framework

- **Every change takes a little-workshop route:** an agent task on a version-pillar branch, gated by policies
  and reviewed by you. Optimization jobs are catalog entries, not free-form prompts.
- **Generated files and policies hold the result in place.** `sync_claude_files.py` rewrites the generated
  files, and `claude-config-present` and `claude-instructions-valid` fail a branch finish that loses them. An
  improvement that every project shares lives in little-workshop and cannot drift in one project unnoticed.
- **New routes are announced where Claude looks.** When a candidate becomes a tool or a job, it is added to
  `claude_templates.ROUTES` (so the guard names it when it refuses the raw command) and to the
  `workshop-lifecycle` skill.
- **Adoption is measured.** Detector D10 notices when Claude keeps doing by hand what a route now does, and
  opens a finding against the route's wording, not against Claude.
- **Results are checked.** An experiment that does not move its metric reopens the finding.
- **Policies judge a checkout; the usage pillar judges behaviour.** Startup-context growth or long sessions are
  findings, not policies, because a sandboxed policy check cannot see them. Whatever is checkable in the
  checkout (deny rules present, LSP plugin enabled, hook registered) becomes part of `claude-config-present`.

## 15. Phases

Each phase is shippable on its own. Its requirements get `REQ-nnnnnnnnn` ids when it is scheduled.

| Phase | Content | New pillar? |
|---|---|---|
| 0. Quick wins | Store S1 usage on agent runs, queries and doc updates (F5). `agent.query`: stable working directory, `CLAUDE_CODE_PROMPT_CACHE_TTL=5m`, minimal launch flags (F4). `workshop_job_wait` (F6). Single-pillar redeploys replaced by `redeploy_pillars` in the skill and `ROUTES` (D10). The aim is to learn whether little-workshop saves tokens or costs them: P0 records the token usage that little-workshop's runs already report, fixes `agent.query` and adds the wait-for-job tool. Once data is collected, the number to follow week by week is tokens spent building little-workshop against tokens spent on the other projects ([§9](#9-seeing-the-interpreted-data)). | no |
| 1. Collection | The `usage` pillar: intake (OTLP http/json, hooks), store, transcript backfill, attribution, rollups; `usage.configure_claude` and the install step that picks the port and applies the user profile; `usage.doctor`; the Dashboard page with plots, week selection and week comparison | yes |
| 2. Findings | Detectors D1 to D12; the "Optimize Claude usage" job; experiments; `registry.sync_claude_files`; project-scope profile entries in `claude_templates.py` (deny paths, LSP plugin, test-output hook), also written by project bootstrap; the policies-digest skill; before and after numbers on findings | – |
| 3. Automation | `usage.mine_automation`; candidate records on the Dashboard; Orchestration triggers; the first triggers (redeploy changed pillars, sync Claude files); `requirements.sync_markers`; scheduling recommendations for candidates | – |
| 4. Token-aware operation | Budgets and models per job type; scheduling around plan pressure; tokens-per-outcome reporting | – |

## 16. Decisions for the owner

Answers from the owner's review of 2026-10-04 are marked **Decided**. Item 10 is new and still open.

1. **A new pillar `usage`, or an extension of Agentic Development?** **Decided: a new, optional pillar**
   ([§5](#5-architecture)).
2. **A loopback socket proxy for the intake** (`127.0.0.1`, through `systemd-socket-proxyd`). The pillar
   itself keeps to its Unix socket. The proxy is needed because Claude Code exports and posts hooks over HTTP.
   **Decided: find a free port.** The install looks for an unused loopback port instead of assuming 4318 (the
   standard OTLP/HTTP port, which another tool may already hold), keeps it in little-workshop's configuration
   and writes it into the user profile ([§6](#6-configuration-dependencies)).
3. **`OTEL_LOG_TOOL_DETAILS=1`**: commands and file paths are stored locally. Automation mining needs them.
   Prompts and responses stay off (see item 10). **Decided: on.**
4. **Transcript history**: import before the 30-day cleanup, raise `cleanupPeriodDays`, or both.
   **Decided: both.** This is a personal computer with limited space, so the pillar's own store, not the raw
   transcripts, becomes the long-term record.
5. **A generated user statusline.** It is the only source of plan pressure and live cache state, and it
   replaces most of the footer's keyboard hints. **Decided: on**, applied with the user profile.
   The owner also wants to stop repeating the same instructions in prompts. The statusline does not solve that;
   the part that can is detecting repeated prompt text, which depends on item 10.
6. **How much of the 5-hour usage window may be used before little-workshop holds back Claude-based scheduled
   jobs?** Claude's plan has a 5-hour usage window, and the statusline (S4) shows how full it is. Above the
   limit you choose, little-workshop does not start the next scheduled job that uses Claude (an agent run, for
   example) and keeps it in its queue until the window has room again, so those jobs never use up what you need
   for your own work. Jobs that run only code never wait. Proposed limit: 80% full. At 80%, a scheduled agent
   run waits, and a policy check still runs.
7. **May triggers start gated jobs?** They would then wait in `pending_confirmation`. **Decided: yes.**
8. **Code intelligence for ethereal-lands.** `csharp-ls` would run in WSL against `/mnt/c`, where Claude Code's
   documentation warns of slow cross-filesystem reads. **Decided: keep it.** More projects will live natively
   on Windows, and the same plugin and server serve them.
9. **To verify before building:**
   - whether `/doctor prompt-audit` runs under `claude -p`; otherwise it needs a Remote Control session
   - whether `enabledPlugins` in project settings is enough on a machine that has not installed the plugin. The
     install installs the plugins that are missing ([§6](#6-configuration-dependencies)), so this only decides
     whether the install also has to enable them.
   - the exact OTLP JSON payloads, tested against a real export
   - whether `--strict-mcp-config` and `--disable-slash-commands` keep `agent.query`'s answers unchanged. This
     is tested and decided during development.
10. **Logging prompt and response text** (`OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_ASSISTANT_RESPONSES`). Open: the
    owner is willing to give up some privacy but wants to understand it first, which
    [§7.2](#72-sessions-independent-of-little-workshop) now explains. Recommended until decided: off.

## 17. Sources

Claude Code documentation, read 2026-10-03:

- Monitoring (OpenTelemetry): https://code.claude.com/docs/en/monitoring-usage
- Manage costs: https://code.claude.com/docs/en/costs
- Prompt caching in Claude Code: https://code.claude.com/docs/en/prompt-caching
- Hooks reference: https://code.claude.com/docs/en/hooks
- Run Claude Code programmatically (`-p`, JSON output, `--bare`): https://code.claude.com/docs/en/headless
- CLI reference: https://code.claude.com/docs/en/cli-reference
- Manage sessions (transcript location and retention): https://code.claude.com/docs/en/sessions
- Status line (cost, context, prompt cache, rate limits): https://code.claude.com/docs/en/statusline
- Debug your configuration (`/context`, `/doctor`, `claude doctor`): https://code.claude.com/docs/en/debug-your-config
- Troubleshooting (`/doctor`, `claude doctor`): https://code.claude.com/docs/en/troubleshooting
- Memory (CLAUDE.md, AGENTS.md, size, `/doctor prompt-audit`, `claudeMdExcludes`): https://code.claude.com/docs/en/memory
- Code intelligence plugins: https://code.claude.com/docs/en/plugins/code-intelligence
- API prompt caching and pricing: https://platform.claude.com/docs/en/build-with-claude/prompt-caching

little-workshop, as of `dev` at 0.2.35:

- `src/agent/claude_invoke.py`, `src/agent/flow.py`, `src/agent/query.py`, `src/agent/doc_update.py`
- `src/dashboard/statistics.py`, `src/dashboard/job_catalog.py`
- `src/registry/claude_templates.py`, `tools/sync_claude_files.py`, `tools/check_system_deps.sh`
- `docs/design.md`, `docs/architecture.md`
- `docs/pillars/orchestration.md`, `agentic_development.md`, `dashboard.md`, `policy_compliance.md`

The baseline in [§3](#3-what-the-data-on-this-machine-already-says) comes from a one-off read of
`~/.claude/projects/*/*.jsonl` on 2026-10-03.
