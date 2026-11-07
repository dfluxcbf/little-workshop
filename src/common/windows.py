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


def to_wsl_path(value: str, root: str = DEFAULT_MOUNT_ROOT) -> str:
    """`C:\\Users\\x` -> `/mnt/c/Users/x`; `\\\\wsl.localhost\\Ubuntu\\home\\x` -> `/home/x`. Anything else is
    returned unchanged."""
    unc = _UNC_WSL.match(value)
    if unc:
        rest = unc.group(2).replace("\\", "/")
        return str(PurePosixPath("/" + rest.lstrip("/")))
    drive = _DRIVE_PATH.match(value)
    if not drive:
        return value
    parts = PureWindowsPath(value).parts[1:]
    return str(PurePosixPath(root, drive.group(1).lower(), *parts))


def to_windows_path(path: str | Path, distro: str | None = None, root: str = DEFAULT_MOUNT_ROOT) -> str:
    """`/mnt/c/x` -> `C:\\x`; any other absolute WSL path -> `\\\\wsl.localhost\\<distro>\\...`."""
    posix = PurePosixPath(str(path))
    root_parts = PurePosixPath(root).parts
    parts = posix.parts
    if parts[:len(root_parts)] == root_parts and len(parts) > len(root_parts) and len(parts[len(root_parts)]) == 1:
        drive = parts[len(root_parts)].upper() + ":\\"
        return str(PureWindowsPath(drive, *parts[len(root_parts) + 1:]))
    distro = distro or os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
    return str(PureWindowsPath(f"\\\\wsl.localhost\\{distro}\\", *parts[1:]))


def is_windows_checkout(path: str | Path, root: str = DEFAULT_MOUNT_ROOT) -> bool:
    """Whether a checkout lives on a Windows drive (`/mnt/<letter>/...`), so Windows code may run in it."""
    root_parts = PurePosixPath(root).parts
    parts = PurePosixPath(str(path)).parts
    return parts[:len(root_parts)] == root_parts and len(parts) > len(root_parts) + 1 \
        and len(parts[len(root_parts)]) == 1 and parts[len(root_parts)].isalpha()


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


def shim_problems(distro: str, lws_path: str) -> list[str]:
    """Why these values cannot go into `lws.cmd` unquoted (cmd metacharacters, spaces), if any."""
    problems = []
    for label, value in (("distro", distro), ("lws path", lws_path)):
        if not value:
            problems.append(f"no {label} found")
        elif re.search(r'[\s"&|<>^%()]', value):
            problems.append(f"the {label} {value!r} holds a space or a cmd metacharacter")
    return problems
