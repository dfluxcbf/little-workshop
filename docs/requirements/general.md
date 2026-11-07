# Requirements: Cross-cutting

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## System health, automatic bug tasks, coverage policy, data catalog (2026-09-22)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000226 | **Marked code never becomes an incident.** Where code is wrapped in the incident reporter's suppression marker (`no_auto_incident()`), an exception raised there shall never be recorded as an incident. | done |

## Unity project template (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000277 | **Unity test runner emits a JUnit report.** When tools/run-unity-tests is invoked with --junit-xml=PATH, the script shall run the project's tests in the Windows Unity Editor in batch mode (EditMode by default, PlayMode on request) and write a JUnit-XML report converted from Unity's NUnit3 result; when Unity writes no result, the script shall exit non-zero without a report. | done |

## Policies as the hard layer of Claude instructions (2026-10-02)

| ID | Description | Status |
| --- | --- | --- |
| — | **Deny rules stop at a word.** The generated .claude/settings.json shall deny each raw lifecycle command with a pattern that ends at a word (git merge *), so a longer command such as git merge-base is not denied, and tools/sync_claude_files.py shall remove the patterns earlier versions wrote. | draft |

## Stuck jobs, force stop and incidents (2026-10-04)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000367 | **Background crashes become incidents.** If an exception escapes a background thread of any pillar process, then the pillar shall record an incident naming the pillar, the thread, the exception and its traceback, as it does for a request's exception. | draft |

## Requirement conflicts, found by code and resolved by a Claude task (2026-10-30)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000470 | **An unregistered project raises nothing.** While a project is not registered in little-workshop, the workshop shall raise no attention item, status entry, incident or requirement conflict for it. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000510 | **A Windows-hosted project's git is git.exe.** While a project's host is `windows`, the system shall run the project's git commands in the Registry & Portfolio, Deployment & Servers, Agentic Development and Version Control & Release pillars with Windows' `git.exe` (with `-C` and the checkout's Windows path, and path arguments as Windows paths), and shall keep WSL's git for a worktree of it that lies outside the Windows drive. | draft |
| REQ-000000529 | **Pillars find Windows programs without a Windows PATH.** If a pillar's PATH does not find a Windows program the pillar runs (git.exe or powershell.exe or tasklist.exe or taskkill.exe or cmd.exe), then the system shall run that program by its absolute path under the C: drive's System32, WindowsPowerShell or Git folders. | done |
