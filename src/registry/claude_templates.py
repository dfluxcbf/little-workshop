"""The `.claude/` files `project.bootstrap` scaffolds into every new project, and
that every registered project keeps (`tools/sync_claude_files.py` rewrites them):

- `settings.json`: routes lifecycle commands through little-workshop (its MCP
  server `little-workshop`, or `lws`) instead of raw git/systemd/twine, and binds
  every `lws` command to the project's own id;
- the PreToolUse guard hook: the hard layer of those instructions. It refuses a
  call that names another project, a raw command that would bypass the policy
  gates (saying the route instead), and a hand edit of what little-workshop
  generates or of a `CLAUDE.md`;
- `little-workshop.md`: the short briefing section every project shares, which
  the project's own `AGENTS.md` imports (`@little-workshop.md`);
- the `workshop-lifecycle` skill (the routes and the steps before a finish) and
  the `policy-preflight` skill (runs the policies a gated action would hit),
  loaded on demand instead of in every session;
- the path-scoped rule `claude-instructions.md`, which says where an instruction
  belongs and loads only when Claude reads an instruction file.

The same files are checked in as little-workshop's own (held equal by a unit
test); the `little-projects` super-repository, which runs the sessions that
manage every project, allows all of it instead."""

from __future__ import annotations

import json
import re

# MCP tools are classified in common/tool_access.py, which the MCP pillar's HTTP gateway reads too; the names are
# re-exported here for the rest of this module and its tests.
from common.tool_access import (  # noqa: F401
    MCP_ASK, MCP_GATED, MCP_NAMED_PROJECT, MCP_NEEDS_PROJECT_ID, MCP_PLATFORM, MCP_READ_ONLY, MCP_ROUTINE,
)

MCP = "mcp__little-workshop__"
SETTINGS_PATH = ".claude/settings.json"
GUARD_PATH = ".claude/hooks/lws_scope_guard.py"
AGENTS_PATH = ".claude/AGENTS.md"
PREFLIGHT_SKILL_PATH = ".claude/skills/policy-preflight/SKILL.md"
PREFLIGHT_SCRIPT_PATH = ".claude/skills/policy-preflight/scripts/preflight.py"
LIFECYCLE_SKILL_PATH = ".claude/skills/workshop-lifecycle/SKILL.md"
POLICIES_SKILL_PATH = ".claude/skills/policies-digest/SKILL.md"
POLICIES_SCRIPT_PATH = ".claude/skills/policies-digest/scripts/digest.py"
TEST_FILTER_PATH = ".claude/hooks/test_output_filter.py"
# A Windows-hosted project's hooks run through this dispatcher (REQ-000000513).
HOOK_RUNNER_PATH = ".claude/hooks/run_hook.sh"
# Generated once, now retired: the sync removes the file and its settings entry (REQ-000000478).
REMINDER_PATH = ".claude/hooks/newest_request_reminder.py"
RETIRED_FILES = (REMINDER_PATH,)
INSTRUCTIONS_RULE_PATH = ".claude/rules/claude-instructions.md"
# The project's own rule for what a session learned (claude-reflect's rules and the like): never generated, always
# loaded (no `paths`), and where the guard sends a refused CLAUDE.md write (REQ-000000498).
LEARNED_RULES_PATH = ".claude/rules/learned.md"
# A generated Markdown file ends with this block; what the project writes between the two markers is its own: the
# sync keeps it and the guard lets Claude edit it (REQ-000000497). Claude Code strips the comments.
PROJECT_SECTION_START = "<!-- project -->"
PROJECT_SECTION_END = "<!-- /project -->"
PROJECT_SECTION = f"{PROJECT_SECTION_START}\n{PROJECT_SECTION_END}\n"
GOVERNANCE_LEVELS = ("light", "full")
# Imported by the project's briefing: `@little-workshop.md` from `.claude/AGENTS.md`,
# `@.claude/little-workshop.md` from a root `AGENTS.md`.
SHARED_BRIEFING_PATH = ".claude/little-workshop.md"
SHARED_BRIEFING_IMPORT = "@little-workshop.md"
# little-workshop writes these with `lws version install-hooks`, not this module.
GIT_HOOK_PATHS = (".githooks/pre-commit", ".githooks/commit-msg")

# The hard limits `claude-instructions-valid` holds the instructions to (REQ pending): one
# instruction file, and everything a session loads at launch (briefing, imports, rules without
# `paths`). Claude Code's own guidance is under 200 lines per file.
MAX_FILE_LINES = 200
MAX_STARTUP_LINES = 250

# A project id ends up inside permission rules and a shell command: keep it to what the
# Registry accepts as a name, so it can never carry a glob, quote or shell metacharacter.
_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# --- `lws` commands, the same split. A path is what follows `lws`. ---------------------------

# No project argument: a read by id or of the whole system, pre-allowed as it stands.
LWS_READ_ONLY_ANY = (
    "core status", "project list", "job list", "job status", "job wait", "job watch", "job queue show",
    "schedule list", "schedule status", "schedule queue get", "trigger list", "usage candidates", "usage plugin-proposals",
    "policy list", "policy status", "policy catalog list", "policy draft list", "policy scan-secrets",
    "requirements status", "requirements suite status", "build run-status", "agent session-list",
    "toolkit sources", "toolkit list", "windows doctor",
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
    "usage summary", "usage findings", "project backlog list", "project backlog show", "api token-list",
    "toolkit status",
)

LWS_ROUTINE = (
    "version feature-start", "version improvement-start", "version hotfix-start", "version support-start",
    "version release-candidate-cut", "version issue-set", "version issue-unset",
    "version install-hooks",
    "requirements create", "requirements update", "requirements supersede", "requirements suite create",
    "requirements use-case create", "requirements milestone create", "requirements link",
    "requirements unlink", "requirements result record", "requirements import-junit",
    "requirements run-tests", "requirements sync-markers",
    "build run", "project backlog add", "project backlog update",
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
    "job stop", "job policy-confirm", "job answer",
    "schedule add", "schedule edit", "schedule remove", "schedule trigger", "schedule cancel",
    "schedule postpone", "schedule queue set",
    "project bootstrap", "project connect-github", "project configure-assets", "project set-governance", "project normalize-eol", "project import", "project remove", "project backlog delete",
    "version init", "version config-add-hook", "version config-remove-hook",
    "requirements publish", "requirements delete", "requirements suite delete", "requirements import",
    "policy projects set",
    "deployment set-manifest", "deployment seed-confirmation",
    "agent run-task", "agent finish-task", "agent skip-task", "agent ci-check",
    "agent session-start", "agent session-stop",
    "usage configure-claude", "trigger add", "trigger remove", "trigger enable", "trigger disable", "trigger install-defaults",
    "api token-create", "api token-revoke",
    "toolkit add-source", "toolkit seed", "toolkit install", "toolkit remove", "toolkit update", "usage draft-skill",
)

# Administration of little-workshop itself, as MCP_PLATFORM.
LWS_PLATFORM = (
    "policy author", "policy draft import", "policy activate", "policy configure", "policy remove",
    "policy check-all", "policy catalog run", "deployment redeploy-pillar", "deployment redeploy-pillars",
    "usage doctor", "usage ingest-transcripts", "usage analyze", "usage mine-automation",
    "deployment redeploy-changed-pillars",
)

# Left to the default prompt on purpose: plumbing the pillars call themselves, host
# configuration, and commands that start a server or change what is installed.
LWS_UNLISTED = (
    "job create", "job policy-confirm-timeout", "job queue release",
    "requirements export", "requirements test-structure-create",
    "build print-pip-config", "build write-pip-config", "build pip", "build print-vsix-urls",
    "deployment rollback-timeout", "deployment redeploy-pillar-worker", "deployment rollback-pillar-worker",
    "worktree provision", "worktree release", "mcp serve", "asset-mcp serve", "windows install-shim",
    "bypass_policies",  # the user's alone (needs sudo); the command itself refuses Claude
    "version sync-version-files",  # retired: a no-op left for pre-commit hooks installed before
)

# Why a raw command is refused, and the little-workshop route the policies gate instead. The
# guard hook prints these when it refuses one (REQ pending), so Claude reads the route rather
# than a bare denial.
ROUTES = {
    "merge": "a branch is finished with workshop_version_<type>_finish, which runs the branch_finish policies "
             "and squash-merges a feature or improvement (a hotfix or a release merges with --no-ff)",
    "cherry-pick": "a fix reaches the integration branch through workshop_version_hotfix_start / _finish "
                   "(hotfix-propagates-by-merge)",
    "tag": "tags are written by workshop_version_release_* (annotated v<M>.<m>.<p>); read the release state with "
           "workshop_version_status",
    "push": "little-workshop pushes when it finishes or releases, and workshop_project_connect_github creates the "
            "remote; a push the user asks for is theirs to run",
    "branch-delete": "workshop_version_<type>_finish deletes a finished branch",
    "destructive": "it discards work that may not be yours: ask the user what to remove",
    "no-verify": "the commit-msg hook refuses a commit that adds a secret: take the secret out and commit again",
    "hooks-path": "core.hooksPath stays .githooks, where the commit-msg secret scan runs",
    "systemd": "servers run through workshop_deployment_deploy / _stop / _status",
    "publish": "packages are published with workshop_build_publish_pypi / _vsix (wheel-filename-has-version runs "
               "there)",
    "install": "the project installs with `bazel run //:install`; a server is deployed with "
               "workshop_deployment_deploy",
    "github": "workshop_project_connect_github creates the repository, little-workshop's agent flow opens and merges "
              "pull requests, and a release comes from workshop_version_release_finish",
}

# The permission rules that deny the same commands: the settings' backstop, should the guard not
# run. Each names the ROUTES entry the guard gives for it. A pattern ends at a word (`git merge *`,
# which also matches a bare `git merge`), so it cannot catch a longer command: `git merge*` denied
# the read-only `git merge-base` too.
DENY_REROUTED = (
    ("git merge *", "merge"),
    ("git cherry-pick *", "cherry-pick"),
    ("git tag *", "tag"),
    ("git push *", "push"),
    ("git branch -d *", "branch-delete"),
    ("git branch -D *", "branch-delete"),
    ("git reset --hard *", "destructive"),
    ("git clean *", "destructive"),
    ("git commit *--no-verify*", "no-verify"),
    ("systemd-run *", "systemd"),
    ("systemctl --user start *", "systemd"),
    ("systemctl --user stop *", "systemd"),
    ("systemctl --user restart *", "systemd"),
    ("systemctl --user kill *", "systemd"),
    ("twine *", "publish"),
    ("pipx install *", "install"),
    ("gh repo create *", "github"),
    ("gh pr create *", "github"),
    ("gh pr merge *", "github"),
    ("gh release *", "github"),
)

# Permission rules earlier versions of this table wrote and this one does not: the sync drops
# them from a project's settings instead of keeping them as the project's own.
RETIRED_RULES = tuple(f"Bash({pattern})" for pattern in (
    "git merge*", "git cherry-pick*", "git tag*", "git push*", "git branch -d*", "git branch -D*",
    "git reset --hard*", "git clean*", "git commit*--no-verify*", "systemd-run*", "systemctl --user start*",
    "systemctl --user stop*", "systemctl --user restart*", "systemctl --user kill*", "twine*", "pipx install*",
    "gh repo create*", "gh pr create*", "gh pr merge*", "gh release*",
))

READ_ONLY_BASH = (
    "git status*", "git diff*", "git log*", "git show*", "git blame*", "git ls-files*",
    "git rev-parse*", "git branch --show-current*", "git branch --list*",
    "systemctl --user status*",
    "gh pr view*", "gh pr list*", "gh pr checks*", "gh issue view*", "gh issue list*",
)

# Local commits are the normal flow; the commit-msg hook scans them for secrets.
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


# Heavy generated folders Claude must not read: a hard exclusion (`permissions.deny` with `Read(...)`;
# Claude Code has no `.claudeignore`). Build output is in `.gitignore` as well.
READ_DENY = {
    "python": ("./bazel-*/**", "./.venv*/**", "./node_modules/**", "./**/node_modules/**"),
    "bazel": ("./bazel-*/**", "./.venv*/**", "./node_modules/**", "./**/node_modules/**"),
    "unity": ("./Library/**", "./Temp/**", "./Logs/**", "./obj/**"),
}

# The code-intelligence plugin of each language (Anthropic's marketplace): Claude gets an `LSP` tool
# (definitions, references) instead of reading whole files. Each needs its language server on PATH,
# which `usage.doctor` checks.
PLUGIN_MARKETPLACE = "claude-plugins-official"
LSP_PLUGINS = ("pyright-lsp", "typescript-lsp", "clangd-lsp", "csharp-lsp")
# What a freshly scaffolded project of each template gets; an existing checkout's are detected.
_PLUGIN_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*")  # a toolkit plugin, `name@marketplace`
TEMPLATE_PLUGINS = {"python": ("pyright-lsp",), "bazel": (), "unity": ("csharp-lsp",)}

def hook_command(script: str, *args: str, windows: bool = False) -> str:
    """The command of a generated hook that runs `script` (a path in the checkout) with `args`. A checkout
    without the script (an older branch) gets a notice and exit 0, never a hook error that blocks the tool
    call (REQ-000000494). Every generated hook command is built here, so a platform's shell is one change.
    `windows`: a Windows-hosted checkout, whose sessions run in WSL or natively on Windows (Git Bash's `sh`):
    the script runs through `HOOK_RUNNER_PATH`, which finds a working Python on either side (REQ-000000513)."""
    tail = "".join(f" {arg}" for arg in args)
    missing = "little-workshop: $1 is missing in this checkout (an older branch?); the hook did not run"
    run = f'sh "$(dirname "$1")/{HOOK_RUNNER_PATH.rsplit("/", 1)[1]}"' if windows else "python3"
    return (f'sh -c \'[ -f "$1" ] && exec {run} "$@"; echo "{missing}" >&2\' hook '
            f'"${{CLAUDE_PROJECT_DIR}}"/{script}{tail}')


def hook_runner_sh() -> str:
    """`.claude/hooks/run_hook.sh`: runs a generated hook script with the first Python that really runs. On
    Windows `python3` is often the Microsoft Store stub, which prints "Python was not found" and fails: a hook
    failing that way does not block, so the guard was silently off in native sessions. With no Python, the
    scope guard blocks (exit 2) and the other hooks pass with a notice (REQ-000000513)."""
    return f"""#!/bin/sh
# Generated by little-workshop (src/registry/claude_templates.py); do not edit by hand.
# Runs a generated hook script ($1) with the first Python that works: python3 (WSL, Linux, a real
# Windows install), else the Windows launcher `py -3`, else python. Stdin (the hook input) is left
# for the script.
script="$1"
shift
for python in python3 "py -3" python; do
    if $python -c 'import sys' </dev/null >/dev/null 2>&1; then
        exec $python "$script" "$@"
    fi
done
case "$script" in
    *{GUARD_PATH.rsplit("/", 1)[1]})
        echo "little-workshop guard unavailable: no Python found (install Python 3 for Windows with the py launcher); the tool call is refused" >&2
        exit 2 ;;
esac
echo "little-workshop: no Python found; $script did not run" >&2
exit 0
"""


# little-workshop's own Claude Code settings: the scaffolded table plus what only this repository needs
# (REQ-000000295). Its checked-in .claude/ files are claude_files("little-workshop", "python",
# **OWN_SETTINGS); a unit test keeps them equal. The live-install guard denies running
# tests/integration beside the installed pillars (REQ-000000304).
LIVE_INSTALL_GUARD_PATH = ".claude/hooks/live_install_guard.py"
OWN_SETTINGS = dict(
    platform=True,
    plugins=("pyright-lsp",),
    extra_allow=("python3 tools/sync_versions.py*", "tools/check_system_deps.sh*", "python3 tools/sync_policies.py*"),
    extra_ask=("bazel run //:install*", "bazel run //*_server*", "python3 tools/sync_policies.py --apply*"),
    extra_hooks=({
        "matcher": "Bash",
        "hooks": [{
            "type": "command",
            "command": hook_command(LIVE_INSTALL_GUARD_PATH),
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


# The tools the guard judges: shell commands, file edits and the little-workshop MCP tools.
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
GUARD_MATCHER = "|".join(("Bash", *EDIT_TOOLS, f"{MCP}.*"))


def guard_hooks(project_id: str, extra_pre_tool_use: tuple[dict, ...] = (), *, windows: bool = False) -> dict:
    """The `hooks` block registering the guard for Bash, the file-edit tools and every
    little-workshop MCP tool, then any project-specific PreToolUse entries (little-workshop's own
    live-install guard)."""
    return {
        "PreToolUse": [{
            "matcher": GUARD_MATCHER,
            "hooks": [{
                "type": "command",
                "command": hook_command(GUARD_PATH, _checked_id(project_id), windows=windows),
                "timeout": 10,
            }],
        }, {
            # After the guard: a plain test command is run through the filter, so a long run puts its
            # failures and summary in the context, not every line.
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": hook_command(TEST_FILTER_PATH, windows=windows),
                "timeout": 10,
            }],
        }, *extra_pre_tool_use],
    }


def settings(project_id: str, template: str = "python", *, platform: bool = False,
             extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
             extra_hooks: tuple[dict, ...] = (), plugins: tuple[str, ...] = (), windows: bool = False) -> dict:
    """The `.claude/settings.json` of project `project_id`; `windows` for a Windows-hosted checkout.

    Every `lws` command that takes `--project-id` is pre-allowed only with this project's id; the
    guard hook denies a call that names another. `platform=True` (little-workshop itself) also
    pre-allows the tools that administer little-workshop rather than a project. `extra_hooks` are
    PreToolUse entries added after the scope guard and the test-output filter; `plugins` are the
    code-intelligence plugins of the project's languages (`LSP_PLUGINS`, by bare name) and the `plugin@marketplace`
    keys its toolkit lock enables (`registry/toolkit.py`)."""
    project = _checked_id(project_id)
    unknown = [plugin for plugin in plugins if plugin not in LSP_PLUGINS and not _PLUGIN_KEY.fullmatch(plugin)]
    if unknown:
        raise ValueError(f"unknown plugin(s) {', '.join(unknown)}, expected some of {', '.join(LSP_PLUGINS)} or plugin@marketplace")
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
    out = {
        "permissions": {
            "allow": allow,
            "ask": ask,
            "deny": [f"Bash({c})" for c, _ in DENY_REROUTED] + [f"Read({path})" for path in READ_DENY[template]],
        },
        "hooks": guard_hooks(project, extra_hooks, windows=windows),
    }
    if plugins:
        out["enabledPlugins"] = {plugin if "@" in plugin else f"{plugin}@{PLUGIN_MARKETPLACE}": True for plugin in plugins}
    return out


def settings_json(project_id: str, template: str = "python", *, platform: bool = False,
                  extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
                  extra_hooks: tuple[dict, ...] = (), plugins: tuple[str, ...] = (), windows: bool = False) -> str:
    return json.dumps(settings(project_id, template, platform=platform, extra_allow=extra_allow,
                               extra_ask=extra_ask, extra_hooks=extra_hooks, plugins=plugins, windows=windows), indent=2) + "\n"


def _project_section(text: str) -> tuple[int, int] | None:
    start = text.find(PROJECT_SECTION_START)
    end = text.find(PROJECT_SECTION_END, start + 1) if start >= 0 else -1
    return (start + len(PROJECT_SECTION_START), end) if start >= 0 and end >= 0 else None


def keep_project_section(generated: str, current: str | None) -> str:
    """`generated` with the project's own section taken from `current` (the file as the checkout holds it), where
    both carry the markers (REQ-000000497)."""
    ours = _project_section(generated)
    theirs = _project_section(current) if current else None
    if ours is None or theirs is None:
        return generated
    return generated[:ours[0]] + current[theirs[0]:theirs[1]] + generated[ours[1]:]


# The guard hook (REQ-000000297, extended REQ pending). A real, standalone script copied into each
# project: the permission rules cannot match an MCP tool's arguments (a settings rule naming an
# `mcp__` tool with parentheses is skipped), a Bash rule is only as good as the argument order it
# assumes, and a permission denial does not say what to do instead. The guard does, so a refused
# call teaches the route the moment Claude reaches for the wrong one.
_GUARD_SOURCE = r'''#!/usr/bin/env python3
"""PreToolUse hook: the hard layer of this project's Claude instructions.

Registered in .claude/settings.json for Bash, the file-edit tools and the
little-workshop MCP tools. Usage: lws_scope_guard.py PROJECT_ID, with the hook's
JSON on stdin. It refuses, and says what to do instead:
- an `lws` command or a little-workshop MCP call that names another project
  (--project-id, project_id, a project name), or that leaves the project id out
  where leaving it out acts on every project;
- a raw command that would bypass the policy gates little-workshop runs (git
  merge, tag, push, cherry-pick, branch deletion, --no-verify, a core.hooksPath
  override, systemd, twine, gh...), in any spelling it can parse;
- an edit of a file little-workshop generates (VERSION, .githooks, its .claude/
  files) or of a CLAUDE.md, which would stop Claude Code reading AGENTS.md.
Input it cannot read passes: the permission rules in settings.json and the
policies are the other gates. Generated by little-workshop
(registry/claude_templates.py): change the generator, not this copy."""

import json
import os
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
# Why a raw command is refused, and the route to take instead.
ROUTES = @@ROUTES@@
# Files little-workshop writes, relative to the checkout -> how they change.
GENERATED = @@GENERATED@@
GIT_HOOKS = @@GIT_HOOKS@@
EDIT_TOOLS = @@EDIT_TOOLS@@
STRAY_INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md")
LEARNED_RULES = @@LEARNED_RULES@@
SECTION_START, SECTION_END = @@SECTION_MARKERS@@
SHELLS = ("bash", "sh", "zsh", "dash")
OPERATORS = set("();<>|&`")
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
# git's global options that take a value as the next word.
GIT_VALUE_OPTIONS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
                     "--super-prefix")
# `git commit` options whose value is the next word (so that word is never read as a flag).
COMMIT_VALUE_OPTIONS = ("-m", "-F", "-C", "-c", "-t", "--message", "--file", "--author", "--date", "--template",
                        "--reuse-message", "--reedit-message", "--fixup", "--squash", "--trailer", "--cleanup",
                        "--pathspec-from-file")
COMMIT_VALUE_LETTERS = "mFCct"


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


def short_flags(word):
    """The letters of a short-option cluster (`-am` -> "am"), or "" for anything else."""
    return word[1:] if word.startswith("-") and not word.startswith("--") and len(word) > 1 else ""


def commit_skips_hooks(args):
    """Whether `git commit ARGS` turns the hooks off (--no-verify, or -n in a short-option cluster)."""
    skip_next = False
    for word in args:
        if skip_next:
            skip_next = False
            continue
        if word == "--":
            return False
        if word == "--no-verify":
            return True
        if word in COMMIT_VALUE_OPTIONS:
            skip_next = True
            continue
        for i, letter in enumerate(short_flags(word)):
            if letter == "n":
                return True
            if letter in COMMIT_VALUE_LETTERS:
                skip_next = i == len(word) - 2  # the value is the next word only when the letter ends the cluster
                break
    return False


def git_route(args):
    """The ROUTES key for `git ARGS` when it bypasses little-workshop, or None."""
    i, overrides = 0, []
    while i < len(args) and args[i].startswith("-"):
        option = args[i].split("=", 1)[0]
        if option in GIT_VALUE_OPTIONS and "=" not in args[i]:
            if option == "-c" and i + 1 < len(args):
                overrides.append(args[i + 1])
            i += 2
            continue
        i += 1
    if any(o.split("=", 1)[0].lower() == "core.hookspath" for o in overrides):
        return "hooks-path"
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    if sub in ("merge", "cherry-pick", "tag", "push"):
        return sub
    if sub == "commit":
        return "no-verify" if commit_skips_hooks(rest) else None
    if sub == "branch":
        deleting = any(w in ("--delete", "-d", "-D") or set(short_flags(w)) & {"d", "D"} for w in rest)
        return "branch-delete" if deleting else None
    if sub == "reset":
        return "destructive" if "--hard" in rest else None
    if sub == "clean":
        return "destructive"
    if sub == "config" and any(w.lower() == "core.hookspath" for w in rest):
        values = [w for w in rest if not w.startswith("-") and w.lower() != "core.hookspath"]
        unsetting = any(w in ("--unset", "--unset-all") for w in rest)
        return "hooks-path" if unsetting or (values and values != [".githooks"]) else None
    return None


def command_route(words):
    """The ROUTES key for one simple command, or None."""
    base = words[0].rsplit("/", 1)[-1]
    args = words[1:]
    if base == "git":
        return git_route(args)
    if base == "systemd-run":
        return "systemd"
    if base == "systemctl" and "--user" in args and set(args) & {"start", "stop", "restart", "kill"}:
        return "systemd"
    if base == "twine":
        return "publish"
    if base == "pipx" and args[:1] == ["install"]:
        return "install"
    if base == "gh" and (args[:2] in (["repo", "create"], ["pr", "create"], ["pr", "merge"]) or args[:1] == ["release"]):
        return "github"
    return None


def without_env_assignments(words):
    """The words after any leading NAME=value assignments and `env`, `command`, `exec`, `sudo`."""
    i = 0
    while i < len(words) and (re.match(r"^[A-Za-z_]\w*=", words[i]) or words[i] in ("env", "command", "exec", "sudo")):
        i += 1
    return words[i:]


def bash_violation(command, own):
    for words in simple_commands(command):
        words = without_env_assignments(words)
        if not words:
            continue
        route = command_route(words)
        if route:
            shown = " ".join(words[:3])
            return f"`{shown}` is refused here: {ROUTES[route]}."
        for i, word in enumerate(words):
            base = word.rsplit("/", 1)[-1]
            if base == "lws":
                problem = lws_violation(words[i + 1:], own)
                if problem:
                    return (f"this checkout is project {own!r} but the lws command {problem}. Work on other "
                            "projects belongs in a session started from the little-projects root.")
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


def outside_section(text):
    """The text before and after the project's own section of a generated file, or None without one."""
    start = text.find(SECTION_START)
    end = text.find(SECTION_END, start + 1) if start >= 0 else -1
    return (text[:start], text[end:]) if start >= 0 and end >= 0 else None


def edits_only_own_section(full, args):
    """Whether the edit changes nothing of a generated file but its project-owned section."""
    try:
        with open(full, encoding="utf-8") as fh:
            current = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    kept = outside_section(current)
    if kept is None:
        return False
    if isinstance(args.get("content"), str):
        return outside_section(args["content"]) == kept
    edited = current
    for edit in args.get("edits") or [args]:
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old or old not in edited:
            return False
        edited = edited.replace(old, new) if edit.get("replace_all") else edited.replace(old, new, 1)
    return outside_section(edited) == kept


def edit_violation(args, root, own):
    """Why editing this file is refused, or None. Paths outside the checkout pass."""
    path = args.get("file_path") or args.get("notebook_path")
    if not isinstance(path, str) or not path or not root:
        return None
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    rel = os.path.relpath(full, os.path.realpath(root)).replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    if rel.rsplit("/", 1)[-1] in STRAY_INSTRUCTIONS or rel == ".claude/CLAUDE.md":
        return (f"{rel} is refused here: Claude Code reads AGENTS.md only where no CLAUDE.md, .claude/CLAUDE.md or "
                "CLAUDE.local.md exists. Write a learned rule (claude-reflect's included) as a bullet in "
                f"{LEARNED_RULES}, project guidance in .claude/AGENTS.md or a path-scoped .claude/rules/ file, and "
                "personal notes in ~/.claude/.")
    if rel in GENERATED and not edits_only_own_section(full, args):
        own_section = (f" Only the part between {SECTION_START} and {SECTION_END} is the project's to edit."
                       if rel.endswith(".md") else "")
        return f"{rel} is refused here: {GENERATED[rel]}.{own_section}"
    if rel in GIT_HOOKS:
        return (f"{rel} is refused here: `lws version install-hooks --project-id {own}` writes it (generator: "
                "little-workshop's src/version/commands/git_hooks.py).")
    return None


def main():
    own = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.load(sys.stdin)
        tool = event["tool_name"]
        args = event.get("tool_input") or {}
    except (ValueError, KeyError, TypeError):
        return
    if not own or not isinstance(args, dict):
        return
    if tool.startswith(PREFIX):
        problem = mcp_violation(tool, args, own)
        if problem:
            problem = (f"this checkout is project {own!r} but {problem}. Work on other projects belongs in a "
                       "session started from the little-projects root.")
    elif tool == "Bash":
        problem = bash_violation(str(args.get("command", "")), own)
    elif tool in EDIT_TOOLS:
        problem = edit_violation(args, os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd"), own)
    else:
        return
    if problem:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"little-workshop: {problem}",
        }}))


if __name__ == "__main__":
    main()
'''


def generated_files_reasons() -> dict[str, str]:
    """Each file the guard refuses to let Claude edit by hand -> how it changes instead."""
    regenerate = ("little-workshop generates it: change src/registry/claude_templates.py in little-workshop and run "
                  "its `python3 tools/sync_claude_files.py --write`")
    reasons = {path: regenerate for path in (GUARD_PATH, PREFLIGHT_SKILL_PATH, PREFLIGHT_SCRIPT_PATH,
                                             LIFECYCLE_SKILL_PATH, INSTRUCTIONS_RULE_PATH, SHARED_BRIEFING_PATH,
                                             POLICIES_SKILL_PATH, POLICIES_SCRIPT_PATH, TEST_FILTER_PATH,
                                             HOOK_RUNNER_PATH)}
    reasons["VERSION"] = ("little-workshop writes VERSION: the feature, improvement, hotfix and release finishes write it "
                          "once; working branches leave it alone")
    return reasons


def scope_guard_py() -> str:
    """The text of `.claude/hooks/lws_scope_guard.py`."""
    def items(names: tuple[str, ...]) -> str:
        return "\n".join(f'    "{n}",' for n in names)

    def literal(value) -> str:
        return json.dumps(value, indent=4, sort_keys=True)

    return (_GUARD_SOURCE
            .replace("@@NEEDS_PROJECT_ID@@", items(MCP_NEEDS_PROJECT_ID))
            .replace("@@NAMED_PROJECT@@", items(MCP_NAMED_PROJECT))
            .replace("@@ROUTES@@", literal(ROUTES))
            .replace("@@GENERATED@@", literal(generated_files_reasons()))
            .replace("@@GIT_HOOKS@@", repr(GIT_HOOK_PATHS))
            .replace("@@EDIT_TOOLS@@", repr(EDIT_TOOLS))
            .replace("@@LEARNED_RULES@@", repr(LEARNED_RULES_PATH))
            .replace("@@SECTION_MARKERS@@", repr((PROJECT_SECTION_START, PROJECT_SECTION_END))))


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

It evaluates the policies in little-workshop's sandbox (`lws policy evaluate`: the checks the gate runs;
nothing is held, and a verdict on committed, unchanged files is recorded so the gate reuses it). If the
installed `lws` or the running Policy pillar predates that, it runs the same policy scripts here and says
so. Exit 0: nothing blocks; 1: a gating policy fails; 2: the policies could not be evaluated. At
`release`, the policy that runs the test suite (python-test-coverage) takes minutes.

## Acting on the result

Each failure names the policy, its rule and what to change. Fix the cause in the project, commit, and run
the preflight again until nothing blocks.

A policy is the project's agreed rule, so change the project, not the policy, and leave a held action for
the user to confirm. If a failure looks wrong, or cannot be fixed in this project, report it with the
policy's output and let the user decide. The steps around a finish are in the `workshop-lifecycle` skill.
"""

_PREFLIGHT_SOURCE = r'''#!/usr/bin/env python3
"""Run the little-workshop policies that would gate an action on this project, against this
checkout, and print every failure with its reason. The `policy-preflight` skill's script.

    python3 .claude/skills/policy-preflight/scripts/preflight.py [--gate-point branch_finish]

Evaluates in little-workshop's policy sandbox (`lws policy evaluate`: the checks the gate itself
runs; nothing is held, and verdicts on committed files are recorded for the gate to reuse). When the installed lws or the running Policy pillar predates
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
# Terminal colour codes: a coloured `E   ...` line would hide the assertion message from the report.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


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
                 "-p", "no:cacheprovider", "--color=no"],
                capture_output=True, text=True, env=env, cwd=scratch,
            )
            stdout = _ANSI.sub("", run.stdout)
            counts = {kind: int(n) for n, kind in _SUMMARY.findall(stdout.strip().splitlines()[-1] if stdout.strip() else "")}
            outcome = ("fail" if counts.get("failed") or run.returncode not in (0, 1) else
                       "pass" if counts.get("passed") else "not_applicable" if counts.get("skipped") else "error")
            reasons = [line[2:].strip() if line.startswith("E ") else line.strip()
                       for line in stdout.splitlines() if line.startswith(("E ", "SKIPPED "))]
            results.append({"topic": policy["topic"], "ears_text": policy["ears_text"], "gating": policy.get("gating", True),
                            "outcome": outcome, "detail": "\n".join(dict.fromkeys(reasons)) or stdout[-800:]})
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
    return _PREFLIGHT_SKILL + "\n" + PROJECT_SECTION


def preflight_py() -> str:
    """The text of `.claude/skills/policy-preflight/scripts/preflight.py`."""
    return _PREFLIGHT_SOURCE


# The workshop-lifecycle skill (REQ pending): the routes and the steps before a finish, loaded
# when the work reaches one of them instead of in every session.
def lifecycle_skill_md(name: str, *, platform: bool = False, governance: str = "light") -> str:
    """The text of `.claude/skills/workshop-lifecycle/SKILL.md`. `platform=True` (little-workshop itself)
    adds the route for redeploying its pillars; `governance` `full` the requirement routes (REQ-000000496)."""
    project = _checked_id(name)
    requirements = (
        "| Create, update or supersede a requirement | `workshop_requirements_create` / `_update` / `_supersede` |\n"
        "| Register and link the requirements the tests' `REQ-` markers name | `workshop_requirements_sync_markers`: "
        "one call, instead of a create and a link per requirement |\n"
        if governance == "full" else ""
    )
    redeploy = (
        "| Redeploy little-workshop's pillars | After a branch finish, `workshop_deployment_redeploy_changed_pillars` "
        "(the pillars the branch touched, found for you; a trigger may already have started it: check "
        "`workshop_job_list`). To choose them yourself, `workshop_deployment_redeploy_pillars`: one call for several "
        "pillars, which orders them itself; never one `workshop_deployment_redeploy_pillar` call per pillar |\n"
        if platform else ""
    )
    return f"""---
name: workshop-lifecycle
description: The little-workshop routes for this project's lifecycle - starting or finishing a feature, improvement or hotfix branch, cutting or finishing a release, deploying or stopping the server, publishing a package, recording requirements and test runs - and the steps before a branch finish. Use when the work reaches one of those steps, or when the guard refused a raw git, systemd or publishing command.
---

# This project's lifecycle through little-workshop

little-workshop owns the branches, versions, releases, servers and packages of `{project}`, and checks the
project's policies at each of those steps; the raw git, systemd and publishing commands skip those checks, so
the guard refuses them. Name the project in every call: `project_id` `{project}` for an MCP tool,
`--project-id {project}` for `lws` (the same commands: `lws version feature-finish --project-id {project}`).

| To do this | Use |
|---|---|
| Start a branch | `workshop_version_feature_start` (milestone work, from `dev`), `workshop_version_improvement_start` (anything else, from `dev`), `workshop_version_hotfix_start` (from the production branch) |
| Finish it: squash merge (a hotfix: `--no-ff` into production and back into `dev`), `VERSION` bump, branch deleted | `workshop_version_<type>_finish` |
| Cut, promote or finish a release | `workshop_version_release_start`, `workshop_version_release_candidate_cut` / `_promote`, `workshop_version_release_finish` |
| Deploy, stop or inspect the server | `workshop_deployment_deploy`, `_stop`, `_status` |
| Publish a package | `workshop_build_publish_pypi`, `workshop_build_publish_vsix` |
{requirements}| Record a test run | `workshop_requirements_run_tests` (or `_import_junit`) |
| Know the branch, version or policy state | `workshop_version_status`, `workshop_policy_results` |
| Create the GitHub repository and push to it | `workshop_project_connect_github` |
| Wait for a Job (a test run, a finish, a deploy) | `workshop_job_wait`: one call that returns when the Job settles, is held or asks a question. Never repeat `workshop_job_status` or `sleep` |
{redeploy}
If the daemon is down (`workshop_core_status`), say so and stop at that step: a raw git fallback skips the
policies.

## Finishing a branch

1. Run the `policy-preflight` skill and fix what blocks.
2. Finish with `workshop_version_<type>_finish`. A finish that lands in `pending_confirmation` was held by a
   policy: report which one (`workshop_policy_results`) and leave the confirmation to the user.

No separate Definition-of-Done run before a finish. In a Python project the release's coverage check runs
the suite once and records it; elsewhere record the run (`workshop_requirements_run_tests` or `_import_junit`)
before the release.

{PROJECT_SECTION}"""


# The policies digest (REQ-000000339): which policies cover this project and what each requires,
# asked of little-workshop when the question comes up, instead of rereading the policy documents.
def policies_skill_md(name: str) -> str:
    """The text of `.claude/skills/policies-digest/SKILL.md`."""
    project = _checked_id(name)
    return f"""---
name: policies-digest
description: Lists the little-workshop policies that cover this project - each policy's rule in one sentence and the actions it gates (commit, branch finish, release, publish, deploy). Use when you need to know what the policies require or which rule a change must satisfy, instead of reading the policy documents.
---

# The policies that cover `{project}`

Run it from the repository root:

```
python3 {POLICIES_SCRIPT_PATH} --project-id {project}            # every policy attached to the project
python3 {POLICIES_SCRIPT_PATH} --project-id {project} --gate branch_finish
```

It prints one line per policy: its name, the actions it gates and its rule, read live from
little-workshop, so it is never out of date. To check the project against them, use the `policy-preflight`
skill; to see the last recorded results, `workshop_policy_results`.

{PROJECT_SECTION}"""


_POLICIES_DIGEST_SOURCE = r'''#!/usr/bin/env python3
"""Print the little-workshop policies attached to a project: name, gate points, rule.

Generated by little-workshop (registry/claude_templates.py): change the generator, not this copy."""

import argparse
import json
import os
import subprocess
import sys


def lws(*args):
    env = {**os.environ, "LWS_NO_PROGRESS": "1"}
    result = subprocess.run(["lws", *args], capture_output=True, text=True, env=env)
    try:
        return json.loads(result.stdout)
    except ValueError:
        sys.exit("could not read little-workshop's answer to `lws " + " ".join(args) + "`: is the daemon running "
                 "(`lws core status`)?")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--gate", default=None, help="Only the policies gating this action (commit, branch_finish, ...)")
    args = parser.parse_args()
    attached = {row["policy_id"]: row for row in lws("policy", "projects", "list", "--project-id", args.project_id).get("projects", [])}
    shown = 0
    for policy in sorted(lws("policy", "list").get("policies", []), key=lambda p: p.get("topic") or ""):
        if policy.get("id") not in attached:
            continue
        gates = policy.get("gating") or policy.get("gate_points") or []
        if args.gate and args.gate not in gates:
            continue
        held = "" if attached[policy["id"]].get("gating_enabled", True) else " (gating off for this project)"
        print("- " + str(policy.get("topic")) + " [" + (", ".join(gates) or "advisory") + "]" + held + ": " + " ".join(str(policy.get("ears_text") or "").split()))
        shown += 1
    print(str(shown) + " polic" + ("y" if shown == 1 else "ies") + " for " + args.project_id + (" gating " + args.gate if args.gate else ""))


if __name__ == "__main__":
    main()
'''


def policies_digest_py() -> str:
    return _POLICIES_DIGEST_SOURCE


# The test-output filter (REQ-000000339): a PreToolUse hook that sends a plain test command through
# itself, so a long run leaves its failures and summary in Claude's context instead of every line.
_TEST_FILTER_SOURCE = r'''#!/usr/bin/env python3
"""PreToolUse hook and output filter for test runs.

As a hook (no arguments, the hook's JSON on stdin): when the Bash command is one plain test run
(pytest, bazel test, npm test, vitest, tools/run-unity-tests) it rewrites the command to pipe its
output through this file with --filter, keeping the command's exit status. Anything else passes
unchanged, as does input it cannot read.

As a filter (--filter): short output is printed whole. Long output is saved to a file and reduced
to the lines that report failures, with some context, and the end of the run.

Generated by little-workshop (registry/claude_templates.py): change the generator, not this copy."""

import json
import os
import re
import sys
import tempfile

TEST_COMMAND = re.compile(r"^\s*(python3? -m pytest|pytest|bazel test|npm (run )?test|npx vitest|tools/run-unity-tests)\b")
SHELL_SYNTAX = re.compile(r"[|;&<>`$()]")
KEEP_WHOLE_LINES = 150
TAIL_LINES = 40
CONTEXT_LINES = 3
MAX_KEPT_LINES = 400
FAILURE = re.compile(r"(FAIL|ERROR|Error|error:|Traceback|AssertionError|^E\s|^\s*assert |panicked|✗|✘|not ok)")


def hook():
    try:
        event = json.load(sys.stdin)
        command = event["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return
    if event.get("tool_name") != "Bash" or not TEST_COMMAND.match(command) or SHELL_SYNTAX.search(command):
        return
    here = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or ".", ".claude", "hooks", "test_output_filter.py")
    rewritten = "set -o pipefail; " + command + " 2>&1 | python3 " + json.dumps(here) + " --filter"
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "a test run, its output filtered to failures and the summary",
        "updatedInput": {**event["tool_input"], "command": rewritten},
    }}))


def filtered(lines):
    """The lines worth reading of a long test run, in order, with a marker where lines were left out."""
    keep = set(range(max(len(lines) - TAIL_LINES, 0), len(lines)))
    for index, line in enumerate(lines):
        if FAILURE.search(line):
            keep.update(range(max(index - CONTEXT_LINES, 0), min(index + CONTEXT_LINES + 1, len(lines))))
    chosen = sorted(keep)
    if len(chosen) > MAX_KEPT_LINES:
        chosen = chosen[:MAX_KEPT_LINES - TAIL_LINES] + chosen[-TAIL_LINES:]
    out, previous = [], -1
    for index in chosen:
        if index != previous + 1:
            out.append("[... " + str(index - previous - 1) + " line(s) left out ...]")
        out.append(lines[index])
        previous = index
    return out


def filter_output():
    lines = sys.stdin.read().splitlines()
    if len(lines) <= KEEP_WHOLE_LINES:
        print("\n".join(lines))
        return
    with tempfile.NamedTemporaryFile("w", prefix="test-output-", suffix=".log", delete=False, encoding="utf-8") as full:
        full.write("\n".join(lines) + "\n")
    print("[test output: " + str(len(lines)) + " lines, filtered to failures and the end of the run; all of it is in " + full.name + "]")
    print("\n".join(filtered(lines)))


if __name__ == "__main__":
    filter_output() if sys.argv[1:] == ["--filter"] else hook()
'''


def test_output_filter_py() -> str:
    return _TEST_FILTER_SOURCE


# The newest request outranks what was decided before (REQ-000000478). Earlier choices live in many
# places a long task rereads (design docs, house settings, memories, requirement rows, tests pinning a
# look) and each reads as a rule, so the shared section states the order.
NEWEST_REQUEST_WINS = (
    "The user's newest request outranks earlier decisions (design docs, instruction files, memories,\n"
    "requirement rows, tests that pin a look): follow it, change the old item in the same work and\n"
    "say what you changed. Read the request and the files it names in full; when it is unclear, ask."
)

# The path-scoped rule on the instructions themselves (REQ pending): where an instruction belongs,
# what checks them and what little-workshop generates. It loads when Claude reads an instruction file.
def instructions_rule_md() -> str:
    """The text of `.claude/rules/claude-instructions.md`."""
    return f"""---
paths:
  - ".claude/**"
  - "**/AGENTS.md"
---
# Claude instructions in this project

Where an instruction lives decides when it loads, so put each one where it is needed:

- The briefing (`.claude/AGENTS.md`, or a root `AGENTS.md`) is read at the start of every session: what the
  project is, the commands Claude cannot guess, its pitfalls and where to start. `@path` imports load at launch
  too, so they organise the briefing without making it cheaper.
- Guidance for one part of the tree is a `.claude/rules/<topic>.md` file whose `paths:` frontmatter lists glob
  patterns; it loads when Claude reads a matching file. `paths` is the only field Claude Code reads there.
- A procedure, above all one with a script, is a skill: `.claude/skills/<name>/SKILL.md`, whose `description`
  says when to use it, with its scripts under `scripts/`. It loads when the task calls for it.
- A rule that must hold every time is a hook (`.claude/hooks/`, registered in `.claude/settings.json`) or a
  little-workshop policy (the gates run them), not a sentence: a sentence is advice.
- A rule learned in a session (a correction, claude-reflect's "learned rules") is a bullet in
  `{LEARNED_RULES_PATH}`: the project's own rule file, without `paths`, so it loads in every session.

When changing them, update or delete an existing line before adding one, and add only what the code does not
show (commands, pitfalls, reasons, conventions that differ from the defaults), checked against the repository.
The policy `claude-instructions-valid` fails an `@` import that does not resolve, a rule pattern that matches no
file, and a skill or subagent without a description; an instruction file over {MAX_FILE_LINES} lines or more than
{MAX_STARTUP_LINES} lines loaded at session start is only a warning.

little-workshop generates `.claude/little-workshop.md`, this rule, the `policy-preflight` and
`workshop-lifecycle` skills, `.claude/hooks/lws_scope_guard.py` and the permission table and guard hook in
`.claude/settings.json` (entries the project adds beside them are kept). Change them in little-workshop's
`src/registry/claude_templates.py` and rewrite them with its `tools/sync_claude_files.py`; the guard refuses hand
edits, except between `{PROJECT_SECTION_START}` and `{PROJECT_SECTION_END}` in a generated Markdown file: that part is
the project's and the sync keeps it. The project's own rules, skills and agents are never touched. Never add a
`CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md`: Claude Code then stops reading `AGENTS.md`.

{PROJECT_SECTION}"""


# The briefing section every project shares (REQ-000000294, REQ-000000305), imported by the
# project's own AGENTS.md: who runs the lifecycle and what enforces the rules, with the details
# left to the skills and the hooks' own messages.
def windows_briefing_md(name: str) -> str:
    """The shared briefing's part for a checkout on a Windows drive: how the project's own Windows code reaches
    little-workshop, which stays in WSL (docs/architecture.md, "Windows-hosted projects")."""
    project = _checked_id(name)
    return f"""
Windows: this checkout is on a Windows drive, so code that runs on Windows (a Unity editor script, a Windows
service, a `.bat`) can reach little-workshop too; little-workshop itself stays in WSL.

- `lws.cmd <command> ...` runs WSL's `lws` (the user installs it once with `lws windows install-shim`): the same
  arguments with Windows paths translated, and `--project-id` defaulting to `{project}` inside this checkout.
- HTTP: `POST http://127.0.0.1:8790/api/v1/tools/<tool>` with the tool's arguments as a JSON object and
  `Authorization: Bearer <token>`; `GET /api/v1/tools` lists the tools. It acts as `{project}`: reads, routine
  work and policy-gated actions; decisions that stand in for a human are refused. The user creates the token
  (`lws api token-create --project-id {project} --write <file>`); read it from that file, never commit it.
"""


def shared_briefing_md(name: str, *, platform: bool = False, windows: bool = False, governance: str = "light") -> str:
    """The text of `.claude/little-workshop.md`; `windows` adds how Windows code reaches little-workshop, `governance`
    `full` the requirement work (REQ-000000496)."""
    project = _checked_id(name)
    scope = ("This session also administers little-workshop (policies, pillar redeploys)." if platform
             else "Other projects belong in a session started from `little-projects`.")
    requirements = (
        "\nGovernance `full`: behaviour changes ship with tests under `# --- REQ-nnnnnnnnn: <title> ---` sections and\n"
        "their requirements created or superseded; cite the ids in commit messages.\n"
        if governance == "full" else ""
    )
    return f"""## little-workshop

little-workshop runs the branches, releases, deploys and publishes of `{project}`, checking its policies at
each: use the `workshop_*` MCP tools (`project_id` `{project}`) or `lws --project-id {project}`
(routes: the `workshop-lifecycle` skill). {scope}
{requirements}
{NEWEST_REQUEST_WINS}
{windows_briefing_md(project) if windows else ""}
Checked, and each refusal says what to do instead: `.githooks/commit-msg` refuses a secret (`VERSION` is
written by the finish); a finish, release, deploy or publish holds a failing policy in `pending_confirmation`
(run the `policy-preflight` skill first, report a hold); the guard `lws_scope_guard.py` refuses raw git, systemd
and publishing commands, other projects, edits of generated files and any `CLAUDE.md` (a learned rule goes in
`{LEARNED_RULES_PATH}`); human decisions prompt.

{PROJECT_SECTION}"""


def claude_files(project_id: str, template: str = "python", *, platform: bool = False,
                 extra_allow: tuple[str, ...] = (), extra_ask: tuple[str, ...] = (),
                 extra_hooks: tuple[dict, ...] = (), plugins: tuple[str, ...] = (),
                 windows: bool = False, governance: str = "light") -> dict[str, str]:
    """Path (relative to the project root) -> text of every `.claude/` file little-workshop
    generates: the settings, the guard, the shared briefing section, the instructions rule and the
    two skills. The `AGENTS.md` is separate (`agents_md`): a registered project keeps its own and
    imports the shared section from it."""
    files = {
        SETTINGS_PATH: settings_json(project_id, template, platform=platform, extra_allow=extra_allow,
                                     extra_ask=extra_ask, extra_hooks=extra_hooks, plugins=plugins, windows=windows),
        GUARD_PATH: scope_guard_py(),
        SHARED_BRIEFING_PATH: shared_briefing_md(project_id, platform=platform, windows=windows, governance=governance),
        INSTRUCTIONS_RULE_PATH: instructions_rule_md(),
        LIFECYCLE_SKILL_PATH: lifecycle_skill_md(project_id, platform=platform, governance=governance),
        PREFLIGHT_SKILL_PATH: preflight_skill_md(),
        PREFLIGHT_SCRIPT_PATH: preflight_py(),
        POLICIES_SKILL_PATH: policies_skill_md(project_id),
        POLICIES_SCRIPT_PATH: policies_digest_py(),
        TEST_FILTER_PATH: test_output_filter_py(),
    }
    if windows:
        files[HOOK_RUNNER_PATH] = hook_runner_sh()
    return files


# Kept out of Claude's context (Claude Code strips block-level HTML comments), for whoever edits the file.
BRIEFING_NOTE = """<!-- A briefing, loaded into every session: keep it short and about this project. Guidance for one
part of the tree goes in .claude/rules/ with `paths:` frontmatter, procedures in .claude/skills/, and a rule
that must hold every time in a hook or a little-workshop policy, not in a sentence here. This file is
.claude/AGENTS.md on purpose: Claude Code reads AGENTS.md only where the project has no CLAUDE.md,
.claude/CLAUDE.md or CLAUDE.local.md, so the guard refuses those (REQ-000000294). The last line imports the
section little-workshop generates for every project. -->"""


def agents_md(name: str, governance: str = "light") -> str:
    """`.claude/AGENTS.md` for the python layout (REQ-000000294); the unity layout has its own."""
    working = (
        "\n## Working here\n\n- Behaviour changes ship with tests tagged with the requirement ids they cover.\n"
        if governance == "full" else ""
    )
    return f"""# {name}

{BRIEFING_NOTE}

Managed by little-workshop. Read `docs/design.md` (what and why) and `docs/architecture.md` (how) before changing
code.

## Commands

- Tests: `python3 -m pytest tests/unit` (the Definition-of-Done suite: network-free, never skipping).
- Build every artifact: `bazel build //:all`; install the project: `bazel run //:install`.
{working}
{SHARED_BRIEFING_IMPORT}
"""
