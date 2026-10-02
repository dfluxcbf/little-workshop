"""The `.claude/` files `project.bootstrap` scaffolds into every new project, and
that every registered project keeps: a `settings.json` that routes lifecycle
commands through little-workshop (its MCP server `little-workshop`, or `lws`)
instead of raw git/systemd/twine and binds every `lws` command to the project's
own id, the PreToolUse hook that keeps a project's session from naming another
project, the `policy-preflight` skill that runs the policies a gated action
would hit before asking for it, and the `AGENTS.md` briefing that tells Claude
about all of them.

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
PREFLIGHT_SKILL_PATH = ".claude/skills/policy-preflight/SKILL.md"
PREFLIGHT_SCRIPT_PATH = ".claude/skills/policy-preflight/scripts/preflight.py"

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
    "workshop_policy_draft_list", "workshop_policy_check_project", "workshop_policy_evaluate",
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
    "policy winning", "policy check-project", "policy gating-for-job", "policy results", "policy evaluate",
    "policy projects list",
    "deployment status", "deployment history", "deployment confirmation", "deployment branches",
    "build run-list", "build package-list",
    "agent runs", "agent run-status", "agent ci-list", "agent ci-status",
)

LWS_ROUTINE = (
    "version feature-start", "version improvement-start", "version hotfix-start", "version support-start",
    "version release-candidate-cut", "version issue-set", "version issue-unset",
    "version sync-version-files", "version install-hooks",
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
    ("git commit*--no-verify*", "the pre-commit (VERSION sync) and commit-msg (commit-time policies) hooks"),
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

# Local commits are the normal flow; the pre-commit hook and the commit-msg policy hook judge them.
COMMIT_BASH = ("git add*", "git commit*")

# The policy-preflight skill's script: it only evaluates policies, records and holds nothing.
SKILL_BASH = (f"python3 {PREFLIGHT_SCRIPT_PATH}*",)

# What a session runs to test the project: the template's own runner. "bazel" is for a registered
# project that is neither Python nor Unity (a TypeScript or C++ project built with Bazel).
TEST_BASH = {
    "python": ("python3 -m pytest*", "bazel build*", "bazel test*", "bazel query*"),
    "bazel": ("bazel build*", "bazel test*", "bazel query*"),
    "unity": ("tools/run-unity-tests*",),
}


# little-workshop's own Claude Code settings: the scaffolded table plus what only this repository needs
# (REQ-000000295). Its checked-in .claude/ files are claude_files("little-workshop", "python",
# **OWN_SETTINGS); a unit test keeps them equal. The live-install guard denies running
# tests/integration beside the installed pillars (REQ-000000304).
LIVE_INSTALL_GUARD_PATH = ".claude/hooks/live_install_guard.py"
OWN_SETTINGS = dict(
    platform=True,
    extra_allow=("python3 tools/sync_versions.py*", "tools/check_system_deps.sh*", "python3 tools/sync_policies.py*"),
    extra_ask=("bazel run //:install*", "bazel run //*_server*", "python3 tools/sync_policies.py --apply*"),
    extra_hooks=({
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": f'python3 "${{CLAUDE_PROJECT_DIR}}"/{LIVE_INSTALL_GUARD_PATH}',
            "timeout": 10,
        }],
    },),
)


def is_project_id(name: str) -> bool:
    return bool(_PROJECT_ID.fullmatch(name or ""))


def _checked_id(project_id: str) -> str:
    if not is_project_id(project_id):
        raise ValueError(f"not a usable project id: {project_id!r}")
    return project_id


def guard_hooks(project_id: str, extra_pre_tool_use: tuple[dict, ...] = ()) -> dict:
    """The `hooks` block registering the scope guard for Bash and every little-workshop MCP tool,
    then any project-specific PreToolUse entries (little-workshop's own live-install guard)."""
    return {
        "PreToolUse": [{
            "matcher": f"Bash|{MCP}.*",
            "hooks": [{
                "type": "command",
                "command": f'python3 "${{CLAUDE_PROJECT_DIR}}"/{GUARD_PATH} {_checked_id(project_id)}',
                "timeout": 10,
            }],
        }, *extra_pre_tool_use],
    }


def settings(project_id: str, template: str = "python", *, platform: bool = False,
             extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
             extra_hooks: tuple[dict, ...] = ()) -> dict:
    """The `.claude/settings.json` of project `project_id`.

    Every `lws` command that takes `--project-id` is pre-allowed only with this project's id; the
    guard hook denies a call that names another. `platform=True` (little-workshop itself) also
    pre-allows the tools that administer little-workshop rather than a project. `extra_hooks` are
    PreToolUse entries added after the scope guard."""
    project = _checked_id(project_id)
    if template not in TEST_BASH:
        raise ValueError(f"unknown template {template!r}, expected one of {', '.join(TEST_BASH)}")
    bash = (*READ_ONLY_BASH, *COMMIT_BASH, *TEST_BASH[template], *SKILL_BASH, *extra_allow)
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
        "hooks": guard_hooks(project, extra_hooks),
    }


def settings_json(project_id: str, template: str = "python", *, platform: bool = False,
                  extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
                  extra_hooks: tuple[dict, ...] = ()) -> str:
    return json.dumps(settings(project_id, template, platform=platform, extra_allow=extra_allow,
                               extra_ask=extra_ask, extra_hooks=extra_hooks), indent=2) + "\n"


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


# The policy-preflight skill (REQ-000000301): the same two files in every project. The skill tells
# Claude when to run it and how to act on the result; the script does the work.
_PREFLIGHT_SKILL = """---
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
"""

_PREFLIGHT_SOURCE = r'''#!/usr/bin/env python3
"""Run the little-workshop policies that would gate an action on this project, against this
checkout, and print every failure with its reason. The `policy-preflight` skill's script.

    python3 .claude/skills/policy-preflight/scripts/preflight.py [--gate-point branch_finish]

Evaluates in little-workshop's policy sandbox (`lws policy evaluate`: the checks the gate itself
runs, nothing recorded or held). When the installed lws or the running Policy pillar predates
that, it runs the same policy scripts here, outside the sandbox, and says so.
Exit 0: nothing blocks. 1: a gating policy fails. 2: the policies could not be evaluated.
Generated by little-workshop (registry/claude_templates.py): change the generator, not this copy."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

GATE_POINTS = ("branch_finish", "release", "package_publish", "build_install", "server_launch",
               "deploy_redeploy", "server_stop", "project_removal")
_SUMMARY = re.compile(r"(\d+) (failed|passed|skipped)")


def checkout_root():
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else Path.cwd()


def project_id(root):
    """The project's little-workshop id: the scope guard's argument in .claude/settings.json,
    else the checkout's directory name (the ids are directory names)."""
    try:
        settings = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        for entry in (settings.get("hooks") or {}).get("PreToolUse") or []:
            for hook in entry.get("hooks") or []:
                match = re.search(r"lws_scope_guard\.py\s+(\S+)", hook.get("command", ""))
                if match:
                    return match.group(1)
    except (OSError, ValueError):
        pass
    return root.name


def evaluate_in_sandbox(project, gate_point, root):
    """(result, None), or (None, why it could not run here) -- None reason means: fall back."""
    try:
        run = subprocess.run(
            ["lws", "policy", "evaluate", "--project-id", project, "--gate-point", gate_point,
             "--checkout", str(root), "--json"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None, "lws is not installed"
    if run.returncode in (0, 1) and run.stdout.strip():
        return json.loads(run.stdout), None
    error = (run.stderr or run.stdout).strip()
    if "invalid choice" in error or "predates policy evaluate" in error:
        return None, None  # an lws or a Policy pillar from before `policy evaluate`
    return None, error or f"lws policy evaluate exited {run.returncode}"


def evaluate_locally(project, gate_point, root):
    """The same policies, fetched from little-workshop, run here with pytest outside the sandbox."""
    listed = subprocess.run(
        ["lws", "policy", "gating-for-job", "--project-id", project, "--categories", gate_point],
        capture_output=True, text=True,
    )
    if listed.returncode != 0:
        raise RuntimeError((listed.stderr or listed.stdout).strip() or "lws policy gating-for-job failed")
    results = []
    env = {**os.environ, "POLICY_PROJECT_DIR": str(root), "COLUMNS": "300", "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory(prefix="policy-preflight-") as scratch:
        for policy in json.loads(listed.stdout).get("policies", []):
            script = Path(scratch) / "test_policy.py"
            script.write_text(policy["test_script"], encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "pytest", str(script), "-q", "-rA", "--tb=short", "--no-header",
                 "-p", "no:cacheprovider"],
                capture_output=True, text=True, env=env, cwd=scratch,
            )
            counts = {kind: int(n) for n, kind in _SUMMARY.findall(run.stdout.strip().splitlines()[-1] if run.stdout.strip() else "")}
            outcome = ("fail" if counts.get("failed") or run.returncode not in (0, 1) else
                       "pass" if counts.get("passed") else "not_applicable" if counts.get("skipped") else "error")
            reasons = [line[2:].strip() if line.startswith("E ") else line.strip()
                       for line in run.stdout.splitlines() if line.startswith(("E ", "SKIPPED "))]
            results.append({"topic": policy["topic"], "ears_text": policy["ears_text"], "gating": policy.get("gating", True),
                            "outcome": outcome, "detail": "\n".join(dict.fromkeys(reasons)) or run.stdout[-800:]})
    return {
        "project_id": project, "gate_point": gate_point, "results": results, "local": True,
        "blocking": [r["topic"] for r in results if r["outcome"] == "fail" and r["gating"]],
        "errors": [r["topic"] for r in results if r["outcome"] == "error"],
    }


def report(result):
    counts = {}
    for entry in result["results"]:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    labels = (("fail", "failed"), ("error", "could not run"), ("pass", "passed"), ("not_applicable", "not applicable"))
    summary = ", ".join(f"{counts[k]} {label}" for k, label in labels if counts.get(k)) or "no policy gates it"
    where = " (run here, outside the sandbox)" if result.get("local") else ""
    lines = [f"Policies gating {result['gate_point']} for {result['project_id']}{where}: {summary}"]
    for entry in result["results"]:
        if entry["outcome"] in ("fail", "error"):
            gating = "" if entry.get("gating", True) else " (gating off here: reported, never blocks)"
            lines.append(f"\n{'FAIL' if entry['outcome'] == 'fail' else 'ERROR'} {entry['topic']}{gating}")
            lines.append(f"  rule: {entry.get('ears_text', '')}")
            lines.extend(f"  {line}" for line in str(entry.get("detail", "")).splitlines())
    if result.get("blocking"):
        lines.append(f"\nWould hold the action: {', '.join(result['blocking'])}.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the policies that would gate an action, against this checkout.")
    parser.add_argument("--gate-point", default="branch_finish", choices=GATE_POINTS)
    parser.add_argument("--project-id", default=None, help="Default: read from the checkout")
    parser.add_argument("--local", action="store_true", help="Run the policy scripts here instead of in the sandbox")
    args = parser.parse_args(argv)
    root = checkout_root()
    project = args.project_id or project_id(root)

    result = None
    if not args.local:
        result, problem = evaluate_in_sandbox(project, args.gate_point, root)
        if problem:
            print(f"error: the policies could not be evaluated: {problem}", file=sys.stderr)
            return 2
    if result is None:
        try:
            result = evaluate_locally(project, args.gate_point, root)
        except (RuntimeError, ValueError) as exc:
            print(f"error: the policies could not be evaluated: {exc}", file=sys.stderr)
            return 2
    print(report(result))
    if result.get("blocking"):
        return 1
    return 2 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def preflight_skill_md() -> str:
    """The text of `.claude/skills/policy-preflight/SKILL.md`."""
    return _PREFLIGHT_SKILL


def preflight_py() -> str:
    """The text of `.claude/skills/policy-preflight/scripts/preflight.py`."""
    return _PREFLIGHT_SOURCE


def claude_files(project_id: str, template: str = "python", *, platform: bool = False,
                 extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
                 extra_hooks: tuple[dict, ...] = ()) -> dict[str, str]:
    """Path (relative to the project root) -> text of the mechanical files under `.claude/`: the
    settings, the guard and the policy-preflight skill. The `AGENTS.md` is separate (`agents_md`):
    a registered project keeps its own."""
    return {
        SETTINGS_PATH: settings_json(project_id, template, platform=platform, extra_allow=extra_allow,
                                     extra_ask=extra_ask, extra_hooks=extra_hooks),
        GUARD_PATH: scope_guard_py(),
        PREFLIGHT_SKILL_PATH: preflight_skill_md(),
        PREFLIGHT_SCRIPT_PATH: preflight_py(),
    }


def lifecycle_md(name: str) -> str:
    """The sections every project's `.claude/AGENTS.md` shares: routing, what enforces the rules, and
    scope (REQ-000000294, REQ-000000305)."""
    return f"""## Lifecycle goes through little-workshop

This project is registered in little-workshop as `{name}`. Branch finishes, releases, deploys and publishes go
through the `little-workshop` MCP server (tools `workshop_<pillar>_*`, registered at user scope as `lws mcp serve`)
or the `lws` CLI, because that is where the project's policies are checked. `.claude/settings.json` denies the raw
git, systemd and twine forms.

| To do this | Use |
|---|---|
| Start / finish a feature, improvement, hotfix | `workshop_version_<type>_start` / `_finish` (never `git merge`) |
| Cut or finish a release, tag | `workshop_version_release_*`, `workshop_version_release_candidate_*` |
| Deploy, stop or inspect a server | `workshop_deployment_*` |
| Publish a package | `workshop_build_publish_*` |
| Record requirements and test runs | `workshop_requirements_*` |
| See what a gated action would hit, and why | the `policy-preflight` skill (`workshop_policy_evaluate`) |
| Push / create the GitHub repository | `workshop_project_connect_github` |

## What enforces the rules

The project's rules are Policy & Compliance policies, checked by code rather than remembered:

- On every commit, `.githooks/commit-msg` runs the policies that gate `commit` (VERSION bumps and format,
  requirement citations) and refuses a commit that breaks one, printing the rule and how to fix it. Fix it and
  commit again (`--no-verify` is denied). On `feature/` and `improvement/` branches `.githooks/pre-commit` writes
  `VERSION` for you.
- Before a gated action (finish, release, deploy, publish) little-workshop runs the policies gating it, and a
  failure holds the action in `pending_confirmation`. Run the `policy-preflight` skill first and fix what it
  reports; if an action is still held, report it rather than working around it.
- Decisions that stand in for a human (`workshop_policy_confirm`, `workshop_agent_finish_task`/`skip_task`,
  scheduling, project registration) always prompt.

## Scope: this project only

This session belongs to `{name}`. Name it in every call: `project_id` `{name}` for a tool, `--project-id {name}`
for `lws`. `.claude/settings.json` pre-allows only those forms, and the hook `.claude/hooks/lws_scope_guard.py`
denies a call that names another project or leaves the id out where that would mean every project. Registering
or removing projects, authoring policies, scheduling and other cross-project work belong in a session started
from `little-projects`, which allows all of little-workshop.
"""


# Kept out of Claude's context (Claude Code strips block-level HTML comments), for whoever edits the file.
BRIEFING_NOTE = """<!-- A briefing, loaded into every session: keep it short and about this project. Guidance for one
part of the tree goes in .claude/rules/ with `paths:` frontmatter, procedures in .claude/skills/, and a rule
that must hold every time in a hook or a little-workshop policy, not in a sentence here. -->"""


def agents_md(name: str) -> str:
    """`.claude/AGENTS.md` for the python layout (REQ-000000294); the unity layout has its own."""
    return f"""# {name}

{BRIEFING_NOTE}

Managed by little-workshop. Read `docs/design.md` (what and why) and `docs/architecture.md` (how) before changing
code.

{WHY_AGENTS_MD}

{lifecycle_md(name)}
## Working here

- Tests: `python3 -m pytest tests/unit` (the Definition-of-Done suite: network-free, never skipping).
- Every commit raises `VERSION`, and one that changes `src/` cites `REQ-nnnnnnnnn` or `REQ-NONE`; the commit-msg
  hook checks both. Subjects read `feat|impr|fix (<issue or -1>): <title>`.
- Behaviour changes ship with tests tagged with the requirement ids they cover.
"""
