---
paths:
  - "docs/**"
  - "shared_project_standards/**"
---
# Documentation

- No duplication between `docs/design.md` (concepts), `docs/architecture.md` (implementation) and
  `docs/milestones.md` (status rows); link to the one that owns a topic. Milestone status says what is actually
  built.
- The requirements register mirrors the Requirements pillar: `docs/requirements.md` is its index and conventions,
  `docs/requirements/<pillar>.md` its rows. Read only the part you need. Create a requirement in the pillar
  (`workshop_requirements_create`, the next free `REQ-nnnnnnnnn` id) and add its row to the part of the pillar
  its statement names.
- `shared_project_standards/` compiles rules from their sources and originates none; where a source and the
  folder disagree, the source wins. It is maintained by the "Update Standards Documentation" job; a hand edit
  is dated in the file's "Last reviewed" line. `little_workshop_standards/README.md` is imported into every
  Claude session here, so it stays short.
- A rule change is deliberate, documented in the file itself at the point of change, and dated
  (`docs/governance_policy.md`'s evolution principle).
