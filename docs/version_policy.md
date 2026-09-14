# Version policy

**little-workshop is exempt from the super-repo's
[version policy](../../docs/policies/version_policy.md)** — see
`docs/governance_policy.md` for why, and `docs/README.md`'s "Exemption:
little-workshop" for where the super-repo itself sanctions this. This file
states little-workshop's own version policy in full; it does not point back
to the root document, because it is not bound by it.

## What this means concretely

- There is **no** `.lvx/config.json` and no dependency on `lvx`
  (`little-versions`). Branches and version bumps are not managed through
  that tool.
- This is not a new pattern in this ecosystem: the super-repo's own root
  checkout already manages its `master`/`dev` branches by hand, with plain
  `git merge --no-ff`, for exactly the reason stated in the root
  [branching policy](../../docs/policies/branching_policy.md) — "The root
  repo is not `lvx`-managed." little-workshop makes the same judgment about
  itself, for the reason `docs/governance_policy.md` explains.

## What little-workshop actually does

- **Two branches, kept because they're valuable, not because a policy
  mandates them**: `master` (always releasable) and `dev` (integration).
  Both already exist, both already pushed.
- **Merges use plain `git merge --no-ff`**, the same mechanism the
  super-repo's own root checkout uses on itself.
- **`VERSION`** stays a plain-text file at the repo root, bumped by hand
  when a real change warrants it — not on every commit, not automatically.
  There is no version hook yet, because there is no version-bearing
  metadata file yet to keep in sync (once one exists, a hook gets added
  under `tools/`, the same convention the rest of this ecosystem uses —
  that convention is kept because it's a good idea, not because `lvx`
  requires it).
- **No `feature`/`improvement`/`hotfix`/`release`/`support` branch
  taxonomy is mandated.** little-workshop may still use short-lived
  branches for real work later, named however is clearest at the time —
  without a tool enforcing a fixed prefix vocabulary on them.

## What this is not

This is not a rejection of gitflow's ideas — a stable branch and an
integration branch are still exactly what little-workshop uses. It's a
rejection of being *required to use `lvx` specifically* to manage them,
which is a different, narrower claim: the ideas are valuable and kept; the
tool dependency is not, because it's one of the tools this project exists
to eventually replace.

## Project-specific notes

- `master` and `dev` both exist and are both pushed to
  `github.com/dfluxcbf/little-workshop`.
- little-runner's cross-repo branch audit (per the root
  [branching policy](../../docs/policies/branching_policy.md)) still
  observes this repository for basic git hygiene (stray branches, detached
  HEAD, extra worktrees) — that's little-runner watching, not
  little-workshop depending on anything. Being observed costs this project
  nothing and imposes no rule on it; the audit is read-only by its own
  design.
- This section will be rewritten, not just appended to, once the Version
  Control & Release pillar exists and little-workshop can manage its own
  branches through its own tooling — its own first user, per the
  self-application principle in `docs/governance_policy.md`.
