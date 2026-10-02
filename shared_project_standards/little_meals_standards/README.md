# little-meals standards

Checkout: `/home/crist/little-projects/little-meals`. Repository `git@github.com:dfluxcbf/little-meals.git`.
Meal-planning facilitator and recipe book for a two-person household. Python, FastAPI, Bazel
(`rules_python`, Python 3.12), packaged as a wheel; runs as `lmeals serve`.

## Governing documents in the project

`.claude/AGENTS.md`, `README.md`, `docs/design.md`, `docs/architecture.md`, `docs/milestones.md`,
`docs/requirements_policy.md`, `docs/version_policy.md`, `docs/first_run.md`, `docs/ui_design.md`.

## Project conventions

- **Relaunching the app:** `bazel run //:relaunch` rebuilds and reinstalls the wheel, then kills
  `lmeals serve` so the crash-respawning server wrapper restarts it on the new build. It does not start
  the app if it is not running. `//:deploy` is the older systemd path.
- **Canonical Bazel targets:** `//:preflight`, `//:install`, `//:serve`, `//:deploy`, `//:relaunch`,
  `//:test`, `//:requirements.update` (full list in the header of `BUILD.bazel`).
- **Version propagation:** the version hook `tools/on_version_bump.py` writes `VERSION` into
  `pyproject.toml`, `MODULE.bazel`, `src/little_meals/BUILD.bazel` and
  `src/little_meals/__init__.py`.
- Requirements tests are tagged `@pytest.mark.requirement("REQ-XXXXXXXXX")`; per-milestone id ranges
  are in `docs/requirements_policy.md`.
- Its server runs on demand; down is normal. Do not restart it automatically.

## Documentation to reconcile

`docs/requirements_policy.md` and `docs/version_policy.md` still say requirements and
versions are managed exclusively through `lreq` and `lvx` and by a `bundle-littles` database. The
shared standards say little-workshop's Requirements and Version pillars are the system of record and
that `lreq`/`lvx` and `bundle-littles` were uninstalled on 2026-09-22 (as little-mindcheck's policy
files record). Verify which is true for this project before following either, then correct the stale
statement.

## Policies

17 of 19 policies gate this project, every one except `back-merge-no-ff` and
`hotfix-propagates-by-merge` ([../policy_register.md](../policy_register.md)). No policy is scoped to it
alone.

Last reviewed: 2026-10-01 (hand edit 2026-10-01: instructions moved to `.claude/AGENTS.md`).
