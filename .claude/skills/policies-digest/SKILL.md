---
name: policies-digest
description: Lists the little-workshop policies that cover this project - each policy's rule in one sentence and the actions it gates (commit, branch finish, release, publish, deploy). Use when you need to know what the policies require or which rule a change must satisfy, instead of reading the policy documents.
---

# The policies that cover `little-workshop`

Run it from the repository root:

```
python3 .claude/skills/policies-digest/scripts/digest.py --project-id little-workshop            # every policy attached to the project
python3 .claude/skills/policies-digest/scripts/digest.py --project-id little-workshop --gate commit
```

It prints one line per policy: its name, the actions it gates and its rule, read live from
little-workshop, so it is never out of date. To check the project against them, use the `policy-preflight`
skill; to see the last recorded results, `workshop_policy_results`.
