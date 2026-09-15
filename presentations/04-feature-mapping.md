← [Jobs, Pipelines, Policy & Recovery](03-execution-model.md) · deck 4 of 8 · next: [Status & What's Next →](05-status-and-roadmap.md)

# What Each Old Tool Becomes

Every capability below traces to a real file this project read directly —
each tool's own `docs/design.md`/`docs/architecture.md` — or to the
standalone FOSS-consolidation research that preceded this project. Nothing
here is invented for the pitch.

## little-requirements → Requirements & Quality pillar

- Project → Test Suite → Requirement → TestResult, status cascading.
- `@track_requirement`/`@pytest.mark.requirement` — automatic pass/fail
  capture, no manual logging.
- Draft → published requirement lifecycle.
- **Its own never-built M8/E6**, now actually implemented: a Use-Case/
  Milestone axis independent of test suites; JUnit-XML ingestion beyond
  pytest; on-demand (not just per-PR) testing.

---

## little-versions → Version Control & Release pillar

- Precondition-checked Gitflow: feature/improvement/hotfix/release/support
  start & finish.
- Release-candidate cut/list/promote; branch↔issue association.
- Safety gates: refuses dirty tree or wrong base, `--yes` required for
  destructive ops, never auto-resolves conflicts.
- **Its own documented gap**, closed by construction: the MCP module
  agents actually use didn't live in the repo's own source tree. A
  pillar's interface *is* the one surface every caller goes through — the
  gap can't recur.

---

## little-runner → six pillars at once

little-runner was already the biggest of the five tools, and it splits
across the most pillars: Orchestration, Policy & Compliance, Registry &
Portfolio (the audit half), Requirements & Quality (the testing half),
Build & Packages (the CI half), Deployment & Servers, Agentic Development,
and Dashboard.

- Its stated non-goals — "not a general CI system," "not an interactive UI
  beyond GitHub" — are lifted.
- Its trigger source generalizes from GitHub-issue-only to any Job source.
- Every safety property (branching control, one-flow-per-project,
  confirm-gated self-restart, cgroup-aware dispatch) carries over
  unchanged — and the *mechanism itself* becomes the general-purpose way
  every pillar gets safely redeployed, including Core and Deployment &
  Servers themselves.

---

## little-pypi → Build & Packages pillar

- Same PEP 503 + `.vsix` model.
- The 5-minute-session compromise is dropped — that only ever existed
  because `little-pypi` had no persistent process to live inside. This
  pillar already is one.

---

## bootstrap-little-project → Registry & Portfolio pillar

- One command: scaffold → `git init` → `lvx init` → `dev` branch → GitHub
  repo + push.
- **Its own stated non-goals**, closed by construction: registering the
  submodule and wiring the requirements DB were left manual because a
  single project's scope couldn't reach shared files. Now both touch one
  pillar's own table.
- **Extended with a capability it never had**: importing an *existing*
  project, not just scaffolding a new one — see
  [15 — Who Owns a Project?](15-project-ownership.md).

---

## little-projects (informal role) & little-tickets → Registry & Portfolio + Policy & Compliance

- The 8 root policy docs, `docs/milestones.md`'s ecosystem table,
  `configure_submodules.sh`, `stabilize_branches.py`/`branch_audit.py` —
  become scheduled/on-demand Jobs and pillar-owned rows with full history.
- `little-tickets`: scaffolded, committed, and — per its own docs — still
  "purpose not yet defined." This is that purpose.
- The super-repo container itself does **not** go away — only its *active*
  tooling role migrates in.

---

## Research-derived ideas, not built anywhere (yet)

| Source | Informs |
|---|---|
| Doorstop / StrictDoc | Git-native requirement traceability patterns |
| Kiwi TCMS / Tuleap | Structured test-plan workflows, distinct from ad hoc runs |
| Forgejo / Gitea Actions | The Pipeline definition's YAML vocabulary |
| OpenHands / Aider | Sandboxed agent-execution patterns for test isolation |
| Coolify / CapRover | Dashboard server-panel UX — explicitly *not* the container-first deploy model |
| Uptime Kuma / Healthchecks | Heartbeat + status-page patterns for pillar health probes |
| RQ / Huey / Celery | Considered and rejected — no distribution problem exists at this scale |
| OpenProject / Redmine / Tuleap | Multi-project roadmap UI patterns for the Registry & Portfolio pillar |

---

Next: [5 — Status & What's Next →](05-status-and-roadmap.md)
