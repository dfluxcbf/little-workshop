# Policy register

Source: the Policy & Compliance pillar (`lws policy list`, `lws policy projects list`,
`lws policy draft list`, `docs/pillars/policy_compliance.md`), snapshot of 2026-10-01: 19 enabled
policies, none disabled. Per-project application is in each project's folder.

A policy is an EARS statement with a test script, a `target_pillar`, a scope level and **gate
points**. The gate-point categories the pillar defines are `build_install`, `server_launch`,
`deploy_redeploy`, `self_redeploy`, `server_stop`, `release`, `package_publish`, `branch_finish`,
`project_removal` and `project_registration` (the last only on a framework-bound policy, verified
after the registration rather than gating it). Of these, the 19 policies use only `branch_finish`
(13), `release` (5) and `package_publish` (1); the others are not in use. A failing check holds a
gated action in `pending_confirmation`. History-based checks judge only the newest commit or tag.

**Which projects a policy applies to is the selection in `policy.set_projects`, not the scope
level.** Per selected project there is a gating switch: with gating off the check still runs and is
recorded, but its verdict never holds an action. Scope level (`project`, `project_set`,
`all_projects`) survives only as authoring intent: it seeds the initial selection once and orders
precedence on a topic (framework-created policies first, then project, project set, all projects). A
project registered later is not selected automatically.

**Pending drafts:** `lws policy draft list` returns 16 drafts, all `approved`; none is pending.
`docs/pending_policy_drafts/` still holds 15 JSON records of them plus a README that describes them
as unactivated (see the discrepancy notes).

## Policies that apply to all projects they are attached to

| Topic | Pillar | Gate | Rule |
|---|---|---|---|
| `version-format` | version | branch_finish | `VERSION` is `M.m.p`, optionally `-dev.N` or `-rc.N` |
| `commit-cites-requirement` | version | branch_finish | A commit changing a file under `src/` cites `REQ-nnnnnnnnn` or `REQ-NONE`, never both |
| `merge-commit-message-format` | version | branch_finish | A newest merge commit on the integration or production branch reads `feat\|impr\|fix (<issue or -1>): <title>` |
| `version-hook-installed` | version | branch_finish | Where another file carries the version, the VERSION auto-sync hook is installed and a propagation script registered |
| `production-version-stable` | version | release | On the production branch, `VERSION` has no pre-release suffix |
| `production-tag-format` | version | release | A production tag is annotated `v<M>.<m>.<p>` |
| `production-tags-on-production-history` | version | release | The newest production tag is reachable from the production branch |
| `rc-suffix-only-on-release-branches` | version | release | An `-rc.N` `VERSION` implies a `release/*` branch |
| `release-candidate-tag-format` | version | release | RC tag `v<M>.<m>.<p>-rc.N` annotated, `N` starting at 1 and increasing by one |
| `requirement-marker-registered` | requirements | branch_finish | pytest configuration registers a `requirement` marker |
| `requirement-marker-id-format` | requirements | branch_finish | Markers reference `REQ-` plus nine digits |
| `python-test-coverage` | requirements | branch_finish | Python suite covers at least 90 percent of statements; skipped for non-Python projects |
| `issue-form-installed` | agent | branch_finish | Where issue-driven automation is used, `.github/ISSUE_TEMPLATE/claude-task.yml` exists |
| `ssh-remotes-no-embedded-credentials` | registry | branch_finish | Git remotes are SSH URLs without embedded credentials |
| `wheel-filename-has-version` | build | package_publish | A built wheel's name carries the project version |

## Policies scoped to one project

Scope level `project`, target `little-workshop`, all created by the user. Their documented scope is
little-workshop only; their actual attachment is wider (see the matrix and the discrepancy notes).
See [little_workshop_standards/](little_workshop_standards/).

| Topic | Gate | Rule |
|---|---|---|
| `commit-bumps-version` | branch_finish | Each commit's `VERSION` has higher precedence than its parent's |
| `branch-commit-dev-suffix` | branch_finish | On a working branch, `VERSION` is next patch of the branch point plus `-dev.N` (`N`: 1, then +1) |
| `back-merge-no-ff` | branch_finish | A release or hotfix branch is merged back with a non-fast-forward merge |
| `hotfix-propagates-by-merge` | branch_finish | A hotfix reaches the integration branch by merge, never cherry-pick |

Policies created by the user rather than the framework: these four plus `commit-cites-requirement`,
`python-test-coverage` and `wheel-filename-has-version`; the other twelve are `framework`-created.

## Attachment matrix (gating on)

| Project | Policies gating | Notes |
|---|---|---|
| little-workshop | 19 | All |
| little-home, little-meals, little-mindcheck | 17 | Everything except `back-merge-no-ff` and `hotfix-propagates-by-merge` |
| little-spectrum | 3 | Only `commit-bumps-version`, `branch-commit-dev-suffix`, `version-hook-installed`; the other 14 are attached with gating off |
| ethereal-lands | 0 | Not attached to any policy |

The full matrix, `G` gating on, `-` attached with gating off, blank not attached:

| Policy | workshop | home | meals | mindcheck | spectrum | ethereal-lands |
|---|---|---|---|---|---|---|
| `commit-bumps-version`, `branch-commit-dev-suffix`, `version-hook-installed` | G | G | G | G | G | |
| `back-merge-no-ff`, `hotfix-propagates-by-merge` | G | | | | | |
| the other 14 | G | G | G | G | - | |

Latest recorded check outcomes (`lws policy results`) are not reproduced here: they change on every
check.

## Discrepancies to resolve

- `commit-bumps-version` and `branch-commit-dev-suffix` have scope `project` / `little-workshop` and
  `docs/version_policy.md` documents them as scoped to little-workshop, but they are attached with
  gating on to little-home, little-meals, little-mindcheck and little-spectrum too. By the pillar's
  rule the attachment, not the scope level, decides, so those four are bound by the per-commit
  version rule today.
- `version-hook-installed` is the one framework policy attached to little-spectrum with gating on;
  the other 14 policies attached there are gating off.
- ethereal-lands has no policy attachment, so none of the policies is checked on it, although it now
  has a `VERSION`, a `.lvx/config.json` and merge commits in the `feat (-1): ...` form.
- `issue-form-installed` gates four projects, and none of the registered checkouts has
  `.github/ISSUE_TEMPLATE/claude-task.yml`; its latest recorded outcome is `not_applicable` on all
  five attached projects, so it blocks nothing.
- `docs/pending_policy_drafts/README.md` still says no draft is activated and that none has gate
  points; `lws policy draft list` says all 16 are approved and every one of the 19 policies is
  enabled with gate points. The live system is authoritative.
- `docs/requirements_policy.md` says `commit-cites-requirement` is "filed as a draft for approval";
  it is an approved, enabled policy gating `branch_finish`.

Last reviewed: 2026-10-01.
