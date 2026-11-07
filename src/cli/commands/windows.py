"""`lws` commands of Windows-hosted projects."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def _windows_local_appdata() -> str:
    out = subprocess.run(["cmd.exe", "/d", "/c", "echo %LOCALAPPDATA%"], capture_output=True, text=True,
                         timeout=30, cwd="/mnt/c" if Path("/mnt/c").is_dir() else None)
    value = out.stdout.strip()
    if out.returncode != 0 or not value or "%" in value:
        raise RuntimeError(f"could not read %LOCALAPPDATA% through cmd.exe: {out.stderr.strip() or value!r}")
    return value


_ADD_TO_USER_PATH = (
    "$dir = $env:LWS_SHIM_DIR; $path = [Environment]::GetEnvironmentVariable('Path', 'User'); "
    "$parts = @($path -split ';' | Where-Object { $_ }); "
    "if ($parts -contains $dir) { 'present' } else { "
    "[Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'User'); 'added' }"
)


def cmd_windows_install_shim(args: argparse.Namespace) -> int:
    """Install `lws.cmd`, and its sh twin `lws` for Git for Windows hooks, on the Windows side so a
    Windows-hosted project's own code and git hooks can run `lws` (docs/architecture.md, "Windows-hosted
    projects")."""
    import shutil

    from common import windows

    distro = os.environ.get("WSL_DISTRO_NAME", "")
    lws_path = args.lws_path or shutil.which("lws") or ""
    problems = windows.shim_problems(distro, lws_path)
    if not distro:
        problems.insert(0, "WSL_DISTRO_NAME is not set: run this inside WSL")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    try:
        win_dir = args.dir or _windows_local_appdata() + "\\little-workshop\\bin"
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}; pass --dir", file=sys.stderr)
        return 1
    root = windows.mount_root()
    if win_dir.startswith(root):  # given as a WSL path (or translated by the shim itself)
        win_dir = windows.to_windows_path(win_dir, distro, root)
    target_dir = Path(windows.to_wsl_path(win_dir, root))
    content = windows.lws_cmd(distro, lws_path).replace("\n", "\r\n")
    sh_content = windows.lws_sh(distro, lws_path)
    result = {"shim": win_dir.rstrip("\\") + "\\lws.cmd", "sh_shim": win_dir.rstrip("\\") + "\\lws",
              "distro": distro, "lws": lws_path}
    if args.dry_run:
        _print({**result, "dry_run": True, "content": content.replace("\r\n", "\n"), "sh_content": sh_content})
        return 0
    if not windows.is_windows_path(win_dir):
        print(f"error: {win_dir!r} is not a Windows path (C:\\...)", file=sys.stderr)
        return 1
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "lws.cmd").write_bytes(content.encode("utf-8"))
    (target_dir / "lws").write_bytes(sh_content.encode("utf-8"))
    result["user_path"] = "unchanged (--no-path)"
    if not args.no_path:
        env = {**os.environ, "LWS_SHIM_DIR": win_dir, "WSLENV": "LWS_SHIM_DIR"}
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _ADD_TO_USER_PATH],
                             capture_output=True, text=True, timeout=60, env=env)
        state = out.stdout.strip().splitlines()[-1:] or [""]
        if out.returncode != 0 or state[0] not in ("present", "added"):
            print(f"warning: could not add {win_dir} to the user PATH: {out.stderr.strip()}", file=sys.stderr)
            result["user_path"] = "not changed: add the folder yourself"
        else:
            result["user_path"] = "already listed" if state[0] == "present" else "added (open a new terminal)"
    _print(result)
    return 0


def cmd_windows_doctor(args: argparse.Namespace) -> int:
    """What Windows-hosted projects depend on on this machine, checked and reported (common/windows_doctor.py)."""
    from common import windows_doctor

    projects = client.list_projects(Paths.from_env()).get("projects", [])
    report = windows_doctor.run(projects)
    _print(report)
    return 0 if report["ok"] else 1


def register(subparsers) -> None:
    windows_cmd = subparsers.add_parser("windows", help="Reach little-workshop from a Windows-hosted project's own code")
    windows_sub = windows_cmd.add_subparsers(dest="windows_command", required=True)
    win_shim = windows_sub.add_parser("install-shim", help="Install lws.cmd and its sh twin lws (for Git for Windows hooks) on Windows, running this lws through wsl.exe, and put them on the user PATH")
    win_shim.add_argument("--dir", help="Windows folder for lws.cmd (default %%LOCALAPPDATA%%\\little-workshop\\bin)")
    win_shim.add_argument("--lws-path", help="The WSL lws the shim runs (default: lws on PATH; pass it when PATH has none)")
    win_shim.add_argument("--no-path", action="store_true", help="Leave the user PATH alone")
    win_shim.add_argument("--dry-run", action="store_true", help="Print the shim and where it would go")
    win_shim.set_defaults(func=cmd_windows_install_shim)
    win_doctor = windows_sub.add_parser("doctor", help="Check the shims, a Windows Python, each Windows-hosted project's core.autocrlf and mirrored networking")
    win_doctor.set_defaults(func=cmd_windows_doctor)
