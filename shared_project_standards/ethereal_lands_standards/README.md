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
| `CLAUDE.md` | Editor, design-doc, ELM and "Never" rules; test and per-clone setup |
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

None. ethereal-lands is attached to no Policy & Compliance policy
([../policy_register.md](../policy_register.md)); there are no project-scoped policies for it yet.
It does keep the conventions the shared policies check: a `VERSION`, a `.lvx/config.json`
(`master`/`dev`, `version_hooks` empty, no `.githooks`), and merge commits of the
`feat (-1): <title>` form. It has no Bazel build, `src/` tree or Python suite, so the coverage and
requirement-marker policies would skip it. Attaching `version-format`, `commit-cites-requirement`
and `ssh-remotes-no-embedded-credentials` is a decision for the owner.

Last reviewed: 2026-10-01.
