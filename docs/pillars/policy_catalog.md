# Policy catalog — draft, pending review

**Status: none of this is active.** Every entry below is a *candidate*
mined from this ecosystem's existing projects and documented decisions, not
yet run through the actual authoring pipeline in
`policy_compliance.md` (EARS-compliance check → Claude CLI refinement →
human review → test-script generation). Treat this document the way that
pipeline treats a freshly-submitted policy: a draft awaiting a decision,
not a record of one. See "Open questions for you" at the bottom before
anything here gets promoted to a real `policies` row.

**Sources investigated**: the 8 root policy documents
(`../../../docs/policies/*.md`); the design/architecture docs of
`little-requirements`, `little-versions`, `little-runner`, `little-pypi`,
`bootstrap-little-project`; and a pass over every other submodule's own
`docs/architecture.md` Technical Decisions (`little-meals`,
`little-mindcheck`, `little-termstyle`, `little-mapgen`, `little-home`;
`little-spectrum` and `little-tickets` have none to mine — the latter is
still a blank skeleton).

## Principle: name the concern, not today's tool

This mattered enough to check before drafting a single entry. A framework
policy that hardcodes `lreq` or `lvx` by name would immediately contradict
`docs/governance_policy.md` the moment little-workshop tries to apply its
own framework policies to itself — it doesn't use either. Every candidate
below that used to name one of the four tools little-workshop is absorbing
(`little-requirements`, `little-versions`, `little-runner`, `little-pypi`)
is written against **the designated mechanism for that concern** instead,
satisfied today by the old tool and, later, by little-workshop's own
pillar without the policy text ever needing to change. Policies naming
tools little-workshop is *not* replacing (`git`, `gh`, `Bazel`, `Claude
CLI`, `systemd`, `Tailscale`) name them concretely — there's no analogous
circularity risk there.

## Excluded: these are code, not policy

Already established in `policy_compliance.md`'s three-tier model — listed
here so their absence from the tables below reads as deliberate, not
missed:

- "Automation never merges, deletes, or finishes a branch on its own
  initiative" (`automation_policy.md` rules 4 &amp; 9; `branching_policy.md`
  rule 5).
- "The agent never touches branches when a Job specifies them."
- The branch/policy audit itself never merges, deletes, or pushes — it only
  reads and reports.

## A. Project structure &amp; inter-project independence

| Topic | EARS requirement | Source |
|---|---|---|
| `standard-folders` | The project shall contain `docs/`, `src/`, `tests/`, and `tools/` folders at its root. | `project_structure_policy.md` |
| `extra-folders-documented` | WHERE a project has folders beyond the standard layout, the project's `docs/architecture.md` shall document their purpose. | `project_structure_policy.md` |
| `no-cross-project-source-imports` | The project shall not import another project's source directly — no path hacks, relative imports, or build-system dependencies reaching into a sibling's `src/`. | `project_structure_policy.md` |
| `dependency-via-installed-version` | WHEN a project depends on another project's functionality, the project shall consume that dependency's installed form (CLI on `PATH`, installed package, registered interface) rather than its source checkout. | `project_structure_policy.md` |
| `architecture-lists-dependencies` | The project's `docs/architecture.md` shall list which other projects it depends on and confirm each is consumed via an installed version. | `project_structure_policy.md` |

## B. Documentation

| Topic | EARS requirement | Source |
|---|---|---|
| `standard-doc-set` | The project shall contain `docs/design.md`, `docs/architecture.md`, `docs/requirements_policy.md`, `docs/version_policy.md`, and `docs/milestones.md`. | `documentation_policy.md` |
| `doc-no-duplication` | IF the same content appears in two documentation files, THEN the project's documentation shall be corrected to state it once and link to that one authoritative source. | `documentation_policy.md` |
| `undefined-purpose-honesty` | WHERE a project's purpose or design is not yet defined, the project's documentation shall state that plainly rather than contain fabricated content. | `documentation_policy.md`; `bootstrap-little-project` design.md (same principle, independently stated) |
| `milestone-definition-of-done` | WHEN a milestone is marked done, the milestone shall have updated documentation, created/updated requirements, complete implementation, and passing automated tests. | `documentation_policy.md`; `requirements_policy.md` |
| `milestone-own-branch` | Each milestone shall be worked on its own short-lived branch. | `documentation_policy.md` |

## C. Version control &amp; branching

| Topic | EARS requirement | Source |
|---|---|---|
| `no-manual-branch-ops` | The project shall not create, merge, or delete a gitflow branch by hand — always through the project's designated version-control mechanism. | `version_policy.md` |
| `no-manual-version-bump` | The project shall not edit its `VERSION` file by hand. | `version_policy.md` |
| `version-file-single-source` | The project shall keep its current version in a plain-text `VERSION` file at its root. | `version_policy.md` |
| `version-hook-sync` | WHERE a project has version-bearing metadata files, the project shall have a version hook that keeps them synced with `VERSION`. | `version_policy.md` |
| `precondition-checked-transitions` | IF a branch or version operation's preconditions are not met (dirty tree, wrong base branch, unresolved conflict), THEN the system shall refuse the operation rather than auto-resolve it. | `little-versions` design.md |
| `squash-before-integration-merge` | WHEN a feature/improvement branch is finished, the system shall rebase and squash it onto the integration branch before merging with a non-fast-forward merge. | `little-versions` architecture.md |
| `two-long-lived-branches` | The project shall keep exactly two long-lived branches. | `branching_policy.md` |
| `branch-naming-convention` | The project's branches shall be named from the fixed set (the two long-lived names, or one of the defined short-lived prefixes). | `branching_policy.md` |
| `stale-merged-branch` | IF a branch is fully merged into every branch it targets and still exists, THEN it shall be reported as a stale-merged violation. | `branching_policy.md` |
| `abandoned-branch` | IF an unmerged short-lived branch has had no commits for longer than its project's staleness window, THEN it shall be reported as an abandoned-branch violation. | `branching_policy.md` |
| `no-detached-head` | IF a repository has a detached `HEAD` outside a brief, tool-managed operation, THEN it shall be reported as a violation. | `branching_policy.md` |
| `single-worktree` | IF a repository has more than one active `git worktree` outside a short-lived, tool-managed one, THEN it shall be reported as a violation. | `branching_policy.md` |

## D. Requirements &amp; quality

| Topic | EARS requirement | Source |
|---|---|---|
| `requirements-via-designated-mechanism` | The project shall not edit or query its requirements record directly — only through the project's designated requirements-tracking mechanism. | `requirements_policy.md` |
| `requirement-id-format` | The system shall generate requirement identifiers in one fixed, unambiguous format, never invented or chosen freely by a person. | `requirements_policy.md` |
| `automatic-test-reporting` | WHEN a project's test suite runs, the system shall automatically record pass/fail results against the requirements that suite is tracked against. | `requirements_policy.md`; `little-requirements` design.md |
| `draft-published-lifecycle` | A requirement shall have a status of draft or published; draft shall be mutable and the default, published shall be immutable. | `little-requirements` design.md |
| `local-only-no-auth` | WHERE a tool runs a local HTTP server, the server shall bind to localhost with no built-in authentication and shall never be exposed except via Tailscale. | `little-requirements` architecture.md; generalizes the same choice in `little-pypi`, `little-runner` |

## E. Build &amp; packaging

| Topic | EARS requirement | Source |
|---|---|---|
| `bazel-canonical-build` | The project shall use Bazel as its canonical build system. | `build_policy.md` |
| `standard-build-targets` | WHERE a project produces an artifact type covered by a standard target name, the project's build file shall expose that target under its standard name. | `build_policy.md` |
| `tests-need-no-manual-preinstall` | The project's tests shall be runnable with no manual install step beyond what the build/test runner performs itself. | `build_policy.md` |
| `no-hardcoded-paths-or-usernames` | The project's build commands shall not depend on a specific username or a hardcoded absolute path. | `build_policy.md` |
| `isolated-installs` | The project's install targets shall install CLI tools and dependencies into an isolated location (a per-tool virtualenv, a project-local `.venv`, a project-local `node_modules`), never into shared or system state. | `build_policy.md`; `little-home` architecture.md (independently applied to its Node toolchain) |
| `preflight-dependency-check` | WHEN a build/install step depends on a system tool that can't be isolated, the project shall verify its presence before doing real work and report every missing dependency in one pass with a copy-pasteable install command. | `build_policy.md`; `bootstrap-little-project` architecture.md |
| `pep503-index-format` | WHERE a project serves a package index, the index shall follow the PEP 503 simple-repository format. | `little-pypi` design.md |

## F. Interface registration

| Topic | EARS requirement | Source |
|---|---|---|
| `mcp-user-scope-registration` | WHEN a project installs an MCP server, the system shall register it at user scope, never local or project scope. | `mcp_policy.md` |
| `mcp-install-script-owns-registration` | The project's install script shall be the only place that registers or removes its MCP server. | `mcp_policy.md` |

## G. Automation &amp; agentic-development safety

| Topic | EARS requirement | Source |
|---|---|---|
| `host-local-automation-config` | The project's automation configuration (trigger conditions, allowed actors, CI/server commands) shall live host-locally and shall never be committed to the repository. | `automation_policy.md` rule 1 |
| `one-flow-per-project` | The system shall hold at most one active automation flow per project at a time; additional triggers shall queue. | `automation_policy.md` rule 7 |
| `every-run-reports` | WHEN an automated run completes, the system shall produce a structured report including background, decisions made, tasks executed, and test results. | `automation_policy.md` rule 5 |
| `remote-control-visibility` | WHEN a development agent runs, the system shall expose it live so the run can be watched and steered while it happens. | `automation_policy.md` rule 6 |
| `pr-tested-before-finish` | IF a pull request's last recorded test run failed, THEN the system shall refuse to finish that branch unless a human explicitly forces it. | `automation_policy.md` rule 8 |
| `fork-safety-gate` | IF a pull request's head repository is not the project's own, THEN the system shall refuse to test or act on it. | `little-runner` architecture.md |
| `claude-cli-budget-and-timeout` | WHEN Claude CLI runs unattended, the system shall enforce a maximum budget and a maximum timeout. | `little-runner` architecture.md |
| `no-secrets-anywhere` | The system shall not store or log a credential; external service calls shall reuse the host's already-authenticated session rather than an embedded token. | `automation_policy.md` rule 11 |

## H. Server &amp; deployment

| Topic | EARS requirement | Source |
|---|---|---|
| `servers-declared-not-scripted` | WHERE a project runs a server, the project shall declare it once in a manifest (command, port, process-match) rather than via a bespoke script. | `automation_policy.md` rule 10 |
| `worktree-based-deploy` | WHEN a server is deployed, the system shall build and install from an isolated checkout, never the project's own primary working tree. | `little-runner` design.md, "Worktree deploys" |
| `build-before-stop` | WHEN a server is redeployed, the system shall install the new version before stopping the old one. | `little-runner` design.md |
| `confirm-gated-critical-deploys` | WHERE a redeploy could remove the system's own access or control channel, the deploy shall require explicit confirmation within a timeout and automatically roll back to the last confirmed version if unconfirmed. | `little-runner` design.md, "Confirm-gated deploys"; `docs/pillars/policy_compliance.md`'s action-gating model |
| `defence-in-depth-stop` | WHEN a server is stopped, the system shall attempt every known way that server could have been started, not assume only one applies. | `little-runner` architecture.md |

## I. Security &amp; network exposure (cross-cutting)

| Topic | EARS requirement | Source |
|---|---|---|
| `tailscale-only-exposure` | The system's dashboards and servers shall be reachable only via a private-mesh "serve" mechanism, never a public-internet "funnel" or equivalent. | Independently established in `little-runner`, `little-meals`, and `little-home` architecture.md — three-for-three is a strong signal this belongs at framework tier. |
| `no-redundant-app-auth` | WHERE a project is reachable only via the system's private network, the project shall not additionally require an app-level username/password. | `little-meals` architecture.md |
| `no-third-party-cdn` | WHERE a project serves a web frontend, the project shall vendor its JS/CSS/font dependencies rather than loading them from a third-party CDN at runtime. | `little-meals` architecture.md (htmx, fonts) |
| `disk-encryption-not-db-encryption` | WHERE a project stores data at rest under this system's single-host, private-network model, the project shall rely on host full-disk encryption rather than implementing database-level encryption. | `little-mindcheck` architecture.md — narrower justification than the others in this section; flagged for confirmation before promoting, see "Open questions." |

## J. Dashboard / UX conventions

| Topic | EARS requirement | Source |
|---|---|---|
| `status-not-color-alone` | The system's UI shall never convey status by color alone; every status indicator shall also carry a text label. | `little-runner` `docs/ui_design.md`; already the working rule behind little-workshop's own visual-identity mockups |

## K. Little-workshop's own architecture (self-referential)

Candidates for the "little-workshop itself" authoring tab specifically —
these check little-workshop against its own design, not a convention
borrowed from elsewhere:

| Topic | EARS requirement | Source |
|---|---|---|
| `pillar-exclusive-storage` | Each pillar shall own exactly one exclusive database; no other pillar or process shall open it. | `docs/architecture.md`, Data model |
| `pillar-interface-only-access` | A pillar shall be reachable only through its own declared Pillar Interface. | `docs/architecture.md`, Pillar Interface |
| `project-file-write-ownership` | IF a pillar attempts to write a project file whose owner tag is not its own, THEN the write shall be refused. | `docs/architecture.md`, Configuration &amp; project data |
| `core-reconnects-not-relaunches` | WHEN Core restarts, Core shall reconnect to every pillar already running rather than relaunching it. | `docs/design.md`, "Core's own recovery" |
| `policy-one-to-one-test-script` | A policy shall be linked to exactly one test script, and a test script shall enforce exactly one policy. | `docs/pillars/policy_compliance.md` |
| `no-multi-tenant-auth` | The system shall not implement a multi-tenant user/role/permission model. | `docs/architecture.md`, Technical decisions |

## Gaps — no existing source covers these

Identified by their absence, not mined from a document. Draft form only;
these need more thought before they're even EARS candidates, not just
before they're approved:

- **Log retention.** Every tool that writes run logs (`little-runner`'s
  `~/.local/state/little-runner/logs/`, and little-workshop's own planned
  `~/.local/state/little-workshop/logs/<pillar>/`) has no stated retention
  or rotation policy anywhere in this ecosystem.
- **Generated-artifact sandboxing.** A policy's test script is Claude-CLI-
  generated code that then runs against a real project checkout. Nothing
  in `policy_compliance.md` yet constrains what that generated script is
  *allowed* to do (network access, writes outside its worktree) — this is
  a safety gap worth resolving before real implementation, not just a
  documentation gap.
- **Crash-loop bounds.** Core's design mentions a crash counter and
  backoff conceptually; no policy states a maximum retry count or backoff
  shape.
- **Health-probe timeout.** Nothing states how long Core waits for a
  pillar's health response before treating it as unhealthy.
- **Project/data retention on removal.** No stated policy for what happens
  to a project's requirements, jobs, and project files when it's removed
  from the registry.
- **Pillar Interface deprecation window.** Versioned paths (`/v1`, `/v2`)
  are designed; nothing states how long an old version must keep working
  before it can be dropped.

## Open questions for you

1. **Framework tier, or a strong all-projects user-policy default?** Every
   entry above is drafted as a framework-tier candidate (immutable,
   authored via the "little-workshop itself" tab). Some of these — Bazel
   as the canonical build system is the clearest example — are ecosystem
   convention today but not obviously something that should be
   *permanently un-overridable* the way "no secrets stored" should be.
   Should this catalog distinguish "genuinely non-negotiable" from "strong
   default, override if a real project needs to," or does everything
   mined from an existing root policy document stay framework-tier by
   default?
2. **`disk-encryption-not-db-encryption`** is the one entry drawn from a
   single project's specific reasoning (`little-mindcheck`) rather than
   repeated convention. Worth promoting alongside the others, or holding
   back until it's actually been chosen twice independently, the way
   `tailscale-only-exposure` was?
3. **The generated-test-script sandboxing gap** — worth treating as its
   own dedicated design round before any policy authoring happens for
   real, given it's a safety question, not just a missing document?
4. Anything in this catalog that's **flat-out wrong** for where this
   ecosystem is heading rather than just unranked — easiest to catch now,
   before any of it goes through EARS refinement.
