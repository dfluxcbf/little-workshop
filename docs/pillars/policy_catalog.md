# Policy catalog — tier decided, individually unauthored

**Status.** Tier is decided: every entry below is a **framework** policy
candidate, not a strong default users could override — confirmed 2026-09-15.
None is an *active* policy yet — each still has to individually clear all
four gates in `policy_compliance.md`'s authoring pipeline (EARS-compliant,
mechanically enforceable, non-conflicting, and full human approval of both
the EARS text and the generated script) before it becomes a real
`policies` row. This document is the input to that pipeline, not a record
of having run it. This catalog's own open items are tracked centrally in
[`../open_questions.md`](../open_questions.md), not inline.

**Enforceability was applied retroactively to this whole catalog**, not
just the one entry it was first caught on (see "Considered and rejected"
below) — Gate 2 didn't exist when this catalog was first drafted, so every
entry has been re-read against it. A few needed narrowing to their
actually-checkable core rather than outright rejection; those are marked
inline.

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

## Considered and rejected: not mechanically enforceable

Gate 2 of the authoring pipeline (`policy_compliance.md`) didn't exist
when this catalog was first drafted. Re-reading it against "if we cannot
enforce a policy, we shall not define it" removed these entirely, rather
than narrowing them the way a few others were:

- **Data-at-rest encryption posture** (`little-mindcheck` architecture.md's
  "rely on host full-disk encryption rather than database-level
  encryption"). Nothing running on the host can meaningfully verify disk
  encryption state as a property *of the project*, and the choice not to
  implement database-level encryption is an absence, not a behavior — there
  is no artifact a test script can point at. Stays exactly what it already
  is: a documented, reasoned decision in one project's own
  `docs/architecture.md`, never a policy.
- **"Documentation shall not contain fabricated content"**
  (`documentation_policy.md`'s "when content doesn't exist yet" clause;
  independently stated in `bootstrap-little-project`'s own design.md).
  Judging whether prose is fabricated is exactly the kind of call a test
  script cannot make. What *is* enforceable from this same source survives
  as `doc-no-verbatim-duplication` below — the honesty requirement itself
  stays a review-time convention, not a policy.

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
| `doc-no-verbatim-duplication` | IF an identical text block appears in two documentation files, THEN it shall be flagged for correction to one authoritative source with a link from the other. | `documentation_policy.md` — narrowed from "the same content" (semantic, unenforceable — judging two differently-worded passages as "the same idea" is a human call) to verbatim/near-verbatim text matching, which a diff can actually verify. |
| `milestone-requirements-linked-and-passing` | WHEN a milestone is marked done, the requirements it implements shall be linked and their tests shall pass. | `documentation_policy.md`; `requirements_policy.md` — narrowed from the original's full definition-of-done (which also named "documentation updated" and "implementation complete," neither independently machine-checkable) to the two sub-clauses a test script can actually verify. |
| `milestone-own-branch` | Each milestone shall be worked on its own short-lived branch. | `documentation_policy.md` |
| `open-questions-document-exists` | The project shall contain a `docs/open_questions.md` file with an Open table and a Resolved table. | `docs/open_questions.md`'s own "As a policy" section — the enforceable half of that document's tracking convention; the other half ("Claude CLI documented every question it surfaced") isn't independently machine-checkable and stays a convention, not a policy, for the same reason `undefined-purpose-honesty` was dropped rather than narrowed. |

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
| `no-secret-pattern-in-commit` | IF a file staged for commit matches a known credential/token pattern, THEN the commit shall be refused. | `automation_policy.md` rule 11 — narrowed from "shall not store or log a credential" (unbounded, unenforceable — nothing can verify the *absence* of every possible secret) to a bounded pattern-scan, the same shape real secret-scanning tools use, and checkable in principle. |

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
| `no-redundant-app-auth` | WHERE a project declares no app-level login route, the project shall not gain one without also declaring itself reachable outside the system's private network. | `little-meals` architecture.md — reframed as a checkable structural pair (login route presence vs. declared exposure) rather than the original's "shall not require," which can't be verified without deciding what "requires" means for arbitrary code. |
| `no-third-party-cdn` | WHERE a project serves a web frontend, the project shall vendor its JS/CSS/font dependencies rather than loading them from a third-party CDN at runtime. | `little-meals` architecture.md (htmx, fonts) |

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

Identified by their absence, not mined from a document — originally
tracked inline here; now tracked as `OQ-4` through `OQ-8` in
[`../open_questions.md`](../open_questions.md), this project's single
open-item tracker, so a gap has exactly one place to be marked resolved
rather than two that can drift apart. Generated-artifact sandboxing, the
one gap this section used to list, was resolved 2026-09-15 — see
`policy_compliance.md`'s "Test-script execution sandbox" and
`open_questions.md`'s Resolved log.

## Resolved 2026-09-15

1. **Tier**: framework, for everything in this catalog — no
   strong-default/user-tier split. Settled, not revisited per-entry.
2. **Data-at-rest encryption**: dropped entirely, not promoted — see
   "Considered and rejected" above. Generalized into Gate 2
   (mechanical enforceability) as a standing admissibility rule for every
   future policy, not just this one.
3. **User-policy lifecycle and scope**: users may create, edit, or remove
   a user-level policy at any time; edits re-run all three gates rather
   than being a lighter-weight path; a user policy may never target or
   gate Policy & Compliance itself (framework policies may, and are
   expected to). See `policy_compliance.md`'s "Authoring a policy," now
   restructured around three gates instead of the original single
   EARS-check step.
4. **Framework-policy lifecycle**: "never overridable" and "never
   revised" are different claims — only the first is true. Framework
   policies are hard rules while in force, but little-workshop's own
   maintainers can edit or remove one, treated as continuous development
   of little-workshop itself (deliberate, documented, dated — the same
   discipline `docs/governance_policy.md` already requires generally) and
   not a casual runtime toggle. See `policy_compliance.md`'s new
   "Framework-policy lifecycle" section.
5. **Timeout is not a policy verdict.** The 30-minute ceiling is a
   framework design decision ("ensure scripts are not stuck"), not
   something Policy & Compliance's rule system enforces — so a timeout
   never enters the ten-minute pending_confirmation window, which exists
   specifically for a human to knowingly bypass a *determined* violation.
   A timeout has no determined violation to bypass; it fails immediately,
   distinctly flagged as something to go fix. See `policy_compliance.md`'s
   resolved "Action-gating checks" outcome tree.
6. **Allowlist review folded into Gate 4, not a separate step.** Policy
   creation/editing now requires full user approval of both the EARS text
   and the generated test script, every time, no exception — the file
   allowlist is read directly off the script already being reviewed
   rather than declared and approved separately. See
   `policy_compliance.md`'s "Authoring a policy," now four gates.

## Open questions for you

Tracked as `OQ-1` and `OQ-2` in [`../open_questions.md`](../open_questions.md)
— this catalog's own open items now live there rather than in a
second list here.
