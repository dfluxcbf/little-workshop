# Requirements: Version Control & Release

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M3 — Version Control & Release

| ID | Requirement | Status |
|---|---|---|
| REQ-000000032 | `version.init` detects or validates production/integration branches and writes `.lvx/config.json`, kept byte-compatible with `little-versions`' existing schema so every already-`lvx`-managed project in this ecosystem needs no migration. | done |
| REQ-000000033 | `feature`/`improvement` start creates `<prefix>/<name>` from the integration branch, refusing on a dirty tree unless forced. | done |
| REQ-000000034 | `feature`/`improvement` finish rebases onto the integration branch, squashes to one commit, merges with `--no-ff` (a squash merge since 2026-10-07, REQ-000000420), bumps the patch version, runs version hooks, and deletes the branch — refusing, never auto-resolving, on a real rebase conflict. | done |
| REQ-000000035 | A `feature`/`improvement` finish conflict is resumable: `--continue` (after resolving and staging) completes the merge; `--abort` restores the pre-finish state. Fixed a latent ordering bug found while porting: the saved finish-state file is the newest commit on the branch, so a conflict on any earlier commit means `git rebase --continue` must run *before* the state file is read back, not after. | done |
| REQ-000000036 | `hotfix` start creates `hotfix/<name>` from the production branch; finish merges into production, bumps the patch version, tags it, and back-merges into the integration branch — resumable the same way, via `git merge` rather than rebase (no state-file-ordering issue there). | done |
| REQ-000000037 | `release` start derives the stable version from `VERSION`, creates `release/<version>` from the integration branch. Release-candidate cut/list/promote: cutting tags `v<version>-rc.<n>` (auto-numbered), listing returns them in order, promoting records which RC is the release target. | done |
| REQ-000000038 | **Release finish bumps the minor version on production.** Superseded: production keeps the released version; only the integration branch moves to the next patch (REQ-000000489). | superseded by REQ-000000489 |
| REQ-000000039 | `support` start creates `support/<version>` from the matching production tag; finish always refuses — support history must never silently diverge from the primary lines, by policy, not by omission. | done |
| REQ-000000040 | `version.config_add_hook`/`remove_hook` manage the registered version-hook scripts (`.sh`/`.py` only); hooks run with `LVX_NEW_VERSION` set on every version bump. | done |
| REQ-000000042 | **No database; config and state in the repository.** Superseded: the version state moved to the state directory, so recording it never commits (REQ-000000488). | superseded by REQ-000000488 |

## M6 — Agentic Development

| ID | Requirement | Status |
|---|---|---|
| REQ-000000061 | `agent.run_task` resolves a `BranchingSpec` (`new`/`existing`/`auto`) against Version Control & Release's own branch-type policy (`GET /v1/config`, never a direct `.lvx/config.json` read) and rejects a `new`-mode request whose base doesn't match that type's required base, before anything is touched. | done |
| REQ-000000062 | `new`-mode branch creation calls Version Control & Release's own `version.<type>_start` Job directly over its Pillar Interface (a synchronous subroutine call, not routed through Orchestration a second time); `existing`-mode checks out a named branch (local or remote-tracking); `auto`-mode leaves the checkout on the integration branch for the agent to branch itself. | done |

## M14 — Dashboard

| ID | Requirement | Status |
|---|---|---|
| REQ-000000146 | **Documented, deliberate scope reduction**: Overview/Projects/Jobs/Policy are the only panels this pass builds — Version Control & Release, Build & Packages, Requirements & Quality, Deployment & Servers, and Agentic Development don't have dedicated panels yet. `dashboard/client.py`'s established glue pattern makes adding one for any of them a small, additive change, not a redesign, whenever that pillar's own dashboard need is concrete enough to justify one — see `docs/pillars/dashboard.md`'s "Left open." Likewise, browser-facing exposure (which reverse proxy fronts the Unix socket, Tailscale ACL specifics) is real future work for whoever deploys this pillar, matching `little-runner`'s own existing `tailscale serve` reliance, external to its own app — this pass proves the mechanism over a real UDS, not the production exposure story. | done |

## M16 follow-up — parity survey and rejecting compatibility shims (2026-09-17)

| ID | Requirement | Status |
|---|---|---|
| REQ-000000168 | **`bootstrap-little-project`'s two real parity gaps, closed** (its third finding, `.lrun/config.toml`, deliberately deferred — see REQ-000000169). (1) GitHub label creation: `registry/github.py` gains `BOOTSTRAP_LABELS` (the real 11 labels the old tool created — 7 trigger/outcome `claude*` labels plus `docs`/`milestone`/`bug`/`task` intent labels, copied as literal data, never imported from `little-runner`'s own code) and `ensure_label`/`ensure_label_with_gh` (an idempotent POST-then-PATCH-on-422 upsert); `bootstrap_project` creates them right after the GitHub repo push, guarded by a new `BootstrapParams.ensure_labels` flag (default `true`), and reports what it created via `BootstrapResult.labels_created`. (2) Version-tracking init: a new `registry/version_client.py` resolves Version Control & Release's own socket through Core and posts a real `version.init` Job — a genuine cross-pillar Pillar Interface call, never a direct import — then runs the same git add/commit/checkout-dev/merge-ff-only-master sequence a caller doing this by hand already had to run, leaving a freshly bootstrapped project checked out on `dev` (the ready-to-work gitflow state), exactly matching what the old tool's own `create` always did via `lvx init`. `BootstrapResult.version_initialized` reports whether it happened (`false`, never raised, if Version Control & Release is unreachable). The cross-pillar call is made *after* `job_bootstrap`'s own `threading.Lock()` is released, not inside it — `version.init` calls back into Registry's own `GET /v1/projects/{name}` via `common/registry_client.py`'s `resolve_project_path`, which needs that same lock, so calling it from inside would deadlock. | done |

## M16 follow-up — `little-runner`'s branch audit (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000178 | **`lrun branch-audit run`, closed, as `version.branch_audit`** — a new, read-only Job on Version Control & Release, ported from the ecosystem's own root `branch_audit.py`/`stabilize_branches.py` classifier but scoped to one project's own real checkout rather than walking every submodule directly (`lws version branch-audit --all` loops the Job over Registry & Portfolio's own project list client-side — this daemon's actual authority for "every project"). Classifies every non-integration/production branch against the same five violation kinds the original tool did — `naming` (doesn't match a recognized branch-type prefix), `stale_merged` (a fully-merged branch that still exists), `abandoned` (unmerged, no commits past `stale_branch_days`, already a per-project config field this pillar's own schema carried), `detached_head`, `stray_worktree` — reusing `version/branches.py`'s own `Branch.parse` as the single source of truth for branch-type classification (the same principle REQ-000000163's issue-linking gap closure already established: reuse this pillar's own taxonomy, never a second copy of it) and `version/gitops.py`'s existing `is_ancestor`/`is_merge_in_progress`/`is_rebase_in_progress` primitives rather than reimplementing git plumbing. Never merges, deletes, commits, or pushes anything, per `docs/policies/branching_policy.md`'s own rule, restated directly in the new module's docstring. Exposed as `lws version branch-audit --project-id/--all` and a `workshop_version_branch_audit` MCP tool. | done |

## M16 follow-up — `little-runner`'s `stabilize run`, the last real gap (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000182 | **`lrun stabilize run`, closed, as `version.stabilize`** — a new, genuinely mutating Job on Version Control & Release (`version/stabilize.py`), ported from the ecosystem's own root `stabilize_branches.py`. Given a project already checked out on some branch: fetches `origin`; brings the integration branch up to date via a real fast-forward-only merge (a genuine divergence is reported as `needs_manual_resolution`, never force-pushed or force-merged, per `docs/policies/branching_policy.md`'s own rule); commits any pending changes; classifies the branch that was checked out (reusing `version/branches.py`'s `Branch.parse`, the same taxonomy `version.branch_audit` already reuses); finishes a feature/improvement/hotfix branch by calling this pillar's own already-existing `cmd_topic_finish`/`cmd_hotfix_finish` functions directly (never a second implementation of that squash-merge-and-bump logic); pushes the integration branch (and production, for a hotfix); and deletes any other already-merged remote/local branches found along the way, skipping protected names/prefixes (`dev`/`develop`/`master`/`main`, `release/*`, `support/*`) exactly like the original. `execute=false` is the default (matching the original tool's own dry-run-by-default design) — every step is planned and reported in the result, nothing on disk or `origin` actually changes until `execute=true` is passed explicitly. Exposed as `lws version stabilize --project-id/--all [--execute]` (looping over Registry & Portfolio's own project list for `--all`, the same pattern `version.branch_audit` already established) and a `workshop_version_stabilize` MCP tool. | done |

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000191 | **`little-mindcheck`'s real deploy/stop/status verified end to end against the actual running app** — `deployment.deploy --project-id little-mindcheck --branch dev` really built, installed, and launched the real server (confirmed listening on its real port, real Tailscale mapping active, real version/commit reported), and `deployment.stop` really tore it back down (unit stopped, Tailscale mapping removed, port confirmed free) — leaving the project in the same not-currently-running state it was in before the pilot began, as instructed. | done |

## Release job (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000260 | **Release job: conditions, then release start, changelog, release finish, relaunch.** When the user starts a Release job (New job or Schedule job), Version Control & Release shall verify the chosen conditions before starting a release branch — the release merges into production without conflicts; minimum delta (lines changed since the last release) and minimum patch bumps, combined with the chosen and/or; and, if checked, all tests pass, all published requirements pass (drafts ignored) and all policies pass, running the tests once for both test conditions — and, only if they all hold, shall start the release branch, write a CHANGELOG.md section, finish the release and then relaunch the running app on its own branch (for little-workshop itself, redeploy every enabled pillar). | done |
| REQ-000000261 | **Release finish back-merges production.** When a release finishes, Version Control & Release shall back-merge the production branch into the integration branch, so the next release merges into production without conflicts on VERSION, `.lvx/state.json` or version-hook-written files. | done |
| REQ-000000262 | **Pre-commit version sync only on working branches.** Superseded: no branch's commits write VERSION any more; the finish writes it (REQ-000000485). | superseded by REQ-000000485 |

## Finish resolves version-only rebase conflicts (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000265 | **Finish resolves version-only rebase conflicts.** Superseded: conflicts in generated files are settled too, by regenerating them (REQ-000000487). | superseded by REQ-000000487 |

## REQ-NONE for commits that affect no requirement (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000266 | **Bookkeeping commits cite REQ-NONE.** When the Version Control & Release pillar makes one of its own bookkeeping commits (a version bump or finish state or release candidate or changelog or issue link), the pillar shall cite REQ-NONE in that commit's message and shall never cite REQ-NONE in a merge commit that carries a branch's changes. | done |

## A finish lands on the branch's own patch (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000269 | **A finish lands on the branch's own patch.** When a feature or improvement branch finishes, the Version Control & Release pillar shall set the integration branch's VERSION to the stable form of the branch's -dev version, or to the next patch when the branch's VERSION carries no -dev suffix, and always to a version above the integration branch's version before the merge. | done |

## The version pillar's own commits skip the repository's hooks (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000270 | **The version pillar's own commits skip the repository's hooks.** When the Version Control & Release pillar runs git in a project's repository, the pillar shall run it with the repository's hooks disabled, so the installed pre-commit version sync never re-bumps a VERSION the pillar has already written. | done |

## Claude-native policy enforcement (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000306 | **Git hooks are installed without rewriting the version configuration.** Superseded: install-hooks writes only commit-msg and removes the retired pre-commit (REQ-000000491). | superseded by REQ-000000491 |
| REQ-000000308 | **Release back-merges are judged on history.** When back-merge-no-ff or hotfix-propagates-by-merge checks a project, the script shall judge the newest production tag: its commit is in the integration branch's history and not on its first-parent line, and no integration commit since it was cherry-picked; the scripts shall not judge the project's source text. | draft |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |

## Pillar crashes and the work they cut (2026-10-05)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000410 | **A Version Job cut by a restart is failed at once.** When the Version Control & Release pillar starts, the pillar shall fail each Job it had answered accepted that its previous process was still running with a reason saying that the repository may be part-way through it, retrying until Orchestration takes the answer, and record an incident of kind "unexpected behaviour" naming the Job. | draft |

## Claude Code status: the phone and the Windows app (2026-10-06)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000414 | **/ws answers with a short status report of the project and the workshop.** When the user runs /ws in a session that loads the little-workshop status mod, the mod shall answer on every surface with a short status report of the checkout's project, version and branch, the server's state, the latest test run with how many passed or failed and when, the running Jobs or that nothing runs, the tasks that need attention and the Jobs failed in the last two hours and the scheduled tasks due within the next hour when there are any, whether the policies pass, the workshop's pillars up and its incidents of the last two hours, say unknown for a topic whose pillar does not answer, and in the terminal also open a pane with every topic, zeros included. | draft |

## Version Control & Release: features and improvements are squash-merged (2026-10-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000420 | **A feature or improvement finish lands on dev as one squash commit; a hotfix merges with --no-ff.** When a feature or improvement branch is finished, the Version Control & Release pillar shall land it on the integration branch as one squash commit carrying the finish message, with no merge commit, followed by the version bump, while a hotfix or a release finish shall keep merging with --no-ff. | draft |
| REQ-000000421 | **A branch squash-merged into its target counts as merged.** The Version Control & Release pillar's branch audit and stabilize shall treat a branch as merged into a target when the target's history holds it or merging it into the target would change nothing, so that a squash-merged branch is reported stale-merged and its remote copy is deleted by stabilize. | draft |
| REQ-000000422 | **`finish-branch` finishes a project's topic branch through the little-workshop routes only.** When the user submits `/finish-branch [project|--all] [--push]`, the `finish-branch` Claude Code plugin shall, without calling the model, commit the pending changes with the subject the commit policies want and the `REQ-` ids of the changed tests' markers, register the markers, run the project's Definition-of-Done tests when it configures a test command, run the policy preflight and finish the checked-out `feature/`, `improvement/` or `hotfix/` branch with `lws version <type>-finish`, never with raw git, reporting a finish held for confirmation without confirming it and stopping where the daemon is down. | draft |
| REQ-000000423 | **`finish-branch` hands over to Claude only where judgment is needed.** If a commit has source changes and no `REQ-` id to cite, the Definition-of-Done tests fail, the policy preflight fails, a rebase conflict is left for the user, or the changes touch files little-workshop generates, then the `finish-branch` plugin shall stop that project and give Claude the facts needed (the diff stat, the failing output, the failing policies, the conflicted files) instead of guessing. | draft |
| REQ-000000424 | **`finish-branch --push` pushes only with the no-reply e-mail.** Where `--push` is given and the finish succeeded, the `finish-branch` plugin shall push the integration branch, and the production branch for a hotfix, only after the repository's `user.email` is the GitHub no-reply address, and shall otherwise report the push as refused. | draft |
| REQ-000000425 | **`finish-branch` is distributed to every project from little-workshop's local plugin marketplace.** The little-workshop repository shall hold a local Claude Code plugin marketplace named `little-workshop` containing the `finish-branch` plugin, and `tools/install_plugins.py` shall add the marketplace and install each plugin at user scope with Claude Code's own `claude plugin` commands, so that every project's sessions have `/finish-branch`. | draft |

## Versioning without churn (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000485 | **Working branches leave VERSION alone.** While a branch is a feature or improvement branch, the system shall not write VERSION, or any file version_hooks writes, on a commit to it; the branch's finish shall write the version once. | draft |
| REQ-000000487 | **Finish settles version-only and generated-file rebase conflicts.** When a feature or improvement finish hits a rebase conflict that differs only by the two commits' own VERSION strings (in VERSION or a version_hooks-written file), is in `.lvx/state.json`, or is in a file little-workshop generates (its generated .claude/ files, .githooks/commit-msg, the retired .githooks/pre-commit), the Version Control & Release pillar shall resolve it, a generated file by writing it again from the generator with .claude/settings.json keeping the project's own entries of both sides, and continue the finish, and shall still stop the finish on any other conflict. | draft |
| REQ-000000488 | **Version state lives in the state directory, never in a commit.** The Version Control & Release pillar shall keep no database of its own: a project's configuration shall stay in its .lvx/config.json, and its version state (finishes in progress, issue links, promoted release candidates) shall be one file per project under the little-workshop state directory, never committed; where a project has no state file yet and its checkout carries a .lvx/state.json, the pillar shall read that file once as its initial state. | draft |
| REQ-000000489 | **Release finish leaves production at the released version.** When a release finishes, the Version Control & Release pillar shall merge it into production (refusing, cleanly, if a promoted RC is not reachable from HEAD or was never cut), tag the stable version, leave production's VERSION and version_hooks files at the released version, back-merge production into the integration branch, and set the integration branch to the next patch. | draft |
| REQ-000000491 | **Git hooks are installed without rewriting the version configuration.** When lws version install-hooks runs in a checkout or version.init runs with a project id, the Version pillar shall write .githooks/commit-msg, remove a .githooks/pre-commit that little-workshop installed to sync VERSION, and set core.hooksPath to .githooks; install-hooks shall change nothing else. | draft |

## Requirements and policies only when necessary (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000499 | **The squash commit records the branch's requirements.** When a branch is squashed into one commit at its finish, the Version Control & Release pillar shall add to the commit's body a line `Requirements: REQ-…, REQ-…` with every requirement id the branch's commit messages cite or the lines its diff adds name, in order of first mention, leaving out those the message already names. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000517 | **On Windows a commit without the lws shim is refused.** When the generated commit-msg hook runs under Git for Windows and finds no `lws` command, the hook shall refuse the commit (exit 1), saying to run `lws windows install-shim`, and when it runs in WSL or Linux without `lws`, it shall warn and let the commit through. | draft |
