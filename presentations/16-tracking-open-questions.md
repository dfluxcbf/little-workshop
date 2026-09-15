← [Who Owns a Project?](15-project-ownership.md) · deck 16 of 16 · back to [index](README.md)

# Tracking Open Questions

A gap in the process, not the architecture: by deck 15, open questions
and design gaps existed scattered across five different files, each with
its own "Left open" or "Gaps" section — and one of them (the
"informed immediately" notification channel, from deck 9) had quietly
fallen out of every later summary without ever actually being resolved.
The tracker this deck introduces exists specifically because that's the
kind of thing a single central document catches and a handful of
footnotes doesn't. Resolved 2026-09-15.

## One document, two tables

[`docs/open_questions.md`](../docs/open_questions.md) is now the single
place every open item lives: a stable `OQ-NNN` ID, what it is, when it was
raised, and its source. When it's settled, it moves to a Resolved table —
terse, one line, pointing at the deck that carries the actual reasoning
(this document indexes; it never duplicates). Every scattered "Left open"
and "Gaps" section across `architecture.md`, `policy_compliance.md`, and
`policy_catalog.md` now points here instead of restating its own copy —
the same "don't duplicate content across files" discipline this project
already applies everywhere else, turned on its own open-question tracking
for the first time.

Nine open items surfaced this way, immediately — including `OQ-9`, the
one that had gone missing.

---

## A policy split cleanly in two, again

"Keep this document, and record what Claude CLI surfaces" doesn't survive
as one policy, for the same reason encryption posture and "no fabricated
documentation" didn't:

- **`open-questions-document-exists`** — the file exists, with the right
  shape. Checkable. A real framework-policy candidate now, in the
  catalog's Documentation domain.
- **"Claude CLI documented every question it raised"** — not checkable
  from repository state. Nothing can verify a *conversation* surfaced a
  question and it went unrecorded. Stays a stated convention on the
  tracker's own page, followed by discipline, not by an automated check.

This is the fourth time this exact split has happened in this project's
own history (data-at-rest encryption, fabricated-content, and now this) —
worth naming as a pattern in its own right: when a rule bundles something
checkable with something that requires judging intent or completeness
from the outside, it splits. The checkable half becomes a policy. The
other half stays exactly what it already was — true, useful, and
enforced by a person, not a script.

---

Back to [index](README.md) · or revisit [9 — Policy & Compliance](09-policy-and-compliance.md)
