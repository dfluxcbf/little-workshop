# little-workshop standards

Checkout: `/home/crist/little-projects/little-workshop`. Repository:
`git@github.com:dfluxcbf/little-workshop.git`. Python, FastAPI pillars, Bazel; Dashboard, `lws` CLI
and MCP server are thin proxies over the pillars.

## Governing documents

| Document | Role |
|---|---|
| `docs/governance_policy.md` | Constitution: independence, evolution, self-application, amendments |
| `docs/requirements_policy.md` | Own requirements policy (exempt from the super-repo's) |
| `docs/version_policy.md` | Own version policy (exempt from the super-repo's) |
| `docs/requirements.md` | Interim requirements register, mirrored in the Requirements pillar |
| `docs/pillars/*.md` | One specification per pillar |
| `docs/open_questions.md` | `OQ-NNN` tracker |

## Project-specific policies

Scope `project`, target `little-workshop`, gate `branch_finish` (details in
[../policy_register.md](../policy_register.md)):

- `commit-bumps-version`: every commit raises `VERSION` precedence over its parent.
- `branch-commit-dev-suffix`: first commit of a working branch is next patch plus `-dev.1`, later
  commits count up.
- `back-merge-no-ff`, `hotfix-propagates-by-merge`: merges, never cherry-picks, and no
  fast-forward.

Their scripts live in `tools/policy_scripts/` and compare only the newest commit with its parent.
Documented as little-workshop-only, but the first two are also attached with gating on to four other
projects ([../policy_register.md](../policy_register.md)).

## Conventions particular to this project

- `.lvx/config.json` (tracked) registers `tools/on_version_bump.py`; `.githooks/pre-commit` syncs
  `VERSION` on `feature/*` and `improvement/*` commits. `docs/governance_policy.md` still says there
  is no `.lvx/config.json` (see [../governance.md](../governance.md)).
- Run `tools/sync_versions.py` whenever `VERSION` changes; `pyproject.toml` is never edited by hand.
- Commits under `src/` cite `REQ-nnnnnnnnn` or `REQ-NONE`. Prefix commit subjects with `feat:`,
  `impr:` or `fix:`.
- Test suites: `tests/unit` (the DoD "Unit tests" suite, never skipping in a sandbox), `tests/integration`,
  `tests/ui` (Playwright). See [../testing_standard.md](../testing_standard.md).
- New user-triggerable jobs are `JobSpec` entries in `src/dashboard/job_catalog.py`; never free-form.
  On `dev` the catalog holds `build`, `run_tests`, `release`; the "Update Standards Documentation"
  job (little-workshop only) is on `feature/doc-review`.
- Servers (little-meals, mindcheck, home) are launched on demand; down is normal.
- The state home is `~/.config/little-workshop`; pillars run as `systemd --user` units.
- Agent prompt templates are in `src/agent/templates/prompts/`; changing them changes every task, so
  update [../agent_task_standard.md](../agent_task_standard.md) in the same change.

Last reviewed: 2026-10-01.
