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
import uuid
from pathlib import Path
from typing import Callable

from common.windows import from_git_path, git_command, git_path, is_windows_project
from registry import assets_config, claude_templates as ct, gitops, toolkit

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


def default_governance(project_id: str) -> str:
    """The governance a project's files are generated for when the Registry's setting is not at hand: little-workshop
    keeps `full`, every other project `light` (the Registry's default)."""
    return "full" if project_id == PLATFORM_PROJECT else "light"


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
    """`options_for`, with the plugins the checkout's languages call for and those its toolkit lock enables, and
    whether it is a Windows-hosted checkout (hooks that also run natively on Windows)."""
    options = options_for(project_id)
    if is_windows_project(checkout):
        options["windows"] = True
    options["plugins"] = (*options.get("plugins", detect_plugins(checkout)), *toolkit.lock_plugins(checkout))
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
    """A hook entry the generator writes or once wrote: the scope guard, the test-output filter, the live-install
    guard, the retired reminder."""
    return any(path in (hook.get("command") or "") for hook in entry.get("hooks") or []
               for path in (ct.GUARD_PATH, ct.TEST_FILTER_PATH, ct.REMINDER_PATH, ct.LIVE_INSTALL_GUARD_PATH))


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
        kept = [e for e in entries if not is_guard_entry(e) and e not in hooks.get(event, [])]
        if event in hooks or kept:  # an event left only with a retired generated hook is dropped
            hooks[event] = hooks.get(event, []) + kept
    merged = {"permissions": permissions, "hooks": hooks}
    plugins = {**(current.get("enabledPlugins") or {}), **(generated.get("enabledPlugins") or {})}
    if plugins:
        merged["enabledPlugins"] = plugins
    merged.update({k: v for k, v in current.items() if k not in merged})
    return merged


def _generated(checkout: Path, project_id: str, template: str, options: dict, governance: str | None) -> dict[str, str]:
    files = ct.claude_files(project_id, template, governance=governance or default_governance(project_id), **options)
    return {rel: ct.keep_project_section(text, _read(checkout / rel)) for rel, text in files.items()}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def wanted_files(checkout: Path, project_id: str, template: str, governance: str | None = None) -> dict[str, str]:
    options = options_in(checkout, project_id)
    files = _generated(checkout, project_id, template, options, governance)
    try:
        current = json.loads((checkout / ct.SETTINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    files[ct.SETTINGS_PATH] = json.dumps(merged_settings(project_id, template, current, options), indent=2) + "\n"
    if options.get("windows") and template != "unity":  # Unity keeps its template's rules (REQ-000000511)
        attributes = gitops.eol_gitattributes(_read(checkout / ".gitattributes"))
        if attributes is not None:
            files[".gitattributes"] = attributes
    mcp = _mcp_json(checkout, project_id, bool(options.get("windows")))
    if mcp is not None:
        files[".mcp.json"] = mcp
    return files


def _mcp_json(checkout: Path, project_id: str, windows: bool) -> str | None:
    """The checkout's `.mcp.json` with its `asset-review` entry made right for its host, or None when it
    declares none (whether a project has the server is `project.configure_assets`' decision)."""
    try:
        data = json.loads((checkout / ".mcp.json").read_text(encoding="utf-8"))
        servers = data["mcpServers"]
        if assets_config.MCP_NAME not in servers:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    servers[assets_config.MCP_NAME] = assets_config.mcp_entry(project_id, windows=windows)
    return json.dumps(data, indent=2) + "\n"


def _union_settings(sides: list[dict]) -> dict | None:
    """One settings document holding every entry of each side: permission lists and hook entries joined, other
    keys from the first side that has them."""
    sides = [side for side in sides if isinstance(side, dict)]
    if not sides:
        return None
    union: dict = {"permissions": {}, "hooks": {}}
    for side in sides:
        for group, joined in (("permissions", union["permissions"]), ("hooks", union["hooks"])):
            for key, entries in (side.get(group) or {}).items():
                if isinstance(entries, list):
                    joined.setdefault(key, [])
                    joined[key] += [e for e in entries if e not in joined[key]]
        for key, value in side.items():
            union.setdefault(key, value)
    return union


def regenerated(checkout: Path, project_id: str, settings_sides: list[dict] | None = None,
                governance: str | None = None) -> dict:
    """What each generated Claude file of the checkout should be, for a rebase or merge stopped on them:
    {"files": {path: text}, "retired": [paths]}. `.claude/settings.json` keeps the entries the project
    added on every side given (the two sides of the conflict), merged into the generated table."""
    template = detect_template(checkout)
    options = options_in(checkout, project_id)
    files = _generated(checkout, project_id, template, options, governance)
    current = _union_settings(list(settings_sides or []))
    files[ct.SETTINGS_PATH] = json.dumps(merged_settings(project_id, template, current, options), indent=2) + "\n"
    return {"files": files, "retired": list(ct.RETIRED_FILES)}


def briefing_note(checkout: Path) -> str | None:
    """Why the project's briefing does not load the shared section yet, or None."""
    for rel, token in ((ct.AGENTS_PATH, ct.SHARED_BRIEFING_IMPORT), ("AGENTS.md", "@" + ct.SHARED_BRIEFING_PATH)):
        path = checkout / rel
        if path.is_file():
            if token in path.read_text(encoding="utf-8"):
                return None
            return f"{rel} does not import the shared section: add a line `{token}`"
    return f"no AGENTS.md briefing: start one from claude_templates.agents_md() ending with `{ct.SHARED_BRIEFING_IMPORT}`"


def pending(checkout: Path, project_id: str | None = None, template: str | None = None,
            governance: str | None = None) -> dict:
    """{"project_id", "template", "files": {relative path: text to write, or None to remove}, "changed": ["created x", ...], "note"}:
    what the checkout's generated files should be and which of them differ now."""
    project_id = project_id or detect_project_id(checkout)
    template = template or detect_template(checkout)
    files, changed = {}, []
    for rel, text in wanted_files(checkout, project_id, template, governance).items():
        path = checkout / rel
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            files[rel] = text
            changed.append(f"{'created' if current is None else 'updated'} {rel}")
    for rel in ct.RETIRED_FILES:  # generated once, no longer: removed (None)
        if (checkout / rel).is_file():
            files[rel] = None
            changed.append(f"removed {rel}")
    return {"project_id": project_id, "template": template, "files": files, "changed": changed, "note": briefing_note(checkout)}


def write(checkout: Path, files: dict[str, str | None]) -> None:
    """Write each file; a file whose text is None is retired and removed. A Windows-hosted checkout gets
    `core.autocrlf=false` too (REQ-000000511)."""
    gitops.pin_autocrlf(checkout)
    for rel, text in files.items():
        path = checkout / rel
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


SYNC_BRANCH_NAME = "claude-files-sync"
COMMIT_MESSAGE = "impr (-1): claude-files-sync\n\nThe Claude files little-workshop generates, rewritten from its templates. REQ-NONE."


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([*git_command(cwd), *args], cwd=cwd, capture_output=True, text=True)


def commit_on_branch(checkout: Path, project_id: str, start_branch: Callable[[str, str], dict], name: str,
                     message: str, write_files: Callable[[], list[str]]) -> dict:
    """Start the improvement branch `name` on a clean checkout, let `write_files` change it (returning the paths it
    changed) and commit those paths there: {"written", "branch", "detail"}. The branch is left for the owner to
    finish: this never merges. `start_branch(project_id, name)` asks the Version pillar for the branch and returns
    the settled Job. An error `write_files` raises is the caller's; the branch stays, with whatever it wrote."""
    if _git(["status", "--porcelain"], checkout).stdout.strip():
        return {"written": False, "detail": "skipped: the checkout has uncommitted changes"}
    job = start_branch(project_id, name)
    if job.get("status") != "succeeded":
        return {"written": False, "detail": f"skipped: could not start the branch: {job.get('error') or job.get('status')}"}
    branch = (job.get("result") or {}).get("branch") or f"improvement/{name}"
    paths = write_files()
    _git(["add", "-A", "--", *paths], checkout)
    commit = _git(["commit", "-m", message], checkout)
    if commit.returncode != 0:
        return {"written": True, "branch": branch,
                "detail": f"written on {branch}, not committed: {(commit.stderr or commit.stdout).strip()[-400:]}"}
    return {"written": True, "branch": branch, "detail": f"committed on {branch}; finish it with workshop_version_improvement_finish"}


def integration_branch(checkout: Path) -> str:
    """The project's integration branch as its `.lvx/config.json` names it (`dev` when it names none)."""
    try:
        return json.loads((checkout / ".lvx" / "config.json").read_text(encoding="utf-8")).get("integration_branch") or "dev"
    except (OSError, ValueError, AttributeError):
        return "dev"


def branch_checkouts(checkout: Path) -> dict[str, Path]:
    """Each local branch checked out somewhere (the checkout itself or one of its worktrees), and where."""
    out: dict[str, Path] = {}
    where: Path | None = None
    for line in _git(["worktree", "list", "--porcelain"], checkout).stdout.splitlines():
        if line.startswith("worktree "):
            where = Path(from_git_path(line[len("worktree "):].strip()))
        elif line.startswith("branch refs/heads/") and where is not None:
            out[line[len("branch refs/heads/"):].strip()] = where
    return out


def current_branch(checkout: Path) -> str:
    return _git(["branch", "--show-current"], checkout).stdout.strip()


def read_on_branch(checkout: Path, branch: str, rel: str) -> str | None:
    """A file's text as `branch` has it committed, or None."""
    shown = _git(["show", f"{branch}:{rel}"], checkout)
    return shown.stdout if shown.returncode == 0 else None


def commit_on_integration(checkout: Path, message: str, write_files: Callable[[Path], list[str]], *,
                          area: str | tuple[str, ...] = ".claude") -> dict:
    """Commit straight onto the project's integration branch, without a branch start or finish (REQ-000000533):
    `write_files(where)` changes the files of a checkout of that branch and returns the paths it changed, and only
    those paths are committed, in one commit. Where the branch is checked out (the project's checkout or one of its
    worktrees) the files are written there; otherwise in a temporary worktree of the branch, removed afterwards, so
    a checkout on another branch is never switched or touched. Refused while `area` has uncommitted changes in that
    checkout; when `write_files` raises, `area` is put back as it was and the error is the caller's.
    Returns {"written", "branch", "commit", "detail"}."""
    areas = (area,) if isinstance(area, str) else tuple(area)
    shown = ", ".join(a + ("/" if "." not in a.lstrip(".") else "") for a in areas)
    branch = integration_branch(checkout)
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], checkout).returncode != 0:
        return {"written": False, "detail": f"skipped: the project has no {branch} branch"}
    where = branch_checkouts(checkout).get(branch)
    temporary: Path | None = None
    if where is None:
        common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], checkout).stdout.strip()
        temporary = Path(from_git_path(common)) / "lws-worktrees" / f"{branch}-{uuid.uuid4().hex[:8]}"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        added = _git(["worktree", "add", git_path(temporary, checkout), branch], checkout)
        if added.returncode != 0:
            return {"written": False, "detail": f"skipped: could not check out {branch}: {(added.stderr or added.stdout).strip()[-300:]}"}
        where = temporary
    try:
        if _git(["status", "--porcelain", "--", *areas], where).stdout.strip():
            return {"written": False, "detail": f"skipped: {branch} has uncommitted changes in {shown} in {where}"}

        def put_back() -> None:
            for one in areas:  # one by one: a path HEAD lacks would fail the others too
                _git(["checkout", "HEAD", "--", one], where)
            _git(["clean", "-fdq", "--", *areas], where)
        try:
            paths = write_files(where)
        except BaseException:
            put_back()
            raise
        if not paths:
            return {"written": False, "branch": branch, "detail": "nothing to commit"}
        # Forced: these are exactly the files the change wrote (the toolkit's lock records them), committed even
        # where the project's .gitignore matches their folder (a skill's `scripts/data/` under a `data/` rule).
        _git(["add", "-A", "-f", "--", *paths], where)
        commit = _git(["commit", "-m", message, "--", *paths], where)
        if commit.returncode != 0:
            _git(["reset", "-q", "--", *paths], where)
            put_back()
            return {"written": False, "branch": branch,
                    "detail": f"not committed on {branch}, nothing kept: {(commit.stderr or commit.stdout).strip()[-400:]}"}
        sha = _git(["rev-parse", "HEAD"], where).stdout.strip()
        note = "" if temporary is None else f" (the checkout is on another branch: {branch} has it, the checkout gets it when it takes {branch} in)"
        return {"written": True, "branch": branch, "commit": sha, "detail": f"committed on {branch} as {sha[:12]}{note}"}
    finally:
        if temporary is not None:
            _git(["worktree", "remove", "--force", git_path(temporary, checkout)], checkout)


def sync_on_branch(checkout: Path, project_id: str, start_branch: Callable[[str, str], dict],
                   governance: str | None = None) -> dict:
    """Write a project's pending Claude files on an improvement branch of their own and commit them there."""
    report = pending(checkout, project_id, governance=governance)
    result = {"project_id": project_id, "changed": report["changed"], "note": report["note"], "written": False}
    if not report["changed"]:
        return {**result, "detail": "in sync"}

    def write_files() -> list[str]:
        write(checkout, report["files"])
        return list(report["files"])

    return {**result, **commit_on_branch(checkout, project_id, start_branch, SYNC_BRANCH_NAME, COMMIT_MESSAGE, write_files)}
