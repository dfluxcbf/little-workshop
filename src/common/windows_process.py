"""Running a native Windows process from WSL, for a Windows-hosted project (`common/windows.py`'s
`is_windows_checkout`) whose Claude Remote Control session or deployed server needs to run as real
Windows process — GPU-backed tools, a project built against its own `.venv-win`, anything that behaves
differently (or not at all) under WSL2's own Linux kernel — rather than as a Linux process in a WSL
worktree, which is all `agent/session_flow.py` and `deployment/flow.py` could do before this module
existed.

The two Linux mechanisms the rest of this project leans on for exactly this (`dtach` keeping a session
alive detached from its launcher; `systemd --user` supervising a server, restarting it on failure) have
no Windows port, so this module gives each a from-scratch equivalent instead:

- `start_background`: a detached, hidden Windows process (PowerShell's own `Start-Process -PassThru`,
  which resolves a `.cmd`/`.bat` wrapper — Claude Code's own Windows install is one — the way an
  interactive shell would) returning its real Windows PID. The caller persists that PID (a plain text
  file is enough; there is no Windows unit to query later) and uses `is_alive`/`stop` on it from then on.
  The process outlives this call, this WSL session, and this pillar's own unit restarting, the same
  guarantee `dtach`+a transient systemd scope gives a WSL-hosted session.
- `start_server`: the same, for a manifest's free-form shell `command` line (the Windows-side
  counterpart of `bash -c`, so a Windows-targeting manifest is written in PowerShell, not bash) — wraps
  it as a `powershell.exe -EncodedCommand` child so the command's own syntax (pipes, `&&`, env
  assignments) is PowerShell's to parse, not `Start-Process`'s fixed FilePath+ArgumentList shape. A
  command written to block in the foreground (every existing manifest already assumes this, for
  `bash -c`) keeps this wrapper alive for exactly as long as the real server runs.
- `is_alive` / `stop`: `tasklist.exe` / `taskkill.exe /T` (tree-kill, since the actual server is a
  *child* of `start_server`'s powershell.exe wrapper) called directly — no scripting needed for either.
- Restart-on-failure has no Windows equivalent to delegate to either: `deployment/flow.py`'s own
  `WindowsServerSupervisor` polls `is_alive` and calls `start_server` again instead, little-workshop
  standing in for what `systemd --user`'s `Restart=on-failure` already does for a WSL-hosted server.

Every Windows-side call goes through `CommandRunner` like every other subprocess in this project
(`common/shell.py`), so it is fully fakeable in tests — none of this has ever been run against a real
Windows machine.
"""

from __future__ import annotations

import base64

from common.shell import CommandResult, CommandRunner

POWERSHELL = "powershell.exe"


class WindowsProcessError(RuntimeError):
    pass


def _ps_literal(value: str) -> str:
    """`value` as a PowerShell single-quoted string literal (only a literal single quote needs
    escaping, by doubling it) — every value this module embeds in a generated script goes through
    this, never raw interpolation."""
    return "'" + value.replace("'", "''") + "'"


def _encoded_command(script: str) -> str:
    """`-EncodedCommand`'s own wire format (base64 of UTF-16LE text), which sidesteps every
    cmd.exe/PowerShell quoting hazard a literal `-Command "..."` crossing the WSL/Windows boundary
    would hit — the whole script is one opaque argv token."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_powershell(runner: CommandRunner, script: str, *, timeout: float) -> CommandResult:
    return runner.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded_command(script)],
                       timeout=timeout)


def _start_process(runner: CommandRunner, file_name: str, args: list[str], cwd: str,
                    env: dict[str, str] | None, *, timeout: float) -> int:
    lines = [f"$env:{name} = {_ps_literal(value)}" for name, value in (env or {}).items()]
    arg_list = ", ".join(_ps_literal(a) for a in args)
    lines.append(
        f"$p = Start-Process -FilePath {_ps_literal(file_name)} -ArgumentList @({arg_list}) "
        f"-WorkingDirectory {_ps_literal(cwd)} -WindowStyle Hidden -PassThru"
    )
    lines.append("Write-Output $p.Id")
    result = _run_powershell(runner, "; ".join(lines), timeout=timeout)
    if not result.ok:
        raise WindowsProcessError(f"could not start {file_name!r} on Windows: {(result.stderr or result.stdout).strip()}")
    reported = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not reported.isdigit():
        raise WindowsProcessError(f"Windows process start did not report a PID: {result.stdout!r} {result.stderr!r}")
    return int(reported)


def start_background(runner: CommandRunner, command: str, args: list[str], cwd: str,
                      env: dict[str, str] | None = None, *, timeout: float = 15.0) -> int:
    """Starts `command args...` as a detached, hidden Windows process (a session's own
    `claude --remote-control ...`) and returns its Windows PID."""
    return _start_process(runner, command, args, cwd, env, timeout=timeout)


def start_server(runner: CommandRunner, command_line: str, cwd: str, env: dict[str, str] | None = None,
                  *, timeout: float = 15.0) -> int:
    """Starts `command_line` (a manifest's own free-form shell command, PowerShell syntax on this
    side) detached and hidden, and returns the Windows PID of the `powershell.exe` wrapper running it —
    which stays alive for exactly as long as `command_line` itself blocks in the foreground, the same
    assumption every manifest's `command` already makes for `bash -c` on the WSL side."""
    return _start_process(runner, POWERSHELL, ["-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded_command(command_line)],
                           cwd, env, timeout=timeout)


def run_foreground(runner: CommandRunner, command: str, args: list[str], cwd: str, *, timeout: float = 3600.0) -> CommandResult:
    """Runs `command args...` as a native Windows process, waiting for it to finish — a manifest's
    own `install` step, resolved and run the way an interactive shell would (a `.cmd`/`.bat` included),
    mirroring `self.runner.run(manifest.install, cwd=worktree)` on the WSL side."""
    arg_literals = " ".join(_ps_literal(a) for a in args)
    script = f"Set-Location {_ps_literal(cwd)}; & {_ps_literal(command)} {arg_literals}; exit $LASTEXITCODE"
    return _run_powershell(runner, script, timeout=timeout)


def is_alive(runner: CommandRunner, pid: int, *, timeout: float = 10.0) -> bool:
    """Whether a Windows process with this PID still exists (`tasklist.exe`, called directly —
    WSL/Windows interop resolves it on the Windows System32 PATH entry, the same way `cmd.exe` already
    is elsewhere in this project)."""
    result = runner.run(["tasklist.exe", "/FI", f"PID eq {pid}", "/NH"], timeout=timeout)
    if not result.ok:
        return False
    return any(str(pid) in line.split() for line in result.stdout.splitlines())


def stop(runner: CommandRunner, pid: int, *, timeout: float = 10.0) -> None:
    """Tree-kills a Windows process (`/T`): `start_server`'s own PID is its `powershell.exe` wrapper,
    and the real server is that wrapper's *child* — killing just the wrapper would orphan it still
    running. A PID that is already gone is not an error (`/F` alone would be; `taskkill` on a missing
    PID exits non-zero either way, so the caller checks `is_alive` first rather than this return
    value)."""
    runner.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], timeout=timeout)
