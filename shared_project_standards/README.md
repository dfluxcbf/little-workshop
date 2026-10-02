# Shared project standards

Everything that affects how the projects little-workshop manages are built, documented,
versioned, tested and released, compiled in one place. A person or an agent working on any
registered project reads this folder first.

## Rules for this folder

- **Markdown only.** No scripts, JSON, or generated data live here. Policy scripts and their
  rules live in little-workshop's `tools/policy_scripts/` (`policies.json`) and in the Policy &
  Compliance pillar's database, which `tools/sync_policies.py` keeps equal.
- **It compiles; it does not originate.** Each file summarises a rule and names its authoritative
  source. Where a source and this folder disagree, the source wins and this folder is wrong.
- **Shared standards at the top level; per-project material in `<project>_standards/`.** A policy
  that applies to every project goes in the shared files. A policy scoped to specific projects, and
  any project-specific convention (an `AGENTS.md` rule, a toolchain quirk), goes in that project's
  folder.
- **Kept current by the "Update Standards Documentation" job**, which reviews every little-workshop
  standard and every policy and rewrites these files (see [Maintenance](#maintenance)).
- **Inconsistencies between sources are recorded, not resolved silently**, in a "Discrepancy notes"
  section of the file they affect.

## Shared standards

| File | What it covers |
|---|---|
| [governance.md](governance.md) | Independence, evolution and self-application principles; how rules change |
| [documentation_standard.md](documentation_standard.md) | Required project documents, their structure, and writing rules |
| [requirements_standard.md](requirements_standard.md) | Requirement ids, EARS wording, lifecycle, `REQ-NONE`, commit citations |
| [version_and_branching_standard.md](version_and_branching_standard.md) | Branch model, `VERSION` format, per-commit bumps, tags, merge messages |
| [build_and_structure_standard.md](build_and_structure_standard.md) | Folder layout, Bazel targets, portability, dependency isolation |
| [testing_standard.md](testing_standard.md) | Test layout, requirement markers, coverage, UI tests, the DoD test run |
| [agent_task_standard.md](agent_task_standard.md) | What an unattended Claude task must do: DoD, commits, branches, report |
| [policy_register.md](policy_register.md) | Every active Policy & Compliance policy, its scope and gate point |

## Per-project standards

| Folder | Project | Checkout |
|---|---|---|
| [little_workshop_standards/](little_workshop_standards/) | little-workshop | `/home/crist/little-projects/little-workshop` |
| [ethereal_lands_standards/](ethereal_lands_standards/) | ethereal-lands (Unity, Windows) | `/mnt/c/little-projects/ethereal-lands` |
| [little_meals_standards/](little_meals_standards/) | little-meals | `/home/crist/little-projects/little-meals` |
| [little_home_standards/](little_home_standards/) | little-home | `/home/crist/little-projects/little-home` |
| [little_mindcheck_standards/](little_mindcheck_standards/) | little-mindcheck | `/home/crist/little-projects/little-mindcheck` |
| [little_spectrum_standards/](little_spectrum_standards/) | little-spectrum | `/home/crist/little-projects/little-spectrum` |

A folder is named after the project with `-` replaced by `_` plus `_standards`. A newly registered
project gets a folder on the next run of the maintenance job.

## Maintenance

The job **Update Standards Documentation** (Dashboard, New Job; offered for little-workshop only) is
the only way these files are updated after the first compilation. It is defined by
`STANDARDS_DESCRIPTION` in `src/dashboard/job_catalog.py` **on branch `feature/doc-review`**; the
catalog on `dev` (checked 2026-10-01) holds only `build`, `run_tests` and `release`. It runs an
agent task (intent `docs`, manual review) that asks Claude to:

1. review every little-workshop standard: `docs/governance_policy.md`, `docs/requirements_policy.md`,
   `docs/version_policy.md`, `docs/architecture.md`, `docs/design.md`, `docs/milestones.md`,
   `docs/open_questions.md`, `docs/pillars/*.md`, the agent prompt templates under
   `src/agent/templates/prompts/`, `src/dashboard/job_catalog.py`, and the super-repo's
   `docs/policies/*.md`;
2. review every policy registered in Policy & Compliance (`lws policy list`,
   `lws policy projects list`), their code in `tools/policy_scripts/`, plus any pending drafts in
   `docs/pending_policy_drafts/`;
3. review every registered project's `.claude/AGENTS.md`, `README.md` and `docs/*_policy.md`;
4. update every document in this folder, add a folder for any project that lacks one, and keep the
   folder of a project that is no longer registered, saying so in its README.

Hand edits are allowed for a one-off correction but must be dated in the file's "Last reviewed" line
and will be reconciled by the next job run.

Last reviewed: 2026-10-01 (hand edits 2026-10-01: instructions moved to `.claude/AGENTS.md`;
2026-10-02: policies as code).
