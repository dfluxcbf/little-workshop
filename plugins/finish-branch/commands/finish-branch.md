---
description: Finish the checked-out feature, improvement or hotfix branch through little-workshop
argument-hint: "[project|--all] [--push]"
---

The plugin's UserPromptSubmit hook already ran `scripts/finish_branch.py` for this command and answered without
the model. If you are reading this, the script handed something over: do only what its "needs Claude" lines say
(the facts are in the context above), through the little-workshop routes (`workshop_*` tools or `lws`; plain
`git` for a push or a branch deletion), and then run `/finish-branch $ARGUMENTS` again. A held finish whose
failing policy this branch did not cause is confirmed (`workshop_policy_confirm`); one it caused is fixed first.
End with a few lines: what finished, what is held or failed and why.
