# Requirements: lws CLI

Part of little-workshop's requirements register; conventions and the other parts are in [../requirements.md](../requirements.md). Sections keep the order and headings of the round that added them.

## M17 — ecosystem cutover begins: `little-mindcheck` as pilot (2026-09-17)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000187 | **`little-mindcheck` registered as M17's pilot project** — a real `project.import` against its real, already-checked-out repository (no scaffolding, no mutation of the project's own files: this milestone's own text is explicit that the old tools stay installed and untouched alongside `lws`). Verified immediately afterward with a real `version.branch_audit` against the real project, reported clean. | done |

## Pre-commit version sync in the CLI (2026-09-24)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000264 | **Pre-commit version sync completes before the commit.** Superseded: the pre-commit version sync is retired (REQ-000000486). | superseded by REQ-000000486 |

## Windows-hosted projects reach little-workshop (2026-10-25)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000463 | **lws runs from a Windows-hosted project's own code.** When the user runs `lws windows install-shim` in WSL, the CLI shall write `lws.cmd`, and beside it an sh `lws` for Git for Windows hooks, into a Windows folder (default %LOCALAPPDATA%\little-workshop\bin) and add that folder to the Windows user PATH unless told not to, so that Windows code running `lws.cmd <command>` (or `lws <command>` in Git Bash) runs WSL's `lws` with the same arguments, the caller's folder as its working directory, GIT_INDEX_FILE, GIT_DIR, GIT_WORK_TREE and LWS_FROM_WINDOWS carried across through WSLENV, and `lws`'s exit code as its own; and when `lws` was started by that shim, it shall translate every Windows path argument (`C:\...`, `--option=C:\...`, `\\wsl.localhost\<distro>\...`) to its WSL path and, for a command taking `--project-id` given none, use the registered project whose checkout holds the working directory. | draft |
| REQ-000000464 | **Gateway tokens belong to one project and are stored only as hashes.** When the user creates a gateway token for a project (`lws api token-create --project-id <p>`), the CLI shall generate a random token, show it once (or write it to a given file with mode 0600), and keep only its SHA-256 with the project, a label and the creation time in `~/.config/little-workshop/api_tokens.json` (mode 0600); `lws api token-list` shall list a project's tokens without the tokens themselves, `lws api token-revoke` shall remove one by id, and a presented token shall resolve to its project only while it is stored. Creating and revoking a token always prompt in Claude sessions. | draft |

## Versioning without churn (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000486 | **The pre-commit version sync is retired.** The lws CLI shall not write VERSION from a git hook: `lws version sync-version-files` shall change nothing and say that the branch finish writes VERSION, and `lws version install-hooks` shall remove the .githooks/pre-commit that little-workshop installed to run it. | draft |

## Windows-drive projects as an explicit host (2026-11-07)

| ID | Description | Status |
| --- | --- | --- |
| REQ-000000514 | **lws windows doctor checks what Windows-hosted projects depend on.** When the user runs `lws windows doctor`, the CLI shall report, each with a hint and changing nothing: whether `lws.cmd` is on the Windows PATH, whether it and the sh `lws` beside it forward GIT_INDEX_FILE, GIT_DIR, GIT_WORK_TREE and LWS_FROM_WINDOWS, whether a Windows Python runs, whether each Windows-hosted project's checkout has `core.autocrlf=false`, and whether `%USERPROFILE%\.wslconfig` sets mirrored networking; and it shall exit non-zero when a check fails. | draft |
