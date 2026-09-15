Fix the bug described above:

1. Reproduce it first — write a failing automated test under `tests/` that captures the reported behaviour (tag it with the existing requirement ID the behaviour belongs to, if one exists; otherwise leave it untagged and say so).
2. Find the root cause; fix it in `src/` with the smallest change that is correct, not the smallest change that hides the symptom.
3. Run the whole test suite; keep it green.
4. If the fix changes documented behaviour, update `docs/architecture.md` (and `design.md` if a decision changed).
5. In the summary, state the root cause in one or two sentences.
