# Agentic development review (2026-10-24)

A read-only review of little-workshop, written after this request from the owner: *reduce the guardrails that make
agentic development too strict, build a complete development pipeline in which only Claude writes code, show each
project's development more clearly, and let the owner react quickly and easily when agentic work needs attention.*
Nothing in this review is implemented yet. It lists what is there today, with evidence, and proposes what to change,
in phases. The deck for this review is a private claude.ai artifact:
<https://claude.ai/artifact/CBWZBrweyqJ7nvaLARR1SJ>.

Code references are to `improvement/newest-request-wins` at `VERSION` 0.2.74-dev.4. Line numbers will drift.

## 1. Summary

**Finding 1: little-workshop's own refusals are not the main cost.** In the last 30 days the guard hooks refused
about 25 calls and the commit hook refused 3. About half of the guard's refusals were false positives. The costly
blocks came from elsewhere:

| Blocker | Count, 30 days | Status |
|---|---|---|
| Requirement-form (EARS) refusals | 60 out of 256 requirement writes | Relaxed to a warning on the open branch, not deployed yet |
| Daemon timeouts on gated routes (`run_tests`, finishes, releases) | 45 | Open |
| Dirty-tree and branch preconditions | about 20 failed or cancelled jobs | Open |
| Claude Code's auto-mode classifier denying lifecycle steps | about 32 | Open. One finish ended with the owner running git by hand |
| Agent runs waiting for a human | 2 runs, in `awaiting_finish` for 3 and 12 days | Open |

**Finding 2: the strictness and the protection are in different places.** Small rules of form refuse an agent at
commit time. Meanwhile the real gate is missing from the path an unattended agent takes:

- `agent.finish_task` and auto-approve merge branches without running any `branch_finish` policy.
- Auto-approve accepts any non-empty, self-reported `test_results`.
- No machine-run Definition-of-Done test run happens between "Claude wrote code" and "branch merged".

**Finding 3: what a Claude-only pipeline lacks** is a verify step, a fix-up loop, a review stage, isolation, and a
route for questions back to the owner.

- **Verify:** run the tests and policies after the agent's code.
- **Fix-up:** feed the failures back to the same session on the same branch.
- **Review:** a reviewer agent and a "request changes" action.
- **Isolation:** a worktree per run, a lock per project, and runs that survive a pillar redeploy.
- **Questions:** today questions only go to a Remote Control session nobody may be watching.

**Finding 4: the owner learns that something needs them only by looking.** There is no push notification. A held
action expires after 10 minutes. The attention list misses most of what needs a person, and its question card carries
a button that stops the job.

**The direction this review proposes:** fewer gates, each one real.

1. Turn form checks into warnings or automatic fixes.
2. Keep one gate, at the finish, that every path goes through, agents included.
3. Have the agent repair a failing gate on its own before a person is asked.
4. When a person is needed, push one inbox item with one-tap actions, and a deadline long enough to reach them.

## 2. Method

- **Code and docs:** four parallel reads of the guardrails (hooks, git hooks, policy scripts, the action gate,
  finish preconditions), the agentic pipeline (`src/agent`, `src/orchestration`, `src/requirements`, the backlog), and
  the information and attention surfaces (Dashboard, `/status`, the Claude Code status mod, CLI, MCP).
- **Spot checks:** the main claims were re-read in the code before they were written here: the ungated agent finish,
  the global run lock, the auto-approve condition, the 12 s version reply, the fail-open gate, the hotfix version
  trap, the missing push handler and the attention question card.
- **Evidence:** 214 Claude Code transcripts from the last 30 days (549 errored or hook-flagged tool results, 901
  owner messages), the pillars' SQLite state read in read-only mode, and `git log`.

## 3. Where the friction comes from

Ranked by how often it happened, 2026-09-24 to 2026-10-24.

1. **EARS refusals: 60 refusals out of 256 `requirements.create_requirement` jobs.**
   - Most were in little-workshop, 24 of them on 10-04. The other 12 were in little-trading.
   - The owner's own note: rewording a requirement to satisfy the form "protects nothing… Make it a warning."
   - The open branch already makes it a warning (REQ-000000450).
   - Still left: the policy-authoring Gate 1 (`src/policy/gates.py:20`).
2. **Daemon timeouts and unreachable pillars: 45 tool errors.**
   - `workshop_requirements_run_tests` "has not answered within 30s": 29 times.
   - `improvement_finish` "orchestration daemon unreachable": 9 times.
   - A `policy.check_project` was still running after 6 hours.
   - The owner, 09-24: "you triggered a test job that was up for 222 minutes". The owner, 10-03: "it is stuck… cancel
     them. try to finish the branch again."
3. **Claude Code's auto-mode classifier: about 32 denials.**
   - It denied lifecycle steps such as `redeploy_pillar`, `sync_policies.py --apply`, `job stop` and
     `workshop_policy_confirm`.
   - The worst case, 10-01: a finish failed on a rebase conflict and the classifier refused the conflict resolution
     three times, even after the owner gave permission. The owner then ran git by hand.
   - This is Claude Code, not little-workshop, but little-workshop can avoid it. The finish could resolve its own
     conflicts as a gated job, so no free-form git is needed.
4. **Scope guard: 17 cross-project refusals, about 11 of them false positives.**
   - Pillar names read as project ids: the `deployment` and `dashboard` self-redeploy confirmation.
   - Policy-only calls with no `project_id` refused as "acts on every project".
   - Text inside a pipe, and a shell loop variable, parsed as project ids.
5. **Working-tree preconditions: about 20 jobs.**
   - "Working tree is dirty" on start and finish.
   - Two agent runs never started because the checkout was dirty.
   - Five projects now hold uncommitted generated Claude files, which will trip this again.
6. **Headless runs missing tool permissions: 9 denials.**
   - One retry loop: 8 identical `requirements_delete` calls in little-trading.
   - In `claude -p`, an `ask` rule never reaches a person, so that tool is simply unavailable.
7. **Policy holds: 7 in total, the last on 10-02.**
   - The friction was in the confirm screens: "does not let me confirm", "There is no button to confirm again".
   - The owner's bypass (`sudo lws bypass_policies`) has never been used.

**The pattern:** agents almost always stop and hand over to the owner rather than loop. So each refusal costs a
human round-trip, which is exactly what a Claude-only pipeline must avoid. Branch churn agrees:

- 12 of the last 65 branches were about the guard, policy or gate machinery.
- 47% of non-merge commits only bump `VERSION`.

## 4. Guardrails: keep, relax, fix

### 4.1 Keep: real protection, cheap, and the refusal names the route

- **Raw lifecycle commands stay refused:** `git commit --no-verify`, `core.hooksPath` overrides, raw `git merge` and
  `cherry-pick`, `git reset --hard`, `systemctl --user start|stop`, `systemd-run`, `twine`, `gh pr|release`.
- **No hand edits** of `CLAUDE.md` or of the generated `.claude/` files and `.githooks/`.
- **`live_install_guard.py`:** it guards a real accident, the live pillars stopped on 2026-09-21.
- **Commit-time content checks:** `no-committed-secrets`, `lfs-tracked-files-are-pointers`,
  `unity-meta-files-paired`, `requirement-marker-not-superseded`, `version-format`.
- **The commit hook fails open** when the daemon or sandbox is down, because the finish checks again.
- **Release and publish checks:** `production-*`, `rc-*`, `wheel-filename-has-version`.
- **Human-only decisions stay human-only:** the policy bypass is the owner's alone, and `workshop_policy_confirm`
  asks.

### 4.2 Relax: too strict for unattended agents

| Guardrail | Where | Proposal |
|---|---|---|
| EARS form at policy authoring (Gate 1) | `src/policy/gates.py:20` | Warn, as the requirements pillar now does |
| `merge-commit-message-format` on a branch finish | `merge_commit_message_format.py:56-69` | It judges dev's and master's *existing* tips, so one bad merge from the past holds every later finish, and no agent route can fix it. Check it after release and hotfix finishes instead, or warn |
| `back-merge-no-ff`, `hotfix-propagates-by-merge` on a branch finish | `back_merge_no_ff.py:62-73` | They judge the previous release, not the action being gated. Move them to post-completion checks on `release_finish` and `hotfix_finish` |
| `python-test-coverage` on every finish | `python_test_coverage.py:59-67` | It runs the whole suite a third time (after the DoD run and the preflight) with a 25-minute timeout, even on docs-only branches. "Could not measure" counts as a fail. Reuse the DoD run's coverage, skip when the branch has no `src/` change, and report "could not measure" as a fault |
| `commit-cites-requirement` | `commit_cites_requirement.py:44-79` | It judges HEAD only: a trailing docs commit gets around it, and a fix needs an amend, which is refused (§4.3). Judge `dev..HEAD` as a warning, or drop it |
| A held job keeps the project queue | `queue.py:14-36`, `tools.py:256-262` | A retried finish waits behind its own held job. Let a new finish of the same type supersede the held one, or let an agent decline its own held job (`workshop_job_stop` is `ask`, which `claude -p` cannot answer) |
| Scope guard on cross-project reads | `lws_scope_guard.py:38-44, 129-143, 264-277` | Guard mutating calls only. Treat pillar ids (`deployment`, `dashboard`) as pillars. Allow policy-only calls. Stop parsing text inside pipes and `$var` as project ids |
| Read-only and recovery git | `lws_scope_guard.py:190-200` | Allow `git tag -l/--contains`, `git push --dry-run`, `git clean -n`, `git merge/cherry-pick --abort`, and deleting a local branch that is unmerged and was never pushed |
| `git push` | settings `deny` | Make it `ask` in project sessions, as the super-repo does. The guard's own text says "a push the user asks for is theirs to run" |
| `ask` rules in headless runs | `claude_invoke.py:47` and settings | Give agent runs an explicit allowlist per intent instead of rules that can never be answered |
| Dirty-tree refusal on agent start | `flow.py:96-100` | Run in a worktree (§5.3), so the owner's checkout never blocks a run |

### 4.3 Fix: bugs, traps with no route, silent fallbacks

1. **Hotfix commit trap.** No agent route gets a commit through on a hotfix branch.
   - `hotfix_start` writes no `VERSION`, and the pre-commit hook skips `hotfix/*` (`.githooks/pre-commit:13-16`).
   - So the first fix commit has master's `VERSION`, and `commit-bumps-version` refuses it
     (`commit_bumps_version.py:125`).
   - Its fix message says "raise VERSION yourself", but the guard refuses that edit.
   - If the agent raises it through Bash anyway, `hotfix_finish` bumps it again and skips a number.
   - **Fix:** sync `x.y.(z+1)-dev.N` on `hotfix/*` in the pre-commit hook, and add a unit test.
2. **`git commit --amend` is refused.** `pending_commit` and the evaluate endpoint treat HEAD as the parent, so an
   amend never "raises" `VERSION` (`src/cli/commit_info.py:39-46`, `policy/app.py:933-937`). Make both amend-aware;
   no test covers this today.
3. **The gate fails open when Policy is down.**
   - `policy_gate.py:102-109` dispatches ungated when the pillar can't be reached, while
     `policy_compliance.md:61` says "fail-closed".
   - A check that runs but can't reach Docker fails the job with an opaque "dependency failed", so a dead pillar gates
     *less* than a dead sandbox.
   - **Fix:** record and alert on an ungated dispatch, and copy the dependency's error into the gated job.
4. **Stale holds.**
   - The `systemd-run` timer's result is ignored (`policy_gate.py:196-200`).
   - Transient units do not survive a WSL restart, and nothing expires or re-arms `pending_confirmation` jobs at
     startup.
   - So a held job can keep its project's queue indefinitely.
5. **Held jobs don't say why they are held.** Put the failing topics, their details and their fixes on the job.
   Have the lifecycle skill say "fix the cause and finish again", which is the autonomous route; today it only says
   "leave the confirmation to the user" (`claude_templates.py:1014-1015`).
6. **Worktree sessions.** Version jobs and `policy.check_project` act on the *registered* checkout. A Claude worktree
   session therefore cannot finish its own branch. Worse, it would finish whatever branch the main checkout is on,
   and the preflight would judge a different tree than the gate does.
7. **`test_output_filter.py` returns `permissionDecision: "allow"`** (`:35-43`), which grants permission it doesn't
   need. Return only `updatedInput`.

## 5. A complete pipeline in which only Claude writes code

### 5.1 The pipeline today

| Stage | Today | Who starts it | Where it stops or breaks |
|---|---|---|---|
| Idea → backlog | `POST /v1/backlog` (`registry/app.py:316`) | Owner | Incidents and usage findings skip the backlog |
| Refine → prompt | Read-only Claude, $3, 600 s (`registry/backlog.py:21-59`) | Owner, one click per step | One-shot. Open questions are not asked through `job_answer`. It can't see requirements or other entries |
| Backlog → task | "Launch task" pre-fills a form (`dashboard/app.py:1667-1690`) | Owner | The run does not record its backlog entry. The agent deletes the entry before review (`DELETE_INSTRUCTION`) |
| Branch | `auto` mode: Claude runs `git checkout -b` itself (`flow.py:102-154`) | Agent | Runs in the owner's checkout. Refuses a dirty tree. Branch names come from the title and collide on retry |
| Code + tests | `claude -p --dangerously-skip-permissions`, Sonnet, 1 h, no budget (`launch.py:330-338`) | Agent | The prompt lacks the test command, requirement ids, gating policies and previous attempts. A timeout leaves orphaned child processes. A redeploy of the agent pillar kills the run |
| DoD test run | **None in the task flow.** Only `agent.ci_check`, started by hand | — | The tests are only what the agent says they were. `run_tests` defaults to pytest, and results are not tied to a commit |
| Review | `_publish`: `git add -A`, commit, push, PR (`flow.py:206-222`) | Agent, then owner | No reviewer agent. No "request changes". Retry starts from scratch. A commit-hook refusal or push failure crashes the worker. The promised screenshots never run |
| Finish | `agent.finish_task` or auto-approve → `version.*_finish` called directly (`version_client.py:36-51`) | Owner or auto | **No policy gate.** No `branch_finished` event, so the redeploy trigger never fires. A finish over 12 s is reported as failed while it carries on |
| Release | `version.release` (`release_job.py`) | Owner or schedule | Test conditions are opt-in. Pytest-only |
| Deploy / publish | `deployment.deploy`, build before stop | Owner or release | No automatic rollback on health, only on the confirm timer |

Concurrency: one process-wide lock (`agent/app.py:134,213,313`) runs one task at a time across *all* projects. A
finish on project A waits behind project B's hour-long run, hits its 20 s dispatch timeout and is requeued ("may run
twice"). The pillar doc says "different projects run fully in parallel".

### 5.2 The target pipeline

```
intent ──▶ backlog ──▶ refine ⇄ owner answers (inbox) ──▶ owner approves plan (one tap)
                                                              │
       ┌──────────────────────────────────────────────────────┘
       ▼
  worktree + branch ──▶ Claude codes ──▶ VERIFY (DoD suite + finish policies, by machine)
                              ▲                 │ fail
                              └── fix-up (resume session, capped) ◀──┘
                                                │ pass
                                                ▼
                                 REVIEW agent (diff vs. acceptance criteria)
                                                │ clean
                                                ▼
                       owner: Finish / Request changes  (or auto when policy allows)
                                                ▼
                         gated finish job ──▶ branch_finished ──▶ redeploy / release / deploy
```

The owner has three touch points: intent, plan approval, and the final finish (optional per project). Every other
transition is done by machine, and each refusal is first sent back to Claude to fix.

### 5.3 Changes, by stage

1. **Route agent finishes through Orchestration.**
   - `flow.finish_task` and auto-approve create a `version.<type>_finish` Job (`created_by: agent:<run>`) and wait on
     it. This replaces the direct call.
   - The gate applies, holds show on the task, `branch_finished` fires, and the 12 s and fake-`agent-flow` bugs go
     away.
2. **A verify step after `run_claude`.**
   - It runs the project's DoD suites at the branch head (`requirements.run_tests`), then the `branch_finish`
     policies (`policy.check_project`).
   - Auto-approve only on a green verdict from the machine, recorded on the run and shown in the report.
   - Needs `test_command` stored per suite or per project (`TestSuite` has none) and `commit_sha` on `TestResult`.
3. **Fix-up loop.**
   - On a failed verify, resume the same session (`--resume <session_id>`, already stored) with a `fixup.md` prompt
     carrying the failing tests and the policy fixes.
   - Capped by `max_fix_attempts` and a `max_budget_usd`. `run_task` has no budget today.
   - The same path serves a **Request changes** button (`agent.revise_task {run_id, feedback}`).
4. **Isolation.**
   - A worktree per run, provisioned by Deployment as update-docs already is.
   - Claude in its own `systemd-run --user` unit, resumed after a redeploy like `resume_doc_updates`.
   - A lock per project, and finish and skip on their own short lock.
   - On failure, commit the work in progress to the branch and drop the worktree. Kill the whole process tree on
     timeout.
5. **A review stage.** A new `agent.review_task`: a read-only Claude compares the diff with the refined entry's
   acceptance criteria and the standards. Blocking findings go to the fix-up loop.
6. **Backlog chaining and questions.**
   - Record `source: {type: backlog, entry_id}` on the run, and archive the entry when the finish succeeds instead of
     having the agent delete it.
   - Send the refinement's open questions as attention steps (`common/job_steps.ask`).
   - An optional pipeline: refine → owner approves → run. Pipeline nodes need `$result.` parameter passing.
   - Let refinement draft requirements for the owner to publish.
7. **A richer task prompt** (`common.md`, `flow._render_prompt`): the DoD command, the relevant requirement ids, the
   gating policies (from `gating_for_job`), and the previous report on a retry or revision.
8. **Questions from the agent.** Today the agent is told to "never block" and may only message a Remote Control
   session (`common.md:20`). Add an `ask_owner` route that creates an inbox item. The run either waits, capped, or
   continues with a stated default the owner can override.
9. **Test runners for every language.**
   - Pytest is the default today (`testrun.py:39`), and the release job passes no command.
   - Store the command per suite (`bazel test //...`, `npm test`, the Unity JUnit import).
   - Stop the UI app by PID, not with `pkill -f <command>`.
10. **Deploy.**
    - An opt-in trigger from `release_finished` to `deployment.deploy`, confirm-gated.
    - A health probe that starts the existing rollback, instead of waiting for the confirm timer.

### 5.4 Small fixes found on the way

- Retry rejects a `queued` result that create accepts (`dashboard/app.py:1831` vs `:1724`).
- Retry works on `failed` runs only, not on `stopped` or `partial` ones.
- Auto-approve ignores the result file's `status` (`flow.py:349-350`).
- In `new` mode, a stale branch with the same name is silently reused (`flow.py:110-112`). Append the run id to
  branch names.
- `_publish` swallows `GitHubError`, so a missing PR goes unnoticed (`flow.py:221`).
- `git add -A` in `_publish` commits stray logs and artifacts.
- Incidents default to `auto_approve`. Until the verify step exists, a crash can become a merged change with no tests
  run. Default them to `manual`.
- `partial` runs still "succeed" their Job (`agent/app.py:236-243`), so `depends_on` chains carry on after bad work.
- Dead code: `agent/poller.py` (the issue flow is unreachable, yet `issue-form-installed` still gates finishes), the
  screenshot step (no caller sets `_deployment_manifest`), and pipelines (none defined, static parameters).
- `policy.activate` failed 18 times with "'commit' is not a valid GatePoint": a stale validator.
- `agent.session_start` failed 5 times with "transient scope unit … already exists".

## 6. Clearer information on each project

### 6.1 What exists

| Surface | Shows |
|---|---|
| Dashboard `/` | Five tiles. A card per project with server up or down, deployed version and branch, Claude session, tasks awaiting finish or running, requirements done/total, and latest CI (`dashboard/app.py:1059-1135`) |
| `/status/<project>`, `/status.json` | Server, running jobs, tasks needing attention, jobs failed in the last 2 h, scheduled tasks due within 1 h, failing policies, latest test run |
| Claude Code status mod (`/ws`) | The same data, read every 30 s while a session is open (`src/usage/claude_mod/register.tsx`) |
| Session title | `<project> · <VERSION> · <branch>` |
| Tasks, Jobs, Incidents, Pillars, Statistics, Schedule, Backlog, Report, Claude usage | Each one area, on its own page |

### 6.2 Gaps

- **No milestone progress anywhere**, though milestones exist in the Requirements pillar.
- **No repository state per project:** `VERSION`, open branches, release candidates, last release. The card shows the
  *deployed* server's version only.
- **No backlog count** on the project card.
- **No timeline of what agents did.** A change report lives only on its own task page.
- **The status mod is not installed on this machine,** and `dashboard_url` defaults to `localhost`, so links from the
  phone are dead. The Windows app's WSL sessions load no plugins.

### 6.3 Proposals

1. **A project page with one plain-language headline**, for example *"2 branches open · 1 task awaiting your review ·
   M17 70% · last release 0.4.2, 6 days ago"*. Below it:
   - repository state from `workshop_version_status`;
   - milestone bars;
   - backlog count;
   - last agent activity;
   - a "since you last looked" counter.
2. **A timeline per project**, built from Orchestration's change feed: tasks with a one-line summary of their change
   report, finishes, releases, deploys, policy failures and incidents, in plain words ("Claude finished X on
   improvement/y; awaiting your review").
3. **A daily or weekly digest**: a catalogued `JobSpec` that builds one summary per project from data (done, blocked,
   needs you, usage). It follows the job-catalog and read-only-what-is-shown rules.
4. **Ecosystem overview:** every project on one screen, as rows of stage (idea / refining / coding / verifying /
   review / released) × project. This lets the owner see where the Claude-only pipeline is stuck.
5. **Tied test results:** with `commit_sha` on `TestResult`, the report can say "done at commit X on dev" instead of
   "latest run anywhere".

## 7. Faster and easier reaction from the owner

### 7.1 Today

- **There is no push.** The service worker has no `push` handler (`static/sw.js`). "Alert me on this device" calls
  `new Notification()` from an open page only (`base.html:154-156`).
- **Android probably never alerts.** Chrome on Android refuses that constructor, and the error is swallowed. Not yet
  tested on a device.
- **Remote Control is not a channel for notifications,** though OQ-9 chose it as one. Only a running task's own Claude
  can message the owner.
- **Deadlines expire unseen.**
  - A held action fails after **10 minutes** (`CONFIRM_TIMEOUT_SECONDS = 600`, `policy_gate.py:76`).
  - A job question fails after 12 hours (`common/job_steps.py:41`).
  - A failed task drops out of "needs attention" after 7 days.
- **The attention list covers 4 kinds only:** failing policies, held jobs, job questions and open incidents
  (`dashboard/app.py:504-546`).
- **The attention list misses:**
  - tasks awaiting a finish, and failed or stopped tasks;
  - failed jobs;
  - dead pillars;
  - policy drafts awaiting Gate 4;
  - missed scheduled runs;
  - jobs held by plan pressure;
  - the deployment confirm gate;
  - the generation queue's pause.
- **Bug: the question card carries the gate's buttons.** Every item with a `job_id` gets `job_gate_actions`
  (`settings.html:24`). A question card therefore shows "Confirm anyway / Cancel / Mark as failed", and **Cancel stops
  the running job**.
- **A held card has no reason and no direct link** (`detail: ""`, link to the list page).
- **A down pillar reads as "Nothing needs you right now"** (`_try(..., default=[])`).
- **`/status.json` and `/ws` omit held actions and questions.**
- **Notifications are deduped by title,** so a second held finish of the same type is not alerted.

| Situation | Taps on the phone today | Limit |
|---|---|---|
| Held finish | About 4, with the PWA already open | 10 minutes, and no reason shown |
| Job question | About 4, and the obvious buttons are wrong | 12 hours |
| Task awaiting review | About 5. Not in the banner | Never surfaced |
| From a Remote Control session | Claude has to stitch several tools together | No single "what needs me" call. Policy drafts can't be approved from MCP or CLI |

### 7.2 Proposals

1. **One "Needs you" inbox.** Extend `collect_attention` to every kind in §7.1. Give each item:
   - a reason (the failing policy and its fix, the question, the change-report summary);
   - a deadline ("expires in 8 min");
   - an inline action: Confirm, Answer, Finish, Request changes, Retry, Redeploy, Run the missed job.

   Show "Policy pillar unavailable" as an item of its own, not as an empty list.
2. **The same inbox for Claude and the CLI:** a `workshop_attention` MCP tool and `lws attention`, so a Remote Control
   session on the phone gets everything in one call and acts on it.
3. **Real push.**
   - Web Push from the service worker: a VAPID subscription, a `push` handler, and `showNotification` with action
     buttons handled in `notificationclick`.
   - The Dashboard's live watcher sends one notification per new inbox item.
   - The result is one tap from the lock screen.
   - A fallback (ntfy or e-mail) for when the PWA is not installed.
4. **Deadlines that fit a person.**
   - The confirm timeout becomes configurable per gate point, measured in hours by default.
   - A reminder before a question expires.
   - On expiry: an inbox item and an incident, never a silent fail.
5. **New events for triggers:** `job.pending_confirmation` and `job.step_attention`, besides `job.settled`, so a
   notifier or a Remote Control session can be started from them.
6. **Quick fixes:**
   - answer buttons on question cards;
   - the reason and a link to `/jobs/{id}` on held cards;
   - dedupe by item id;
   - keep open incidents in `/status` past 2 hours;
   - install the status mod with `usage.configure_claude apply`, and set `dashboard_url` to the tailnet address.

## 8. Doc and code drift

| Doc says | Code does |
|---|---|
| Policy gating is fail-closed (`policy_compliance.md:61`) | Dispatches ungated when the pillar is unreachable |
| AGENTS.md: "policies gate branch finishes" | Agent finishes skip the gate |
| Auto-approve needs a passing recorded test run (`agentic_development.md`; the UI: "tests pass") | Any non-empty self-reported text passes |
| Different projects run fully in parallel | One global lock |
| `ci_check` and `run_task` share a worktree path | `run_task` uses no worktree |
| The change report has a Screenshot section | It never runs |
| `commit-cites-requirement` gates `commit` (`policy_compliance.md:973`) | It gates the finish only since REQ-000000451 |
| `version_policy.md`: rule 1 "applies to every branch", and the policies are scoped to little-workshop | Hotfix and release starts bump nothing, and `policies.json` scopes the policies to all projects |
| `workshop_agent_finish_task` "publishes its branch/PR" | It merges through `version.*_finish` |
| `agent_task_standard.md`: `update_docs.md` exists only on `feature/doc-review` | It is in this checkout |
| `commit-bumps-version`'s fix says "raise VERSION yourself" | The guard refuses that edit |

## 9. Roadmap

Each phase can be handed to Claude as its own improvement or feature branch.

**Phase 0: stop losing decisions (bugs, about a day)**
- Finish and deploy `improvement/newest-request-wins` (the EARS warning, form checks as warnings).
- Commit the generated Claude files left uncommitted in five projects.
- Fix the question card, add the reason to held cards, make the confirm timeout configurable, and expire stale holds
  at startup.
- Fix the hotfix trap and the amend refusal.
- Fix the scope guard's false positives.
- Fix `test_output_filter`'s allow.
- Install the status mod and set `dashboard_url`.

**Phase 1: one real gate (a feature)**
- Agent finishes through gated Orchestration Jobs.
- The verify step: DoD suite and finish policies, by machine.
- Auto-approve only on a green verdict. Incidents default to manual until then.
- Move the history-judging policies to after release; coverage reuses the DoD run.
- The gate fails visibly, not silently.

**Phase 2: the Claude-only loop (a feature)**
- A worktree per run, systemd units for runs, a lock per project.
- The fix-up loop and Request changes, with budgets and caps.
- The review agent.
- A richer prompt, and test commands for every language.

**Phase 3: the owner's inbox (a feature)**
- The inbox for every kind of item, with deadlines and inline actions.
- `workshop_attention` and `lws attention`.
- Web Push with action buttons, and the new trigger events.

**Phase 4: clear information (a feature)**
- The project page with a headline, repository state, milestones and backlog.
- The timeline and the ecosystem overview.
- The digest job.
- Test results tied to a commit.

**Phase 5: planning (a feature)**
- Backlog-to-task chaining, with refinement questions through the inbox.
- Requirements drafted from the refined entry.
- An opt-in deploy after release, with rollback on a failed health check.

## 10. Decisions for the owner

1. **Auto-finish.** Once verify and review are green, should a project's agent work finish without you (per project,
   off by default), or always wait for your tap?
2. **The confirm deadline.** Hours, a whole day, or no expiry, with reminders instead?
3. **Push channel.** Web Push from the Dashboard PWA only, or also a fallback such as ntfy or e-mail?
4. **The history policies.** Should `merge-commit-message-format`, `back-merge-no-ff` and
   `hotfix-propagates-by-merge` move to after a release, become warnings, or be dropped?
5. **`commit-cites-requirement`.** A warning over the whole branch, or retired?
6. **Agent budgets.** The cost cap per run and the number of fix-up attempts.
7. **Parallel work.** How many agent runs may run at once on one project, now that worktrees allow it?
