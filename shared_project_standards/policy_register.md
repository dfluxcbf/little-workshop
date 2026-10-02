# Policy register

Source: the Policy & Compliance pillar (`lws policy list`, `lws policy projects list`,
`docs/pillars/policy_compliance.md`) and the policies' code in little-workshop's
`tools/policy_scripts/` (one script per policy plus `policies.json`: rule, scope, gate points).
Snapshot of 2026-10-02: 21 enabled policies, none disabled. Per-project application is in each
project's folder.

A policy is an EARS statement with a test script, a `target_pillar`, a scope level and **gate
points**. A gate point is where a failure blocks: `commit` (the project's `.githooks/commit-msg`
hook refuses the commit), and the Job categories `build_install`, `server_launch`,
`deploy_redeploy`, `self_redeploy`, `server_stop`, `release`, `package_publish`, `branch_finish`,
`project_removal` and `project_registration` (the last only on a framework-bound policy, verified
after the registration rather than gating it). A failing check holds a gated Job in
`pending_confirmation`. History-based checks judge only the newest commit or tag. Before a gated
action, a project's `policy-preflight` skill (`lws policy evaluate`) runs the same policies without
recording or holding anything.

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
| `version-format` | version | commit*, branch_finish | `VERSION` is `M.m.p`, optionally `-dev.N` or `-rc.N` |
| `commit-cites-requirement` | version | commit*, branch_finish | A commit changing a file under `src/` cites `REQ-nnnnnnnnn` or `REQ-NONE`, never both |
| `merge-commit-message-format` | version | branch_finish | A newest merge commit on the integration or production branch reads `feat\|impr\|fix (<issue or -1>): <title>` |
| `version-hook-installed` | version | branch_finish | `.githooks/pre-commit` runs the VERSION sync, and a propagation script is registered where another file carries the version |
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
| `claude-config-present` | registry | branch_finish | `.claude/AGENTS.md` and a `.claude/settings.json` registering the scope guard exist, and no `CLAUDE.md` |
| `wheel-filename-has-version` | build | package_publish | A built wheel's name carries the project version |

\* `commit` is in each policy's code (`policies.json`); it is applied to the live pillar once the
pillar carrying the `commit` gate point is deployed (until then the sync runs with
`--without-gate-point commit`, and the commit-msg hook warns that it could not check).

## Policies scoped to one project

Scope level `project`, target `little-workshop`, created by the user. See
[little_workshop_standards/](little_workshop_standards/).

| Topic | Gates | Rule |
|---|---|---|
| `commit-bumps-version` | commit*, branch_finish | Each commit's `VERSION` has higher precedence than its parent's |
| `branch-commit-dev-suffix` | commit*, branch_finish | On a working branch, `VERSION` is next patch of the branch point plus `-dev.N` (`N`: 1, then +1) |
| `back-merge-no-ff` | branch_finish | The newest finished release or hotfix reached the integration branch by a non-fast-forward merge (judged on history) |
| `hotfix-propagates-by-merge` | branch_finish | The newest production tag is in the integration branch's history, and nothing since was cherry-picked |

Policies created by the user rather than the framework: these four plus `commit-cites-requirement`,
`python-test-coverage` and `wheel-filename-has-version`; the other fourteen are `framework`-created.

## Attachment matrix

`G` gating on, `-` attached with gating off, blank not attached.

| Policy | workshop | home | meals | mindcheck | spectrum | ethereal-lands |
|---|---|---|---|---|---|---|
| `commit-bumps-version`, `branch-commit-dev-suffix`, `version-hook-installed`, `commit-policy-hook-installed` | G | G | G | G | G | |
| `back-merge-no-ff`, `hotfix-propagates-by-merge` | G | | | | | |
| the other 15 | G | G | G | G | - | |

## Results of 2026-10-02 (all 21 policies, every attached project)

- `commit-bumps-version` fails on little-home, little-meals, little-mindcheck and little-spectrum:
  each one's newest commit on `dev` ("chore(version): pre-commit version sync only on working
  branches") does not raise `VERSION`. It clears with the next commit there that does.
- `commit-policy-hook-installed` fails everywhere until each project commits the
  `.githooks/commit-msg` installed that day.
- Everything else passes or is not applicable. `back-merge-no-ff` and `hotfix-propagates-by-merge`,
  which failed on little-workshop as text scans, pass as history checks.

## Discrepancies to resolve

- `commit-bumps-version` and `branch-commit-dev-suffix` have scope `project` / `little-workshop`
  and `docs/version_policy.md` documents them as scoped to little-workshop, but they are attached
  with gating on to little-home, little-meals, little-mindcheck and little-spectrum too. By the
  pillar's rule the attachment, not the scope level, decides.
- ethereal-lands has no policy attachment, so none of the policies is checked on it, although it has
  a `VERSION`, a `.lvx/config.json` and merge commits in the `feat (-1): ...` form.
- little-spectrum's newest release `v0.2.2` was never merged back into `dev`; `back-merge-no-ff` and
  `hotfix-propagates-by-merge` would fail there, but they are attached to little-workshop only.
- `docs/requirements_policy.md` says `commit-cites-requirement` is "filed as a draft for approval";
  it is an approved, enabled policy.

Last reviewed: 2026-10-02 (hand edit: policies as code, the `commit` gate point, two new
policies, a full re-check).
