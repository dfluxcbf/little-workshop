# Ethereal Lands documentation rules

Source: `CLAUDE.md` in the project, plus [../documentation_standard.md](../documentation_standard.md).

## Layout

- `docs/design.md`: overview and the full index of design documents.
- `docs/design/NN-*.md`: one topic per file, read in number order (01 theme and style through
  17 simulation).
- `docs/game/`: game documentation (`assumptions.md`, `README.md`).
- `docs/machination/`: ELM planning and reference.
- `docs/architecture.md` and `docs/milestones.md`: currently short; these are the shared-standard
  documents most in need of completion.
- `docs/playthrough_testing/` is mentioned in some task descriptions but does not exist.

## Rules

1. After adding, renaming or retitling a document or section, run
   `tools/update_design_index.py` to regenerate indexes and navigation.
2. **Never edit** blocks between `<!-- name -->` and `<!-- /name -->` markers, or
   `docs/design/README.md`, by hand.
3. Open questions live in `docs/design/16-open-items.md`, each with a short anchor (`#q-01`,
   `#o-01`). This replaces the shared `open_questions.md` / `OQ-NNN` scheme for design questions.
4. In every document except the ELM references themselves, cite a reference look-up-table item
   (`R-05` in `docs/machination/references.md`), never the repository. When you had to read a
   reference repository directly, add an item.
5. Reference repositories are cloned into `.references/` (git-ignored). Use them for mechanics only;
   do not copy their code.
6. Decision ids (`D-01`, `D-02`, ...) in the Machination plan are cited, not restated.

## Tasks that say "leave X untouched"

Honour it exactly. Typical protected areas for documentation tasks: `docs/design/`, `docs/game/`.
The sub-project `docs/machination/` should be treated as protected unless the task names it.

Last reviewed: 2026-10-01.
