"""Windows-hosted projects: a checkout on a Windows drive (`/mnt/c/...` from WSL) whose own code runs on Windows
(a Unity editor script, a Windows service, a `.bat`) and reaches little-workshop from there. little-workshop
itself stays in WSL; this module is the WSL side of the bridge (docs/architecture.md, "Windows-hosted projects"):

- path translation between the two sides, done in Python rather than with `wslpath`, so it is testable and
  needs no subprocess;
- the `lws.cmd` shim that runs WSL's `lws` from Windows (`lws windows install-shim`), and the argument
  translation `lws` applies when the shim started it (`LWS_FROM_WINDOWS`).
"""

from __future__ import annotations

import configparser
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

FROM_WINDOWS_ENV = "LWS_FROM_WINDOWS"
DEFAULT_MOUNT_ROOT = "/mnt/"
WSL_CONF = Path("/etc/wsl.conf")

_DRIVE_PATH = re.compile(r"^([A-Za-z]):(?:[\\/]|$)")
_UNC_WSL = re.compile(r"^[\\/]{2}(?:wsl\.localhost|wsl\$)[\\/]([^\\/]+)(.*)$", re.IGNORECASE)
_OPTION_VALUE = re.compile(r"^(--[A-Za-z0-9][A-Za-z0-9-]*=)(.*)$")


def mount_root(wsl_conf: Path = WSL_CONF) -> str:
    """Where WSL mounts the Windows drives (`[automount] root` in /etc/wsl.conf), with a trailing slash."""
    parser = configparser.ConfigParser()
    try:
        parser.read(wsl_conf, encoding="utf-8")
        root = parser.get("automount", "root", fallback=DEFAULT_MOUNT_ROOT).strip().strip('"') or DEFAULT_MOUNT_ROOT
    except (OSError, configparser.Error):
        root = DEFAULT_MOUNT_ROOT
    return root if root.endswith("/") else root + "/"


def is_windows_path(value: str) -> bool:
    """A Windows absolute path: `C:\\x`, `C:/x`, `C:` alone, or a `\\\\wsl.localhost\\<distro>\\...` share."""
    return bool(_DRIVE_PATH.match(value) or _UNC_WSL.match(value))


def to_wsl_path(value: str, root: str | None = None) -> str:
    """`C:\\Users\\x` -> `/mnt/c/Users/x`; `\\\\wsl.localhost\\Ubuntu\\home\\x` -> `/home/x`. Anything else is
    returned unchanged. `root`: the mount root, /etc/wsl.conf's by default."""
    root = mount_root() if root is None else root
    unc = _UNC_WSL.match(value)
    if unc:
        rest = unc.group(2).replace("\\", "/")
        return str(PurePosixPath("/" + rest.lstrip("/")))
    drive = _DRIVE_PATH.match(value)
    if not drive:
        return value
    parts = PureWindowsPath(value).parts[1:]
    return str(PurePosixPath(root, drive.group(1).lower(), *parts))


def to_windows_path(path: str | Path, distro: str | None = None, root: str | None = None) -> str:
    """`/mnt/c/x` -> `C:\\x`; any other absolute WSL path -> `\\\\wsl.localhost\\<distro>\\...`. `root`: the
    mount root, /etc/wsl.conf's by default."""
    root = mount_root() if root is None else root
    posix = PurePosixPath(str(path))
    root_parts = PurePosixPath(root).parts
    parts = posix.parts
    if parts[:len(root_parts)] == root_parts and len(parts) > len(root_parts) and len(parts[len(root_parts)]) == 1:
        drive = parts[len(root_parts)].upper() + ":\\"
        return str(PureWindowsPath(drive, *parts[len(root_parts) + 1:]))
    distro = distro or os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
    return str(PureWindowsPath(f"\\\\wsl.localhost\\{distro}\\", *parts[1:]))


def is_windows_checkout(path: str | Path, root: str | None = None) -> bool:
    """Whether a checkout lives on a Windows drive (`<mount root>/<letter>/...`, the mount root from
    /etc/wsl.conf), so Windows code may run in it."""
    root = mount_root() if root is None else root
    root_parts = PurePosixPath(root).parts
    parts = PurePosixPath(str(path)).parts
    return parts[:len(root_parts)] == root_parts and len(parts) > len(root_parts) + 1 \
        and len(parts[len(root_parts)]) == 1 and parts[len(root_parts)].isalpha()


WSL, WINDOWS = "wsl", "windows"
HOSTS = (WSL, WINDOWS)


def _project_path(project) -> str | None:
    if isinstance(project, dict):
        return project.get("path")
    return getattr(project, "path", None)


def project_host(project, root: str | None = None) -> str:
    """Where a project's code and tools run: `windows` for a checkout on a Windows drive, `wsl` otherwise.
    `project` is a registry row (a dict or a `Project`), whose explicit `host` wins, or a checkout path."""
    if isinstance(project, (str, Path)):
        return WINDOWS if is_windows_checkout(project, root) else WSL
    host = project.get("host") if isinstance(project, dict) else getattr(project, "host", None)
    if host in HOSTS:
        return host
    path = _project_path(project)
    return WINDOWS if path and is_windows_checkout(path, root) else WSL


def is_windows_project(project, root: str | None = None) -> bool:
    return project_host(project, root) == WINDOWS


GIT_EXE = "git.exe"


def git_command(project, root: str | None = None) -> list[str]:
    """The git to run for a project (a registry row or a checkout path), up to its subcommand: Windows' own
    `git.exe -C <C:\\checkout>` for a Windows-hosted one (one git per checkout: fast on NTFS, and the same
    line-ending and index view as the user's Windows tools), plain `git` otherwise."""
    if not is_windows_project(project, root):
        return ["git"]
    path = project if isinstance(project, (str, Path)) else _project_path(project)
    root = mount_root() if root is None else root
    return [GIT_EXE, "-C", to_windows_path(path, root=root)]


def git_path(path: str | Path, project, root: str | None = None) -> str:
    """A path argument for `git_command(project)`: a Windows path when that git is `git.exe`."""
    if not is_windows_project(project, root):
        return str(path)
    return to_windows_path(path, root=mount_root() if root is None else root)


def from_git_path(value: str, root: str | None = None) -> str:
    """A path git printed (`C:/x` from git.exe) as this side's path; a WSL path is returned unchanged."""
    return to_wsl_path(value, mount_root() if root is None else root) if is_windows_path(value) else value


def translate_argv(argv: list[str], root: str = DEFAULT_MOUNT_ROOT) -> list[str]:
    """`lws`'s arguments as the shim passed them, with every Windows path (a whole argument or an
    `--option=value`'s value) turned into its WSL path."""
    out = []
    for arg in argv:
        option = _OPTION_VALUE.match(arg)
        if option and is_windows_path(option.group(2)):
            out.append(option.group(1) + to_wsl_path(option.group(2), root))
        else:
            out.append(to_wsl_path(arg, root) if is_windows_path(arg) else arg)
    return out


def windows_claude_configs(root: str = DEFAULT_MOUNT_ROOT) -> list[Path]:
    """Every Windows user's own `.claude.json`, reachable under the WSL mount root across any drive
    letter — Claude Code running natively on Windows (not through WSL) keeps its own trust store there,
    keyed by Windows-style paths (`C:/...`), separate from this side's `~/.claude.json`."""
    return sorted(Path(p) for p in Path(root).glob("*/Users/*/.claude.json"))


def project_for_cwd(cwd: str | Path, projects: list[dict]) -> str | None:
    """The registered project whose checkout holds `cwd` (the deepest one when checkouts nest)."""
    here = PurePosixPath(str(cwd))
    best: tuple[int, str] | None = None
    for project in projects:
        path, name = project.get("path"), project.get("name")
        if not path or not name:
            continue
        checkout = PurePosixPath(path)
        if here == checkout or checkout in here.parents:
            depth = len(checkout.parts)
            if best is None or depth > best[0]:
                best = (depth, name)
    return best[1] if best else None


# The environment `lws` reads from the Windows side. WSLENV carries a variable across `wsl.exe` (`/p`
# translates a path list); GIT_* let `lws` run inside a Windows Git hook against the same repository.
SHIM_WSLENV = f"{FROM_WINDOWS_ENV}:GIT_DIR/p:GIT_WORK_TREE/p:GIT_INDEX_FILE/p"


def lws_cmd(distro: str, lws_path: str) -> str:
    """`lws.cmd`: runs WSL's `lws` with the caller's folder as its working directory and its exit code as the
    shim's. `\\.` after %CD% keeps a drive root (`C:\\`) from escaping the closing quote."""
    return f"""@echo off
rem Installed by `lws windows install-shim` (little-workshop, src/common/windows.py). Runs WSL's lws from
rem Windows: same arguments (Windows paths are translated), the current folder as its working directory,
rem and --project-id defaulting to the registered project holding that folder. Reinstall to update.
setlocal
set "{FROM_WINDOWS_ENV}=1"
if defined WSLENV (set "WSLENV={SHIM_WSLENV}:%WSLENV%") else (set "WSLENV={SHIM_WSLENV}")
wsl.exe -d {distro} --cd "%CD%\\." -e {lws_path} %*
exit /b %ERRORLEVEL%
"""


def lws_sh(distro: str, lws_path: str) -> str:
    """`lws`, the sh twin of `lws.cmd` installed beside it: what a Git for Windows hook (`.githooks/commit-msg`
    run by git.exe, in Git Bash) finds on PATH as `lws`. `pwd -W` is Git Bash's Windows form of the folder."""
    return f"""#!/bin/sh
# Installed by `lws windows install-shim` (little-workshop, src/common/windows.py). Runs WSL's lws from
# Git Bash (a Windows git hook): same arguments, the current folder as its working directory, and the
# hook's GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE carried across. Reinstall to update.
export {FROM_WINDOWS_ENV}=1
export WSLENV="{SHIM_WSLENV}${{WSLENV:+:$WSLENV}}"
exec wsl.exe -d {distro} --cd "$(pwd -W 2>/dev/null || pwd)" -e {lws_path} "$@"
"""


def shim_problems(distro: str, lws_path: str) -> list[str]:
    """Why these values cannot go into `lws.cmd` unquoted (cmd metacharacters, spaces), if any."""
    problems = []
    for label, value in (("distro", distro), ("lws path", lws_path)):
        if not value:
            problems.append(f"no {label} found")
        elif re.search(r'[\s"&|<>^%()]', value):
            problems.append(f"the {label} {value!r} holds a space or a cmd metacharacter")
    return problems
