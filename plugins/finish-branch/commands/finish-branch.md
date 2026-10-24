---
description: Finish the checked-out feature, improvement or hotfix branch through little-workshop
argument-hint: "[project|--all] [--push]"
---

The plugin's UserPromptSubmit hook already ran `scripts/finish_branch.py` for this command and answered without
the model. If you are reading this, the script handed something over: do only what its "needs Claude" lines say
(the facts are in the context above), through the little-workshop routes (`workshop_*` tools or `lws`, never raw
git for a merge, tag or push), and then tell the user to run `/finish-branch $ARGUMENTS` again. Never confirm a
held finish.
