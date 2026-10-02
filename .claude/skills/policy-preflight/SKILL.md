---
name: policy-preflight
description: Runs the little-workshop policies that would gate an action on this project (a branch finish by default; also release, package_publish, server_launch, deploy_redeploy) against this checkout, and reports each failure with how to fix it. Use before workshop_version_*_finish, a release or a publish, and when a commit or a gated job was refused by a policy.
---

# Policy preflight

little-workshop checks this project's policies when it is asked to finish a branch, release, deploy or
publish, and holds the action in `pending_confirmation` if one fails. Running the same checks first lets
you fix the failures while the change is still in front of you.

Run it from the repository root:

```
python3 .claude/skills/policy-preflight/scripts/preflight.py                      # branch_finish
python3 .claude/skills/policy-preflight/scripts/preflight.py --gate-point release
```

It evaluates the policies in little-workshop's sandbox (`lws policy evaluate`: the checks the gate runs,
nothing recorded or held). If the installed `lws` or the running Policy pillar predates that, it runs the
same policy scripts here and says so. Exit 0: nothing blocks; 1: a gating policy fails; 2: the policies
could not be evaluated. A policy that runs the test suite (python-test-coverage) takes minutes.

## Acting on the result

Each failure names the policy, its rule and what to change. Fix the cause in the project, commit (the
commit-msg hook re-checks the commit-level policies), and run the preflight again until nothing blocks.

A policy is the project's agreed rule, so change the project, not the policy, and leave a held action for
the user to confirm. If a failure looks wrong, or cannot be fixed in this project, report it with the
policy's output and let the user decide.

## Finishing a branch

1. Run the project's Definition-of-Done test suite and record the run with `workshop_requirements_run_tests`.
2. Run this preflight and fix what blocks.
3. Finish with `workshop_version_<type>_finish`. If it lands in `pending_confirmation`, report which policy
   held it (`workshop_policy_results`).
