# little-workshop standards

Checkout: `/home/crist/little-projects/little-workshop`. Repository:
`git@github.com:dfluxcbf/little-workshop.git`. Python, FastAPI pillars, Bazel; Dashboard, `lws` CLI
and MCP server are thin proxies over the pillars. This file is imported into every Claude session
in the project (by `.claude/AGENTS.md`), so it stays short and current.

## Governing documents

| Document | Role |
|---|---|
| `docs/governance_policy.md` | Constitution: independence, evolution, self-application, amendments |
| `docs/requirements_policy.md` | Own requirements policy (exempt from the super-repo's) |
| `docs/version_policy.md` | Own version policy (exempt from the super-repo's) |
| `docs/requirements.md` | Interim requirements register, mirrored in the Requirements pillar |
| `docs/pillars/*.md` | One specification per pillar |
| `docs/open_questions.md` | `OQ-NNN` tracker |

## Policies, as code

Every Policy & Compliance policy is a script in `tools/policy_scripts/` plus its entry in
`tools/policy_scripts/policies.json` (rule, scope, gate points); `python3 tools/sync_policies.py`
shows what differs from the live pillar and `--apply` replaces it. Besides the policies every project
is attached to ([../policy_register.md](../policy_register.md)), little-workshop's own are:

- `commit-bumps-version` (also gating `commit`): every commit raises `VERSION` precedence over its
  parent.
- `branch-commit-dev-suffix` (also gating `commit`): a working branch's first commit is the next patch
  plus `-dev.1`, later commits count up.
- `back-merge-no-ff`, `hotfix-propagates-by-merge`: a finished release or hotfix reaches `dev` through a
  non-fast-forward merge, never a cherry-pick (judged on history: the newest production tag).

## Conventions particular to this project

- `.githooks/pre-commit` syncs `VERSION` on `feature/*` and `improvement/*` commits (`.lvx/config.json`
  registers `tools/on_version_bump.py`); `.githooks/commit-msg` runs the policies gating `commit`.
  `lws version install-hooks --project-id little-workshop` (re)installs both.
- Run `tools/sync_versions.py` whenever `VERSION` changes; `pyproject.toml` is never edited by hand.
- Commits that change `src/` cite `REQ-nnnnnnnnn` or `REQ-NONE`. Subjects read
  `feat|impr|fix (<issue or -1>): <title>`.
- Test suites: `tests/unit` (the DoD "Unit tests" suite, never skipping in a sandbox),
  `tests/integration` (stops the live pillars: never beside the live install), `tests/ui` (Playwright).
  See [../testing_standard.md](../testing_standard.md).
- New user-triggerable jobs are `JobSpec` entries in `src/dashboard/job_catalog.py`; never free-form.
- Agent prompt templates are in `src/agent/templates/prompts/`; changing them changes every task, so
  update [../agent_task_standard.md](../agent_task_standard.md) in the same change.

Last reviewed: 2026-10-02 (hand edit: policies as code, commit-time policies, file imported by
`.claude/AGENTS.md`).
