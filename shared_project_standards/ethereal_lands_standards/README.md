# Ethereal Lands standards

A Unity game (working title **Sorcery Village**) for Windows. Registered as `ethereal-lands`
(2026-09-24), status active, no `repo_url` in the registry (its git `origin` is
`git@github.com:dfluxcbf/ethereal-lands.git`). Branch `dev`, `VERSION` 0.1.14 on 2026-10-01.

**The checkout lives on the Windows drive**: `C:\little-projects\ethereal-lands`, which is
`/mnt/c/little-projects/ethereal-lands` from WSL. Work on it there, not in a WSL copy; little-workshop
manages it from WSL.

## Governing documents in the project

| File | Role |
|---|---|
| `AGENTS.md` | The briefing every session loads: commands, the rules that hold everywhere, branches, what else enforces the rules; imports `docs/HANDOFF.md` and little-workshop's shared section `.claude/little-workshop.md` (no `CLAUDE.md`, `CLAUDE.local.md` or `AGENT.md`) |
| `.claude/rules/*.md` | Path-scoped guidance (C#, engine-free core, Unity assets, game, ELM model and format, tests, docs, tools, viewer, instruction files; `claude-instructions.md` is generated) that loads when a matching file is read |
| `.claude/hooks/edit_guard.py`, `.claude/hooks/lws_scope_guard.py`, `.githooks/` | Refuse hand edits of Unity-owned and generated files; refuse raw lifecycle commands and other projects (generated); run the commit-time policies |
| `.claude/skills/` | `verify-changes` (the checks covering a change), `session-retro`; `policy-preflight` and `workshop-lifecycle` (generated) |
| `docs/design.md`, `docs/design/NN-*.md` | Game design (overview plus one topic per file, numbered) |
| `docs/design/README.md` | Generated index; never hand-edited |
| `docs/design/16-open-items.md` | Open questions with `#q-NN` and `#o-NN` anchors |
| `docs/game/` | Game documentation and assumptions |
| `docs/machination/` | Ethereal Lands Machination (ELM) plan, architecture, model, glossary, references |
| `docs/architecture.md`, `docs/milestones.md` | Project architecture and milestones (short; incomplete) |

## Applicable shared standards

All of [../](../README.md) applies, with the adaptations in [documentation.md](documentation.md) and
[unity_and_windows.md](unity_and_windows.md).

## Policies

Attached 2026-10-02, gating on: every all-projects policy (the version and commit rules,
`commit-cites-requirement`, `merge-commit-message-format`, the release-tag rules, the hook, Claude
configuration, LFS and `.meta` checks; the Python-only ones skip it), plus its project policy
`design-index-and-schema-current`. `back-merge-no-ff` and `hotfix-propagates-by-merge` stay little-workshop's.
See [../policy_register.md](../policy_register.md). The commit-time ones run from `.githooks/commit-msg`; the
project's `AGENTS.md` names what enforces each rule rather than restating it.

Last reviewed: 2026-10-02 (briefing at the root `AGENTS.md`, rules in `.claude/rules/`, policies attached;
hand edit, same day: the briefing imports little-workshop's shared section, generated rule and skill added).
