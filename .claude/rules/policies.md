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
  check history, files or configuration instead.
- After changing a policy, run every policy against every project it applies to (`workshop_policy_check_all`
  per policy) and read the details of each failure.
- `policy.evaluate` (`lws policy evaluate`, `workshop_policy_evaluate`) runs policies without recording or
  holding anything; `policy.check_project` is what records results and gates Jobs.
