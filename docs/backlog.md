# Backlog

Features asked for and not yet refined or scheduled. An item here is recorded as it was asked for;
refining it (requirements, design, a milestone) moves it out of this file into `docs/milestones.md`,
`docs/design.md` and `docs/requirements.md`.

## Bypassing policies (added 2026-10-04, not refined)

Any policy can be bypassed by user (not Claude). User may be able to:

* Disable policy gating temporarily by running `sudo lws bypass_policies true --time [time_in_minutes]`.
   * Must be super user.
   * Must not be Claude.
   * This sets a flag internally for a period of time.
      * Maximum 30 minutes.
      * Default 2 minutes.
* While `bypass_policies` is true:
   * Policy checks performed by user are executed, but do not gate when failing.
      * Policy violations still show on the dashboard.
   * Dashboard warns that "bypass_policies" flag is TRUE.
      * Displays the remaining time.
      * Give button to instantly return flag to FALSE.
* Policies are only bypassed by CLI executed by user in the terminal while the flag is TRUE.
* Policies are never bypassed by commands originated from Claude, even if the flag is TRUE.
* Policies are never bypassed by commands originated from automation: schedule jobs ("right away", "once, at a date and time", "every week") , jobs triggered through the UI. 

* Policies must keep track of the origination of the request.
* Only user is allowed to bypass policies, restricted to CLI. They still get informed about policy violations. Policy violations still reflect on dashboard's statistics.

## Claude usage: what can be worked on now (added 2026-10-04, not refined)

Proposed after the first findings and automation candidates of the Usage & Efficiency pillar
(`docs/pillars/usage.md`). Items 1 and 2 were taken up the same day; the rest wait.

1. **Fix the mining's branching check.** Almost every top candidate scored "hybrid 4/5" because
   `bounded_branching` judged whether the next tool call was predictable, which it never is in an
   interactive session. It should ask whether the call ends in a few known outcomes (succeeded, failed,
   refused). As it was, redeploying pillars one at a time (242 calls in 12 sessions) ranked as judgment
   work. *Taken up 2026-10-04.*
2. **Record the replacements already built** on their candidates, so "tokens avoided" is tracked and
   detector D10 shows whether Claude takes the new routes: single-pillar redeploy ->
   `workshop_deployment_redeploy_changed_pillars` and its trigger; `sleep` while waiting -> `workshop_job_wait`;
   `workshop_requirements_create` -> `workshop_requirements_sync_markers`. *Taken up 2026-10-04.*
3. **Teach the `little-projects` root workspace the new routes.** The root is the second-largest spender,
   and its `.claude/AGENTS.md` is hand-written with none of the generated lifecycle routes: root sessions made
   185 single-pillar redeploy calls in two weeks and do not know `workshop_job_wait`, the changed-pillars
   redeploy or the marker sync. A short edit, from a root session.
4. **Run "Optimize Claude usage" on ethereal-lands.** Its top finding is about $66 a week in list-price
   terms, mostly long sessions (90% of context tokens in requests over 200k), plus polling on Unity test runs
   (about $11 a week). The job starts an agent task on a branch for review, measured again two weeks later.
   It spends tokens: the owner's call.
5. **Shorten sessions.** Long sessions are the largest cost in every project. Claude Code has
   `--autocompact <tokens>` (100k to 1M) to compact earlier, trading some conversation detail for much
   smaller requests; whether it can be set in `settings.json` instead of per launch is not checked. The
   statusline's `ctx %` and `/clear` between unrelated tasks do much of the same. A habit or setting change:
   the owner's call.

