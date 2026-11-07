"""`lws windows doctor`: is what Windows-hosted projects depend on in place on this machine
(docs/architecture.md, "Windows-hosted projects")? Read-only: each check reports, with a hint, and
changes nothing (REQ-000000514).

- the `lws.cmd` shim on the Windows PATH, its sh twin `lws` beside it (Git for Windows hooks), both
  forwarding the git hook variables;
- a Windows Python that really runs (the generated hooks' dispatcher needs one natively);
- `core.autocrlf=false` in every Windows-hosted project's checkout;
- mirrored networking in `%USERPROFILE%\\.wslconfig`, so a Windows process reaches WSL's 127.0.0.1.
"""

from __future__ import annotations

import configparser
from pathlib import Path

from common import windows
from common.shell import CommandRunner, DEFAULT_RUNNER

FORWARDED = ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", windows.FROM_WINDOWS_ENV)


def _check(name: str, ok: bool, detail: str, hint: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail, "hint": "" if ok else hint}


def _cmd(runner: CommandRunner, line: str):
    # From a folder on the Windows drive: cmd.exe refuses a \\wsl.localhost working directory.
    return runner.run([windows.windows_exe("cmd.exe"), "/d", "/c", line], timeout=30, cwd=windows.mount_root() + "c")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def shim_checks(runner: CommandRunner) -> list[dict]:
    hint = "run `lws windows install-shim` in WSL, then open a new Windows terminal"
    found = _cmd(runner, "where lws.cmd")
    lines = [line.strip() for line in found.stdout.splitlines() if line.strip()] if found.ok else []
    if not lines:
        return [_check("windows_shim", False, "lws.cmd is not on the Windows PATH", hint)]
    root = windows.mount_root()
    cmd_path = Path(windows.to_wsl_path(lines[0], root))
    checks = []
    for name, path in (("windows_shim", cmd_path), ("windows_sh_shim", cmd_path.with_name("lws"))):
        text = _read(path)
        missing = [var for var in FORWARDED if text is not None and var not in text]
        ok = text is not None and not missing
        detail = (f"{windows.to_windows_path(path, root=root)}" if ok else
                  f"{windows.to_windows_path(path, root=root)} is missing" if text is None else
                  f"{windows.to_windows_path(path, root=root)} does not forward {', '.join(missing)}")
        checks.append(_check(name, ok, detail, hint))
    return checks


def python_check(runner: CommandRunner) -> dict:
    for label, line in (("py -3", "py -3 -c \"import sys\""), ("python3", "python3 -c \"import sys\""),
                        ("python", "python -c \"import sys\"")):
        if _cmd(runner, line).ok:
            return _check("windows_python", True, f"`{label}` runs")
    return _check("windows_python", False, "no Windows Python runs (python3 may be the Microsoft Store stub)",
                  "install Python 3 for Windows from python.org with the py launcher: the guard hook refuses tool calls in native sessions without it")


def autocrlf_checks(runner: CommandRunner, projects: list[dict]) -> list[dict]:
    checks = []
    for project in projects:
        if not windows.is_windows_project(project) or not project.get("path"):
            continue
        result = runner.run([*windows.git_command(project), "config", "--get", "core.autocrlf"], timeout=30)
        value = result.stdout.strip() or "unset (Windows' git defaults to true)"
        checks.append(_check(f"autocrlf:{project.get('name')}", value == "false", f"core.autocrlf={value}",
                             f"lws project normalize-eol --project-id {project.get('name')}"))
    return checks


def networking_check(runner: CommandRunner) -> dict:
    hint = "add `[wsl2]` `networkingMode=mirrored` to %USERPROFILE%\\.wslconfig, then `wsl --shutdown`"
    profile = _cmd(runner, "echo %USERPROFILE%").stdout.strip()
    if not profile or "%" in profile:
        return _check("wsl_mirrored_networking", False, "could not read %USERPROFILE%", hint)
    path = Path(windows.to_wsl_path(profile, windows.mount_root())) / ".wslconfig"
    parser = configparser.ConfigParser()
    try:
        parser.read_string(_read(path) or "")
    except configparser.Error as exc:
        return _check("wsl_mirrored_networking", False, f"{path} does not parse: {exc}", hint)
    mode = parser.get("wsl2", "networkingMode", fallback="").strip().lower()
    return _check("wsl_mirrored_networking", mode == "mirrored", f"networkingMode={mode or 'unset (NAT)'}", hint)


def run(projects: list[dict], runner: CommandRunner = DEFAULT_RUNNER) -> dict:
    checks = [*shim_checks(runner), python_check(runner), *autocrlf_checks(runner, projects), networking_check(runner)]
    return {"ok": all(check["ok"] for check in checks), "checks": checks}
