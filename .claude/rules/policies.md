---
paths:
  - "src/policy/**"
  - "src/orchestration/policy_gate.py"
  - "tools/policy_scripts/**"
  - "tools/sync_policies.py"
  - "docs/pillars/policy_*.md"
---
# Policies

A policy is one EARS rule plus one pytest script, run in the Policy & Compliance sandbox (Docker, no network,
the checkout mounted read-only) at its gate points; `docs/pillars/policy_compliance.md` is the specification.
Policies are the hard layer of every project's Claude instructions: a rule that must hold on every commit gates
`commit` (the commit-msg hook runs it and refuses the commit), one that must hold before a finish, release or
publish gates that action, and a rule about what a session may do is the generated guard
(`src/registry/claude_templates.py`). A project's briefing says what enforces a rule, never restates it.

- Every policy is code in this repository: `tools/policy_scripts/<topic>.py` and its entry in
  `tools/policy_scripts/policies.json` (EARS text, target pillar, scope, gate points). Change both, then
  `python3 tools/sync_policies.py` shows the difference from the live pillar and `--apply` replaces it there
  (it keeps each policy's project selection). Applying stands in for Gate 4, so it waits for the user's review.
- The script contract, which the authoring prompt in `src/policy/gates.py` also states:
  - `PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")`, never another way to find the checkout, so the
    same script runs in the sandbox, from the commit-msg hook and from the `policy-preflight` skill.
  - A policy gating `commit` judges the pending commit when `POLICY_COMMIT_DIR` is set (files `message`,
    `files`, `VERSION`; its parent is HEAD), and HEAD against its parent otherwise.
  - `pytest.skip()` for not applicable; every assertion message says what is wrong and how to fix it, since
    that message is all a person or Claude sees when the policy fails.
  - Judge the newest commit or tag only, so an old mistake never fails a policy forever.
- A policy that matches source text instead of state fails on the text that forbids the thing it looks for;
  check history, files or configuration instead. The same goes for this repository's tests: build a pattern a
  policy scans for at run time (`"@pytest.mark." + "requirement"`) rather than writing it out.
- List the project's files with `git ls-files --cached --others --exclude-standard` (keeping only those that
  exist), never a walk of the checkout: ignored folders are not the project's, and a Unity project's `Library/`
  holds tens of thousands of files that make a walk slow and its matches false.
- A policy scanning content judges only what the commit introduces (compare with the parent's version), and
  gives a test's fake value a way out on its own line (`no-committed-secrets` honours `pragma: allowlist secret`).
- After changing a policy, run every policy against every project it applies to (`workshop_policy_check_all`
  per policy) and read the details of each failure.
- `policy.evaluate` (`lws policy evaluate`, `workshop_policy_evaluate`) runs policies without recording or
  holding anything; `policy.check_project` is what records results and gates Jobs.
