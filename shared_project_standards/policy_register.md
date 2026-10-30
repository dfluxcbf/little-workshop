# Policy register

Source: the Policy & Compliance pillar (`lws policy list`, `lws policy projects list`,
`docs/pillars/policy_compliance.md`) and the policies' code in little-workshop's
`tools/policy_scripts/` (one script per policy plus `policies.json`: rule, scope, gate points).
Snapshot of 2026-10-02: 26 policies in `policies.json`. Per-project application is in each project's
folder.

A policy is an EARS statement with a test script, a `target_pillar`, a scope level and **gate
points**. A gate point is where a failure blocks: `commit` (the project's `.githooks/commit-msg`
hook refuses the commit), and the Job categories `build_install`, `server_launch`,
`deploy_redeploy`, `self_redeploy`, `server_stop`, `release`, `package_publish`, `branch_finish`,
`project_removal` and `project_registration` (the last only on a framework-bound policy, verified
after the registration rather than gating it). A failing check holds a gated Job in
`pending_confirmation`. History-based checks judge only the newest commit or tag. Before a gated
action, a project's `policy-preflight` skill (`lws policy evaluate`) runs the same policies without
recording or holding anything.

Policies are the hard layer of a project's Claude Code instructions: a rule that must hold every time
is a policy (or a hook in the project: the generated guard refuses raw lifecycle commands and edits of
generated files, naming the route to take), and the project's `AGENTS.md` and `.claude/rules/` say what
enforces it instead of restating it. Every failure message says what to change, for a person or for
Claude.

**Which projects a policy applies to is the selection in `policy.set_projects`, not the scope
level.** Per selected project there is a gating switch: with gating off the check still runs and is
recorded, but its verdict never holds an action. Scope level (`project`, `project_set`,
`all_projects`) survives only as authoring intent: it seeds the initial selection once and orders
precedence on a topic (framework-created policies first, then project, project set, all projects). A
project registered later is not selected automatically.

**Changing a policy:** edit its script and its entry in `tools/policy_scripts/policies.json`, then
`python3 tools/sync_policies.py --apply` in little-workshop replaces it in the pillar (keeping its
project selection), and `workshop_policy_check_all` re-checks it everywhere it applies.

## Policies on every project they are attached to

| Topic | Pillar | Gates | Rule |
|---|---|---|---|
| `version-format` | version | commit, branch_finish | `VERSION` is `M.m.p`, optionally `-dev.N` or `-rc.N` |
| `commit-bumps-version` | version | commit, branch_finish | Each commit's `VERSION` has higher precedence than its parent's |
| `branch-commit-dev-suffix` | version | commit, branch_finish | On a working branch, `VERSION` is next patch of the branch point plus `-dev.N` (`N`: 1, then +1) |
| `commit-cites-requirement` | version | commit, branch_finish | A commit changing source (`src/`; in a Unity project also `Assets/`, `Packages/`, `ProjectSettings/`, `tools/`) cites `REQ-nnnnnnnnn` or `REQ-NONE`, never both |
| `unity-meta-files-paired` | version | commit, branch_finish | In a Unity project, a file or folder under `Assets/` is added or deleted with its `.meta`, and no `.meta` apart from its asset |
| `lfs-tracked-files-are-pointers` | version | commit, branch_finish | A file `.gitattributes` routes to Git LFS is committed as an LFS pointer |
| `no-committed-secrets` | version | commit, branch_finish | A commit introduces no private key, no provider token or API key in a recognisable format and no `.env` file (a line marked `pragma: allowlist secret` is skipped) |
| `merge-commit-message-format` | version | branch_finish | A newest merge commit on the integration or production branch reads `feat\|impr\|fix (<issue or -1>): <title>` |
| `version-hook-installed` | version | branch_finish | `.githooks/pre-commit` runs the VERSION sync, and a propagation script is registered where another of the project's files carries the version |
| `commit-policy-hook-installed` | version | branch_finish | `.githooks/commit-msg` runs the policies gating `commit`, and `core.hooksPath` is `.githooks` |
| `production-version-stable` | version | release | On the production branch, `VERSION` has no pre-release suffix |
| `production-tag-format` | version | release | The newest production tag is annotated `v<M>.<m>.<p>` |
| `production-tags-on-production-history` | version | release | The newest production tag is reachable from the production branch |
| `rc-suffix-only-on-release-branches` | version | release | An `-rc.N` `VERSION` implies a `release/*` branch |
| `release-candidate-tag-format` | version | release | The newest RC tag is annotated `v<M>.<m>.<p>-rc.N`, `N` starting at 1 and increasing by one |
| `requirement-marker-registered` | requirements | branch_finish | Where tests use requirement markers, pytest configuration registers one |
| `requirement-marker-id-format` | requirements | branch_finish | Markers reference `REQ-` plus nine digits |
| `python-test-coverage` | requirements | branch_finish | A Python project's suite covers at least 90 percent of statements |
| `issue-form-installed` | agent | branch_finish | Where issue-driven automation is used, `.github/ISSUE_TEMPLATE/claude-task.yml` exists |
| `ssh-remotes-no-embedded-credentials` | registry | branch_finish | Git remotes are SSH URLs without embedded credentials |
| `claude-config-present` | registry | branch_finish | An `AGENTS.md` briefing (root or `.claude/`) importing the shared `.claude/little-workshop.md`; the generated `claude-instructions` rule, `workshop-lifecycle` and `policy-preflight` skills and guard, registered for Bash, the file-edit tools and the MCP tools; no `CLAUDE.md` or `CLAUDE.local.md` anywhere |
| `claude-instructions-valid` | registry | branch_finish | The Claude instructions load as written: `@` imports resolve inside the project, rules parse with only `paths` and match files, skills and subagents have descriptions, skill and hook scripts exist, no instruction file loads more than 200 lines and a session starts with at most 250 |
| `wheel-filename-has-version` | build | package_publish | A built wheel's name carries the project version |

The policies that read the project's files list them through `git ls-files` (tracked, or untracked
and not ignored), so ignored build output, caches and vendored packages are never judged.

## Policies scoped to one project

| Topic | Project | Gates | Rule |
|---|---|---|---|
| `back-merge-no-ff` | little-workshop | branch_finish | The newest finished release or hotfix reached the integration branch by a non-fast-forward merge (judged on history) |
| `hotfix-propagates-by-merge` | little-workshop | branch_finish | The newest production tag is in the integration branch's history, and nothing since was cherry-picked |
| `design-index-and-schema-current` | ethereal-lands | branch_finish | The design index and the ELM JSON schema match what their generators write (`--check`) |

Policies created by the user rather than the framework: `commit-bumps-version`,
`branch-commit-dev-suffix`, `back-merge-no-ff`, `hotfix-propagates-by-merge`,
`commit-cites-requirement`, `python-test-coverage`, `wheel-filename-has-version`,
`design-index-and-schema-current` and `no-committed-secrets`; the other seventeen are `framework`-created.

## Attachment matrix

`G` gating on, `-` attached with gating off, blank not attached. ethereal-lands' column and the
four new all-projects policies take effect when the 2026-10-02 sync and attachment are applied.

| Policy | workshop | home | meals | mindcheck | spectrum | ethereal-lands |
|---|---|---|---|---|---|---|
| `commit-bumps-version`, `branch-commit-dev-suffix`, `version-hook-installed`, `commit-policy-hook-installed` | G | G | G | G | G | G |
| `back-merge-no-ff`, `hotfix-propagates-by-merge` | G | | | | | |
| `design-index-and-schema-current` | | | | | | G |
| `claude-instructions-valid`, `unity-meta-files-paired`, `lfs-tracked-files-are-pointers`, `no-committed-secrets` | G | G | G | G | - | G |
| the other 15 | G | G | G | G | - | G |

## Results of 2026-10-02 (every policy against every project, run from the policy scripts)

Second run, after the briefings, rules, skills and guard were regenerated in every project (26 policies,
`python-test-coverage` left to the test run): everything passes or is not applicable, except
`commit-bumps-version` on little-home, little-meals, little-mindcheck and little-spectrum, as below. The
commit-level policies, run on each repository's pending changes as the commit-msg hook will, pass.

First run:

- `commit-bumps-version` fails on little-home, little-meals, little-mindcheck and little-spectrum:
  each one's newest commit on `dev` ("chore(version): pre-commit version sync only on working
  branches") does not raise `VERSION`. It clears with the next commit there that does.
- ethereal-lands, on `improvement/agents-md-instructions`: `commit-bumps-version`,
  `branch-commit-dev-suffix` and `commit-cites-requirement` fail on its newest commit (`b300534`,
  committed before the hooks were installed: no `VERSION` bump, no `REQ-NONE`). The next commit, made
  through the hooks now installed there, clears them. Everything else passes or is not applicable.
- Everything else passes or is not applicable everywhere.

## Discrepancies to resolve

- little-spectrum's newest release `v0.2.2` was never merged back into `dev`; `back-merge-no-ff` and
  `hotfix-propagates-by-merge` would fail there, but they are attached to little-workshop only.
- `docs/requirements_policy.md` says `commit-cites-requirement` is "filed as a draft for approval";
  it is an approved, enabled policy.
- The requirements behind the 2026-10-02 policies are proposed in little-workshop's
  `docs/requirements.md` with ids pending registration in the Requirements pillar.

Last reviewed: 2026-10-02 (four new policies, three policies fixed to read only the project's files,
claude-config-present and commit-cites-requirement widened, ethereal-lands attached; hand edit, same day:
no-committed-secrets added, claude-config-present and claude-instructions-valid widened, the guard refuses
raw lifecycle commands and generated-file edits).
