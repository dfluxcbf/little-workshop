Execute the milestone the task refers to (by ID in `docs/milestones.md`, or as described above):

1. Read `docs/design.md`, `docs/architecture.md` and the milestone row; if the row is missing, add it first with the scope described.
2. Make sure each requirement the milestone lists exists in the project's requirements register; create the missing ones with real IDs.
3. Implement the code under `src/` following `docs/architecture.md`; update `design.md`/`architecture.md` where decisions change.
4. Write automated tests under `tests/` tagged with the requirement IDs; run the project's test target until green.
5. Mark the milestone row's status accurately (done only if every definition-of-done item holds).
