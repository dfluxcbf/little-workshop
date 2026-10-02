"""The `.claude/` files `project.bootstrap` scaffolds into every new project, and
that every registered project keeps: a `settings.json` that routes lifecycle
commands through little-workshop (its MCP server `little-workshop`, or `lws`)
instead of raw git/systemd/twine and binds every `lws` command to the project's
own id, the PreToolUse hook that keeps a project's session from naming another
project, and the `AGENTS.md` that tells Claude about both.

The same table is checked in as little-workshop's own `.claude/settings.json`
(held equal by a unit test); the `little-projects` super-repository, which runs
the sessions that manage every project, allows all of it instead."""

from __future__ import annotations

import json
import re

MCP = "mcp__little-workshop__"
SETTINGS_PATH = ".claude/settings.json"
GUARD_PATH = ".claude/hooks/lws_scope_guard.py"
AGENTS_PATH = ".claude/AGENTS.md"

# Why the instructions file is `.claude/AGENTS.md` and a project has no `CLAUDE.md` (REQ-000000294).
WHY_AGENTS_MD = (
    "This file is `.claude/AGENTS.md` on purpose: Claude Code reads `AGENTS.md` (also as `.claude/AGENTS.md`) only\n"
    "where a project has no `CLAUDE.md`, so never add a `CLAUDE.md` here, or this file stops being loaded."
)

# A project id ends up inside permission rules and a shell command: keep it to what the
# Registry accepts as a name, so it can never carry a glob, quote or shell metacharacter.
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# --- MCP tools (`mcp__little-workshop__<name>`), every tool exactly once ---------------------

# Reads. Pre-allowed in any project's session.
MCP_READ_ONLY = (
    "workshop_core_status",
    "workshop_project_list", "workshop_project_get",
    "workshop_job_list", "workshop_job_status",
    "workshop_version_status", "workshop_version_config", "workshop_version_issue_list",
    "workshop_version_branch_audit", "workshop_version_release_candidate_list",
    "workshop_requirements_list", "workshop_requirements_get", "workshop_requirements_report",
    "workshop_requirements_suite_list",
    "workshop_policy_list", "workshop_policy_get", "workshop_policy_results", "workshop_policy_winning",
    "workshop_policy_gating_for_job", "workshop_policy_projects_list", "workshop_policy_catalog_list",
    "workshop_policy_draft_list", "workshop_policy_check_project",
    "workshop_deployment_status", "workshop_deployment_history", "workshop_deployment_branches",
    "workshop_deployment_confirmation_status",
    "workshop_build_run_list", "workshop_build_run_status", "workshop_build_package_list",
    "workshop_agent_runs", "workshop_agent_run_status", "workshop_agent_ci_list",
    "workshop_agent_ci_status", "workshop_agent_session_status",
    "workshop_schedule_list", "workshop_schedule_status", "workshop_schedule_queue_get",
)

# Routine, reversible day-to-day work on the project (each takes the project's `project_id`).
MCP_ROUTINE = (
    "workshop_version_feature_start", "workshop_version_improvement_start",
    "workshop_version_hotfix_start", "workshop_version_support_start",
    "workshop_version_release_candidate_cut",
    "workshop_version_issue_set", "workshop_version_issue_unset",
    "workshop_requirements_create", "workshop_requirements_update",
    "workshop_requirements_suite_create", "workshop_requirements_use_case_create",
    "workshop_requirements_milestone_create", "workshop_requirements_link_suite",
    "workshop_requirements_unlink_suite", "workshop_requirements_result_record",
    "workshop_requirements_import_junit", "workshop_requirements_run_tests",
    "workshop_build_run",
)

# Policy-gated lifecycle actions on the project. A failing policy holds them in
# `pending_confirmation`, so the gate, not a prompt, is the safety.
MCP_GATED = (
    "workshop_version_feature_finish", "workshop_version_improvement_finish",
    "workshop_version_hotfix_finish", "workshop_version_release_start",
    "workshop_version_release_finish", "workshop_version_release_candidate_promote",
    "workshop_version_support_finish", "workshop_version_stabilize",
    "workshop_deployment_deploy", "workshop_deployment_stop", "workshop_deployment_confirm",
    "workshop_build_publish_pypi", "workshop_build_publish_vsix", "workshop_build_package_delete",
)

# Stand in for a human decision, run unattended agents, change what a policy covers, register or
# remove projects or are hard to undo: always a prompt.
MCP_ASK = (
    "workshop_policy_confirm", "workshop_policy_projects_set",
    "workshop_agent_run_task", "workshop_agent_finish_task", "workshop_agent_skip_task",
    "workshop_agent_ci_check", "workshop_agent_session_start", "workshop_agent_session_stop",
    "workshop_project_bootstrap", "workshop_project_import", "workshop_project_connect_github",
    "workshop_project_remove",
    "workshop_version_init", "workshop_version_config_add_hook", "workshop_version_config_remove_hook",
    "workshop_deployment_set_manifest", "workshop_deployment_seed_confirmation",
    "workshop_requirements_publish", "workshop_requirements_delete", "workshop_requirements_suite_delete",
    "workshop_job_stop", "workshop_schedule_add", "workshop_schedule_remove", "workshop_schedule_trigger",
    "workshop_schedule_cancel", "workshop_schedule_postpone", "workshop_schedule_queue_set",
)

# Administration of little-workshop itself (policies, its own pillars): they name no project, so
# no project's session may pre-allow them. Only little-workshop's own settings do (`platform=True`).
MCP_PLATFORM = (
    "workshop_policy_check_all", "workshop_policy_author", "workshop_policy_draft_import",
    "workshop_policy_catalog_run", "workshop_policy_remove", "workshop_policy_activate",
    "workshop_policy_configure", "workshop_deployment_redeploy_pillar",
)

# Tools whose `project_id` is optional: left out, they act on every project, so the guard
# insists on it. (A test keeps this equal to the live tool catalog.)
MCP_NEEDS_PROJECT_ID = (
    "workshop_build_package_list", "workshop_build_run_list", "workshop_policy_projects_list",
    "workshop_policy_results", "workshop_requirements_list", "workshop_requirements_suite_list",
    "workshop_schedule_add",
)

# Tools that name a project in `name` (not `project_id`). The guard holds `name` to the project.
MCP_NAMED_PROJECT = (
    "workshop_project_bootstrap", "workshop_project_connect_github", "workshop_project_get",
    "workshop_project_import", "workshop_project_remove",
)

# --- `lws` commands, the same split. A path is what follows `lws`. ---------------------------

# No project argument: a read by id or of the whole system, pre-allowed as it stands.
LWS_READ_ONLY_ANY = (
    "core status", "project list", "job list", "job status", "job queue show",
    "schedule list", "schedule status", "schedule queue get",
    "policy list", "policy status", "policy catalog list", "policy draft list",
    "requirements status", "requirements suite status", "build run-status", "agent session-list",
)

# From here to LWS_GATED each command takes `--project-id`, and the rule pre-allowing it carries
# the project's own id: `lws version feature-finish --project-id <this project> ...`.
LWS_READ_ONLY = (
    "version status", "version config-list", "version issue-list", "version release-candidate-list",
    "version branch-audit",
    "requirements list", "requirements report", "requirements suite list",
    "requirements use-case list", "requirements milestone list", "requirements result list",
    "policy winning", "policy check-project", "policy gating-for-job", "policy results",
    "policy projects list",
    "deployment status", "deployment history", "deployment confirmation", "deployment branches",
    "build run-list", "build package-list",
    "agent runs", "agent run-status", "agent ci-list", "agent ci-status",
)

LWS_ROUTINE = (
    "version feature-start", "version improvement-start", "version hotfix-start", "version support-start",
    "version release-candidate-cut", "version issue-set", "version issue-unset",
    "version sync-version-files",
    "requirements create", "requirements update", "requirements suite create",
    "requirements use-case create", "requirements milestone create", "requirements link",
    "requirements unlink", "requirements result record", "requirements import-junit",
    "requirements run-tests",
    "build run",
)

LWS_GATED = (
    "version feature-finish", "version improvement-finish", "version hotfix-finish",
    "version release-start", "version release-finish", "version release-candidate-promote",
    "version support-finish", "version stabilize",
    "deployment deploy", "deployment stop", "deployment confirm",
    "build publish-pypi", "build publish-vsix", "build package-delete",
)

# Always a prompt, whichever project they name.
LWS_ASK = (
    "job stop", "job policy-confirm",
    "schedule add", "schedule edit", "schedule remove", "schedule trigger", "schedule cancel",
    "schedule postpone", "schedule queue set",
    "project bootstrap", "project connect-github", "project import", "project remove",
    "version init", "version config-add-hook", "version config-remove-hook",
    "requirements publish", "requirements delete", "requirements suite delete", "requirements import",
    "policy projects set",
    "deployment set-manifest", "deployment seed-confirmation",
    "agent run-task", "agent finish-task", "agent skip-task", "agent ci-check",
    "agent session-start", "agent session-stop",
)

# Administration of little-workshop itself, as MCP_PLATFORM.
LWS_PLATFORM = (
    "policy author", "policy draft import", "policy activate", "policy configure", "policy remove",
    "policy check-all", "policy catalog run", "deployment redeploy-pillar",
)

# Left to the default prompt on purpose: plumbing the pillars call themselves, host
# configuration, and commands that start a server or change what is installed.
LWS_UNLISTED = (
    "job create", "job policy-confirm-timeout", "job queue release",
    "requirements export", "requirements test-structure-create",
    "build print-pip-config", "build write-pip-config", "build pip", "build print-vsix-urls",
    "deployment rollback-timeout", "deployment redeploy-pillar-worker", "deployment rollback-pillar-worker",
    "worktree provision", "worktree release", "mcp serve",
)

# Raw command -> the little-workshop route the policies gate it through.
DENY_REROUTED = (
    ("git merge*", "workshop_version_*_finish (branch_finish gate, --no-ff merges)"),
    ("git cherry-pick*", "workshop_version_hotfix_finish (hotfix-propagates-by-merge)"),
    ("git tag*", "workshop_version_release_* (annotated v<M>.<m>.<p> tags)"),
    ("git push*", "workshop_project_connect_github / the agent flow"),
    ("git branch -d*", "workshop_version_*_finish deletes finished branches"),
    ("git branch -D*", "workshop_version_*_finish deletes finished branches"),
    ("git reset --hard*", "destructive"),
    ("git clean*", "destructive"),
    ("git commit*--no-verify*", "the pre-commit hook syncs VERSION"),
    ("systemd-run*", "workshop_deployment_*"),
    ("systemctl --user start*", "workshop_deployment_deploy"),
    ("systemctl --user stop*", "workshop_deployment_stop"),
    ("systemctl --user restart*", "workshop_deployment_deploy / redeploy_pillar"),
    ("systemctl --user kill*", "workshop_deployment_stop"),
    ("twine*", "workshop_build_publish_pypi (wheel-filename-has-version)"),
    ("pipx install*", "workshop_deployment_* / bazel run //:install"),
    ("gh repo create*", "workshop_project_connect_github"),
    ("gh pr create*", "the agent flow opens pull requests"),
    ("gh pr merge*", "workshop_version_*_finish"),
    ("gh release*", "workshop_version_release_finish"),
)

READ_ONLY_BASH = (
    "git status*", "git diff*", "git log*", "git show*", "git blame*", "git ls-files*",
    "git rev-parse*", "git branch --show-current*", "git branch --list*",
    "systemctl --user status*",
    "gh pr view*", "gh pr list*", "gh pr checks*", "gh issue view*", "gh issue list*",
)

# Local commits are the normal flow; the pre-commit hook and the policies judge them.
COMMIT_BASH = ("git add*", "git commit*")

# What a session runs to test the project: the template's own runner. "bazel" is for a registered
# project that is neither Python nor Unity (a TypeScript or C++ project built with Bazel).
TEST_BASH = {
    "python": ("python3 -m pytest*", "bazel build*", "bazel test*", "bazel query*"),
    "bazel": ("bazel build*", "bazel test*", "bazel query*"),
    "unity": ("tools/run-unity-tests*",),
}


def is_project_id(name: str) -> bool:
    return bool(_PROJECT_ID.fullmatch(name or ""))


def _checked_id(project_id: str) -> str:
    if not is_project_id(project_id):
        raise ValueError(f"not a usable project id: {project_id!r}")
    return project_id


def guard_hooks(project_id: str) -> dict:
    """The `hooks` block registering the scope guard for Bash and every little-workshop MCP tool."""
    return {
        "PreToolUse": [{
            "matcher": f"Bash|{MCP}.*",
            "hooks": [{
                "type": "command",
                "command": f'python3 "${{CLAUDE_PROJECT_DIR}}"/{GUARD_PATH} {_checked_id(project_id)}',
                "timeout": 10,
            }],
        }],
    }


def settings(project_id: str, template: str = "python", *, platform: bool = False,
             extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = ()) -> dict:
    """The `.claude/settings.json` of project `project_id`.

    Every `lws` command that takes `--project-id` is pre-allowed only with this project's id; the
    guard hook denies a call that names another. `platform=True` (little-workshop itself) also
    pre-allows the tools that administer little-workshop rather than a project."""
    project = _checked_id(project_id)
    if template not in TEST_BASH:
        raise ValueError(f"unknown template {template!r}, expected one of {', '.join(TEST_BASH)}")
    bash = (*READ_ONLY_BASH, *COMMIT_BASH, *TEST_BASH[template], *extra_allow)
    allow = [f"Bash({c})" for c in bash]
    allow += [f"Bash(lws {c} *)" for c in LWS_READ_ONLY_ANY]
    allow += [f"Bash(lws {c} --project-id {project} *)" for c in (*LWS_READ_ONLY, *LWS_ROUTINE, *LWS_GATED)]
    allow += [f"{MCP}{t}" for t in (*MCP_READ_ONLY, *MCP_ROUTINE, *MCP_GATED)]
    ask = [f"{MCP}{t}" for t in MCP_ASK]
    ask += [f"Bash(lws {c} *)" for c in LWS_ASK]
    ask += [f"Bash({c})" for c in extra_ask]
    admin = [f"{MCP}{t}" for t in MCP_PLATFORM] + [f"Bash(lws {c} *)" for c in LWS_PLATFORM]
    (allow if platform else ask).extend(admin)
    return {
        "permissions": {
            "allow": allow,
            "ask": ask,
            "deny": [f"Bash({c})" for c, _ in DENY_REROUTED],
        },
        "hooks": guard_hooks(project),
    }


def settings_json(project_id: str, template: str = "python", *, platform: bool = False,
                  extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = ()) -> str:
    return json.dumps(settings(project_id, template, platform=platform, extra_allow=extra_allow,
                               extra_ask=extra_ask), indent=2) + "\n"


# The guard hook (REQ-000000297). A real, standalone script copied into each project: the
# permission rules cannot match an MCP tool's arguments (a settings rule naming an `mcp__` tool
# with parentheses is skipped), and a Bash rule is only as good as the argument order it assumes.
_GUARD_SOURCE = r'''#!/usr/bin/env python3
"""PreToolUse hook: keep this project's Claude sessions inside this project.

Registered in .claude/settings.json for Bash and the little-workshop MCP tools.
Usage: lws_scope_guard.py PROJECT_ID, with the hook's JSON on stdin.

Denies an `lws` command or a little-workshop MCP call that names another project
(--project-id, project_id, a project name), or that leaves the project id out
where leaving it out acts on every project. Input it cannot read passes: the
permission rules in settings.json are the other gate. Generated by little-workshop
(registry/claude_templates.py): change the generator, not this copy."""

import json
import re
import shlex
import sys

PREFIX = "mcp__little-workshop__"
# MCP tools whose project_id is optional: left out, they act on every project.
NEEDS_PROJECT_ID = frozenset((
@@NEEDS_PROJECT_ID@@
))
# MCP tools that take the project's own name in `name`.
NAMED_PROJECT = frozenset((
@@NAMED_PROJECT@@
))
# `lws project <command>`: these create or register another project.
FOREIGN_PROJECT_COMMANDS = ("bootstrap", "import")
# `lws project <command> NAME`: these act on the project NAME.
NAMED_PROJECT_COMMANDS = ("connect-github", "remove")
SHELLS = ("bash", "sh", "zsh", "dash")
OPERATORS = set("();<>|&`")
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def without_heredoc_bodies(command):
    """The command line minus the text of its here-documents: that is data, not commands."""
    kept, delimiters = [], []
    for line in command.split("\n"):
        if delimiters:
            if line.strip() == delimiters[0]:
                delimiters.pop(0)
            continue
        kept.append(line)
        delimiters.extend(match.group(2) for match in HEREDOC.finditer(line))
    return "\n".join(kept)


def simple_commands(command):
    """The command line split into simple commands (lists of words)."""
    command = without_heredoc_bodies(command)
    try:
        lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars="".join(sorted(OPERATORS)))
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    commands, current = [], []
    for word in words:
        if set(word) <= OPERATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(word)
    if current:
        commands.append(current)
    return commands


def lws_violation(args, own):
    """Why these `lws` arguments leave the project, or None."""
    for i, arg in enumerate(args):
        option, equals, value = arg.partition("=")
        if len(option) >= 4 and ("--project-id".startswith(option) or option.startswith("--project")):
            if not equals:
                value = args[i + 1] if i + 1 < len(args) else None
            if value is not None and value != own:
                return f"names project {value!r}"
    if args[:1] == ["project"] and len(args) > 1:
        if args[1] in FOREIGN_PROJECT_COMMANDS:
            return f"`lws project {args[1]}` registers another project"
        if args[1] in NAMED_PROJECT_COMMANDS and len(args) > 2 and not args[2].startswith("-") and args[2] != own:
            return f"names project {args[2]!r}"
    return None


def bash_violation(command, own):
    for words in simple_commands(command):
        for i, word in enumerate(words):
            base = word.rsplit("/", 1)[-1]
            if base == "lws":
                problem = lws_violation(words[i + 1:], own)
                if problem:
                    return f"the lws command {problem}"
                break
            if base in SHELLS:
                for j, option in enumerate(words[i + 1:], i + 1):
                    if option.startswith("-") and not option.startswith("--") and "c" in option[1:]:
                        if j + 1 < len(words):
                            problem = bash_violation(words[j + 1], own)
                            if problem:
                                return problem
                        break
                break
    return None


def mcp_violation(tool, args, own):
    name = tool[len(PREFIX):]
    project = args.get("project_id")
    if project is not None and project != own:
        return f"{name} names project {project!r}"
    if project is None and name in NEEDS_PROJECT_ID:
        return f"{name} without project_id acts on every project"
    if name in NAMED_PROJECT and args.get("name") != own:
        return f"{name} names project {args.get('name')!r}"
    for entry in args.get("projects") or ():
        other = entry.get("project_id") if isinstance(entry, dict) else None
        if other is not None and other != own:
            return f"{name} names project {other!r}"
    return None


def main():
    own = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
        tool = event["tool_name"]
        args = event.get("tool_input") or {}
    except (ValueError, KeyError, TypeError):
        return
    if not own:
        return
    if tool.startswith(PREFIX):
        problem = mcp_violation(tool, args, own)
    elif tool == "Bash":
        problem = bash_violation(str(args.get("command", "")), own)
    else:
        return
    if problem:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"little-workshop scope: this checkout is project {own!r} but {problem}. "
                "Work on other projects belongs in a session started from the little-projects root."
            ),
        }}))


if __name__ == "__main__":
    main()
'''


def scope_guard_py() -> str:
    """The text of `.claude/hooks/lws_scope_guard.py`."""
    def items(names: tuple[str, ...]) -> str:
        return "\n".join(f'    "{n}",' for n in names)

    return (_GUARD_SOURCE
            .replace("@@NEEDS_PROJECT_ID@@", items(MCP_NEEDS_PROJECT_ID))
            .replace("@@NAMED_PROJECT@@", items(MCP_NAMED_PROJECT)))


def claude_files(project_id: str, template: str = "python", *, platform: bool = False,
                 extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = ()) -> dict[str, str]:
    """Path (relative to the project root) -> text of the mechanical files under `.claude/`: the
    settings and the guard. The `AGENTS.md` is separate (`agents_md`): a registered project keeps
    its own."""
    return {
        SETTINGS_PATH: settings_json(project_id, template, platform=platform, extra_allow=extra_allow,
                                     extra_ask=extra_ask),
        GUARD_PATH: scope_guard_py(),
    }


def lifecycle_md(name: str) -> str:
    """The sections every project's `.claude/AGENTS.md` shares: routing and scope (REQ-000000294)."""
    return f"""## Lifecycle goes through little-workshop

This project is registered in little-workshop as `{name}`. Policies gate branch finishes, releases, deploys
and publishes, so those actions are made through the `little-workshop` MCP server (tools `workshop_<pillar>_*`,
registered at user scope as `lws mcp serve`) or the `lws` CLI, never through raw git, systemd or twine.
`.claude/settings.json` denies the raw forms.

| To do this | Use |
|---|---|
| Start / finish a feature, improvement, hotfix | `workshop_version_<type>_start` / `_finish` (never `git merge`) |
| Cut or finish a release, tag | `workshop_version_release_*`, `workshop_version_release_candidate_*` |
| Deploy, stop or inspect a server | `workshop_deployment_*` |
| Publish a package | `workshop_build_publish_*` |
| Record requirements and test runs | `workshop_requirements_*` |
| Check policies before finishing | `workshop_policy_check_project`, `workshop_policy_gating_for_job` |
| Push / create the GitHub repository | `workshop_project_connect_github` |

Gated actions (finish, release, deploy, publish) are pre-allowed because the policy gate is the check: a failure
holds the action in `pending_confirmation`; report it, never work around it. Decisions that stand in for a
human (`workshop_policy_confirm`, `workshop_agent_finish_task`/`skip_task`, scheduling, project registration)
prompt.

## Scope: this project only

This session belongs to `{name}`. Name it in every call: `project_id` `{name}` for a tool, `--project-id {name}`
for `lws`. `.claude/settings.json` pre-allows only those forms, and the hook `.claude/hooks/lws_scope_guard.py`
denies a call that names another project or leaves the id out where that would mean every project. Registering
or removing projects, authoring policies, scheduling and other cross-project work belong in a session started
from `little-projects`, which allows all of little-workshop.
"""


def agents_md(name: str) -> str:
    """`.claude/AGENTS.md` for the python layout (REQ-000000294); the unity layout has its own."""
    return f"""# {name}

Managed by little-workshop. Read `docs/design.md` and `docs/architecture.md` first.

{WHY_AGENTS_MD}

{lifecycle_md(name)}
## Rules

- Every commit raises `VERSION` (use `tools/sync_versions.py`-style propagation where the project has one).
- Commits cite exactly one of `REQ-nnnnnnnnn` or `REQ-NONE`; subjects read `feat|impr|fix (<issue or -1>): <title>`.
- Behaviour changes ship with tests tagged with requirement ids. Unit tests are network-free and never skip.
- Run tests with `python3 -m pytest tests/unit`.
"""
