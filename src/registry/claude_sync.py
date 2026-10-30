"""Bringing a project's generated Claude Code files in line with `claude_templates.py`: what differs
in a checkout, and writing it (docs/pillars/registry_portfolio.md, "Claude files sync").

`.claude/settings.json` is merged, not replaced: the generated permission table and guard hook come
first, and every entry the project added beside them (its own allows, Read denies, extra hooks, other
settings) is kept. The project's own AGENTS.md is never touched. `tools/sync_claude_files.py` is the
command line over this module; the `registry.sync_claude_files` Job is the same thing for every
registered project, each on a branch of its own."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from registry import claude_templates as ct

PLATFORM_PROJECT = "little-workshop"


def detect_template(checkout: Path) -> str:
    if (checkout / "ProjectSettings" / "ProjectVersion.txt").is_file():
        return "unity"
    if (checkout / "pyproject.toml").is_file():
        return "python"
    return "bazel"


def detect_project_id(checkout: Path) -> str:
    """The scope guard's argument in the checkout's settings, else the directory name."""
    try:
        settings = json.loads((checkout / ct.SETTINGS_PATH).read_text(encoding="utf-8"))
        for entry in (settings.get("hooks") or {}).get("PreToolUse") or []:
            for hook in entry.get("hooks") or []:
                match = re.search(r"lws_scope_guard\.py\s+(\S+)", hook.get("command", ""))
                if match:
                    return match.group(1)
    except (OSError, ValueError):
        pass
    return checkout.resolve().name


def options_for(project_id: str) -> dict:
    return dict(ct.OWN_SETTINGS) if project_id == PLATFORM_PROJECT else {}


def detect_plugins(checkout: Path) -> tuple[str, ...]:
    """The code-intelligence plugins of the languages the checkout holds, from its top-level markers."""
    def any_file(*patterns: str) -> bool:
        return any(next(checkout.glob(pattern), None) is not None for pattern in patterns)

    plugins = []
    if (checkout / "pyproject.toml").is_file() or any_file("src/**/*.py"):
        plugins.append("pyright-lsp")
    if any_file("tsconfig.json", "package.json", "src/tsconfig.json", "src/package.json"):
        plugins.append("typescript-lsp")
    if any_file("*.cc", "*.cpp", "src/**/*.cc", "src/**/*.cpp", "src/**/*.h"):
        plugins.append("clangd-lsp")
    if (checkout / "ProjectSettings" / "ProjectVersion.txt").is_file() or any_file("*.sln", "*.csproj"):
        plugins.append("csharp-lsp")
    return tuple(plugins)


def options_in(checkout: Path, project_id: str) -> dict:
    """`options_for`, with the plugins the checkout's languages call for."""
    options = options_for(project_id)
    options.setdefault("plugins", detect_plugins(checkout))
    return options


def generator_owned(project_id: str) -> set[str]:
    """Every permission entry the generator writes for this project, under any template."""
    owned: set[str] = set()
    for template in ct.TEST_BASH:
        for platform in (False, True):
            for entries in ct.settings(project_id, template, platform=platform)["permissions"].values():  # Read denies included
                owned.update(entries)
    owned.update(e for entries in ct.settings(project_id, **ct.OWN_SETTINGS)["permissions"].values() for e in entries)
    owned.update(ct.RETIRED_RULES)
    return owned


def is_generated_entry(entry: str, owned: set[str]) -> bool:
    return entry in owned or entry.startswith(ct.MCP) or entry.startswith("Bash(lws ")


def is_guard_entry(entry: dict) -> bool:
    """A hook entry the generator writes: the scope guard or the test-output filter."""
    return any(path in (hook.get("command") or "") for hook in entry.get("hooks") or []
               for path in (ct.GUARD_PATH, ct.TEST_FILTER_PATH))


def merged_settings(project_id: str, template: str, current: dict | None, options: dict | None = None) -> dict:
    """The generated settings, plus what the project added beside them."""
    generated = ct.settings(project_id, template, **(options_for(project_id) if options is None else options))
    if not current:
        return generated
    owned = generator_owned(project_id)
    permissions = dict(generated["permissions"])
    for key, entries in (current.get("permissions") or {}).items():
        if key not in permissions:
            permissions[key] = entries
            continue
        extra = [e for e in entries if isinstance(e, str) and not is_generated_entry(e, owned)]
        permissions[key] = permissions[key] + [e for e in extra if e not in permissions[key]]
    hooks = dict(generated["hooks"])
    for event, entries in (current.get("hooks") or {}).items():
        if event not in hooks:
            hooks[event] = entries
            continue
        kept = [e for e in entries if not is_guard_entry(e) and e not in hooks[event]]
        hooks[event] = hooks[event] + kept
    merged = {"permissions": permissions, "hooks": hooks}
    plugins = {**(current.get("enabledPlugins") or {}), **(generated.get("enabledPlugins") or {})}
    if plugins:
        merged["enabledPlugins"] = plugins
    merged.update({k: v for k, v in current.items() if k not in merged})
    return merged


def wanted_files(checkout: Path, project_id: str, template: str) -> dict[str, str]:
    options = options_in(checkout, project_id)
    files = ct.claude_files(project_id, template, **options)
    try:
        current = json.loads((checkout / ct.SETTINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    files[ct.SETTINGS_PATH] = json.dumps(merged_settings(project_id, template, current, options), indent=2) + "\n"
    return files


def briefing_note(checkout: Path) -> str | None:
    """Why the project's briefing does not load the shared section yet, or None."""
    for rel, token in ((ct.AGENTS_PATH, ct.SHARED_BRIEFING_IMPORT), ("AGENTS.md", "@" + ct.SHARED_BRIEFING_PATH)):
        path = checkout / rel
        if path.is_file():
            if token in path.read_text(encoding="utf-8"):
                return None
            return f"{rel} does not import the shared section: add a line `{token}`"
    return f"no AGENTS.md briefing: start one from claude_templates.agents_md() ending with `{ct.SHARED_BRIEFING_IMPORT}`"


def pending(checkout: Path, project_id: str | None = None, template: str | None = None) -> dict:
    """{"project_id", "template", "files": {relative path: text to write}, "changed": ["created x", ...], "note"}:
    what the checkout's generated files should be and which of them differ now."""
    project_id = project_id or detect_project_id(checkout)
    template = template or detect_template(checkout)
    files, changed = {}, []
    for rel, text in wanted_files(checkout, project_id, template).items():
        path = checkout / rel
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            files[rel] = text
            changed.append(f"{'created' if current is None else 'updated'} {rel}")
    return {"project_id": project_id, "template": template, "files": files, "changed": changed, "note": briefing_note(checkout)}


def write(checkout: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = checkout / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


SYNC_BRANCH_NAME = "claude-files-sync"
COMMIT_MESSAGE = "impr (-1): claude-files-sync\n\nThe Claude files little-workshop generates, rewritten from its templates. REQ-NONE."


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def sync_on_branch(checkout: Path, project_id: str, start_branch: Callable[[str, str], dict]) -> dict:
    """Write a project's pending Claude files on an improvement branch of their own and commit them there.
    The branch is left for the owner to finish: this never merges. `start_branch(project_id, name)`
    asks the Version pillar for the branch and returns the settled Job."""
    report = pending(checkout, project_id)
    result = {"project_id": project_id, "changed": report["changed"], "note": report["note"], "written": False}
    if not report["changed"]:
        return {**result, "detail": "in sync"}
    if _git(["status", "--porcelain"], checkout).stdout.strip():
        return {**result, "detail": "skipped: the checkout has uncommitted changes"}
    job = start_branch(project_id, SYNC_BRANCH_NAME)
    if job.get("status") != "succeeded":
        return {**result, "detail": f"skipped: could not start the branch: {job.get('error') or job.get('status')}"}
    branch = (job.get("result") or {}).get("branch") or f"improvement/{SYNC_BRANCH_NAME}"
    write(checkout, report["files"])
    _git(["add", "--", *report["files"]], checkout)
    commit = _git(["commit", "-m", COMMIT_MESSAGE], checkout)
    if commit.returncode != 0:
        return {**result, "written": True, "branch": branch,
                "detail": f"written on {branch}, not committed: {(commit.stderr or commit.stdout).strip()[-400:]}"}
    return {**result, "written": True, "branch": branch, "detail": f"committed on {branch}; finish it with workshop_version_improvement_finish"}
