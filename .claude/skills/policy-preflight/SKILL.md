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

It evaluates the policies in little-workshop's sandbox (`lws policy evaluate`: the checks the gate runs;
nothing is held, and a verdict on committed, unchanged files is recorded so the gate reuses it). If the
installed `lws` or the running Policy pillar predates that, it runs the same policy scripts here and says
so. Exit 0: nothing blocks; 1: a gating policy fails; 2: the policies could not be evaluated. At
`release`, the policy that runs the test suite (python-test-coverage) takes minutes.

## Acting on the result

Each failure names the policy, its rule and what to change. Fix the cause in the project, commit, and run
the preflight again until nothing blocks.

A policy is the project's agreed rule, so change the project, not the policy. A failure this work did not
cause (it is already on `dev`, or sits in toolkit-installed files) does not stop the work: finish anyway and
confirm the hold (`workshop_policy_confirm`), naming the policy in the report. The steps around a finish are
in the `workshop-lifecycle` skill.

<!-- project -->
<!-- /project -->
