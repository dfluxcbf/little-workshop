# Milestones

Follows the super-repo's [documentation policy](../../docs/policies/documentation_policy.md)
milestone rules, adapted for this project's own kept-not-mandated version
and requirements conventions (`docs/version_policy.md`,
`docs/requirements_policy.md`): each milestone is worked on its own
short-lived branch (plain `git`, not `lvx`), and is only done once
documented, its requirements are created/updated in `docs/requirements.md`
(not an `lreq` database), implemented, covered by automated tests, and
those tests pass.

## Milestones

| ID | Objective | Requirements | Status |
|---|---|---|---|
| M1 | **Core + Orchestration** — the substrate every pillar depends on, built and proven before any real pillar exists. Core: `manifest.toml`, pillar registry, `systemd --user` lifecycle per enabled pillar, health probing (5s timeout, 10 retries, each pillar's own internal report — not a bare ping), crash-relaunch (10 tries, 1 minute apart, then **faulted**), reconnect-not-relaunch on Core's own restart. Orchestration: the `Job` model (`id, type, target_pillar, project_id, params, status, queue_key, depends_on, created_by, result`), dispatch over a pillar's Pillar Interface, the `queue_key` mutex, the self-pumping queue, `depends_on` resolution. Proven end-to-end against one minimal reference/stub pillar built for this milestone only (a trivial health endpoint + one no-op Job type) — not a real pillar's own implementation, which is later work. | `docs/requirements.md`, to be drafted as this milestone's own first task | Not started |
