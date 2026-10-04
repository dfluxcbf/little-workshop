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
