"""Ad-hoc, human-driven Claude Code Remote Control sessions — M16
`little-runner` parity: `lrun session start/stop/list`.

Distinct from `agent.run_task`'s own headless `-p`-mode Remote Control
usage (`flow.py`'s `_invoke_claude`): an interactive
`claude --remote-control` session needs a real PTY to attach to.
`dtach` gives it one and keeps it alive independent of whatever
launched it, so a session survives this pillar's own unit restarting.

A transient `systemd --user` *scope* (not a *service*) tracks cgroup
population rather than one main PID, so it survives `dtach`'s own
self-detach dance and keeps the session in its own independent cgroup
no matter which unit, including this pillar's own, gets restarted
afterward — ported directly from `little-runner`'s own hard-earned
finding: a plain `dtach -n` forks away from its launcher in the process
tree, but the fork still inherits the launcher's *cgroup* regardless,
so without a scope of its own the session would die the moment this
pillar redeployed itself.

Exactly one session per project: the dtach socket path is deterministic
per project (`Paths.session_socket_path`), so "is it running" and "stop
it" both reduce to matching that path in the process list.

A Windows-hosted project (`common/windows.py`'s `is_windows_checkout`,
docs/architecture.md's "Windows-hosted projects") has no WSL process to
`dtach`/`systemd-run` at all — its session runs as a real Windows
process instead (`common/windows_process.py`'s `start_background`),
tracked by a plain PID file (`Paths.session_windows_pid_path`) rather
than a process-list match, since there is no Windows unit to query.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent import usage_client
from agent.launch import JOB_SESSION, launch_for
from agent.claude_invoke import model_args, setenv_args
from common import playwright, windows, windows_process
from common.job_steps import step
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.systemd import user_path_env


class SessionError(RuntimeError):
    pass


def claude_config_path() -> Path:
    # Overridable for tests that launch this pillar as a real, separate
    # subprocess (a real `systemd --user` unit) and so can't reach this
    # process's own monkeypatch — never for real use, where the actual
    # `~/.claude.json` is exactly what a real trust-dialog check needs.
    override = os.environ.get("LITTLE_WORKSHOP_CLAUDE_CONFIG_PATH")
    return Path(override) if override else Path.home() / ".claude.json"


def _trust_accepted(config_path: Path, matches) -> bool:
    try:
        data = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return any(matches(key) and isinstance(entry, dict) and entry.get("hasTrustDialogAccepted")
               for key, entry in data.get("projects", {}).items())


def is_trusted(project_dir: Path) -> bool:
    """Whether Claude Code's own workspace-trust dialog has already been
    accepted for this directory. A session launched via `dtach -n` runs
    fully detached — if the folder isn't trusted yet, `claude
    --remote-control` sits at the trust prompt forever with no one there
    to answer it, and `is_running` would otherwise call it "running"
    even though it never reaches Remote Control.

    A Windows-hosted checkout (docs/architecture.md, "Windows-hosted
    projects") may instead have been trusted through Claude Code running
    natively on Windows, which keeps its own `.claude.json` on the
    Windows side, keyed by that project's Windows-style path — this
    side's own `~/.claude.json` never sees it."""
    posix_key = str(project_dir)
    if _trust_accepted(claude_config_path(), lambda key: key == posix_key):
        return True
    if not windows.is_windows_checkout(project_dir):
        return False
    target = windows.to_windows_path(project_dir).replace("\\", "/").lower()
    return any(_trust_accepted(config_path, lambda key: key.replace("\\", "/").lower() == target)
               for config_path in windows.windows_claude_configs())


def session_name(project_id: str) -> str:
    return f"lws-{project_id.split('/')[-1]}-session"


def _match(paths: Paths, project_id: str) -> str:
    return f"dtach -n {paths.session_socket_path(project_id)}"


def _unit_name(project_id: str) -> str:
    return f"little-workshop-agent-session-{project_id.replace('/', '-')}"


class SessionFlow:
    def __init__(self, paths: Paths, runner: CommandRunner = DEFAULT_RUNNER, *, playwright_mcp: bool = True):
        self.paths = paths
        self.runner = runner
        self.playwright_mcp = playwright_mcp

    def is_running(self, project_id: str) -> bool:
        winpid_path = self.paths.session_windows_pid_path(project_id)
        if winpid_path.exists():
            pid = int(winpid_path.read_text().strip())
            if windows_process.is_alive(self.runner, pid):
                return True
            winpid_path.unlink(missing_ok=True)  # the Windows process died without us noticing
            return False
        return self.runner.run(["pgrep", "-f", _match(self.paths, project_id)]).ok

    def start(self, project_id: str, project_dir: Path) -> str:
        on_windows = windows.is_windows_checkout(project_dir)
        with step("check_session") as handle:
            if not on_windows and not self.runner.run(["which", "dtach"]).ok:
                raise SessionError("dtach is not installed (sudo apt install -y dtach)")
            if self.is_running(project_id):
                raise SessionError(f"a session is already running for {project_id!r}; stop it first")
            if not is_trusted(project_dir):
                raise SessionError(
                    f"{project_dir} is not trusted by Claude Code yet — a detached session would just hang "
                    "at the trust prompt. Run `claude` in that directory once and accept the trust prompt, "
                    "then retry."
                )
            handle.detail = f"{project_dir} is trusted"

        name = session_name(project_id)
        if on_windows:
            with step("launch_session") as handle:
                chosen = launch_for(JOB_SESSION, self.paths)
                args = ["--remote-control", name, *model_args(chosen.model, chosen.effort)]
                # Playwright MCP's own config lives in a WSL path a Windows-native claude can't reach
                # (docs/architecture.md, "Windows-hosted projects") — left off for a Windows session.
                env = usage_client.launch_env(self.paths, project=project_id, job_type="agent.session")
                try:
                    pid = windows_process.start_background(self.runner, "claude", args, windows.to_windows_path(project_dir), env)
                except windows_process.WindowsProcessError as exc:
                    raise SessionError(f"could not start a session for {project_id!r}: {exc}") from exc
                winpid_path = self.paths.session_windows_pid_path(project_id)
                winpid_path.parent.mkdir(parents=True, exist_ok=True)
                winpid_path.write_text(str(pid))
                handle.detail = f"{name} (Windows pid {pid})"
                return name

        with step("launch_session") as handle:
            socket = self.paths.session_socket_path(project_id)
            socket.parent.mkdir(parents=True, exist_ok=True)
            if socket.exists():
                # is_running() (a pgrep on this same path) just came back
                # negative, so nothing holds this socket — a leftover from a
                # session that died without cleaning up (e.g. its cgroup was
                # killed directly). `dtach -n` refuses to bind an existing
                # path even when nothing is listening on it.
                socket.unlink()

            chosen = launch_for(JOB_SESSION, self.paths)
            argv = ["dtach", "-n", str(socket), "claude", "--remote-control", name, *model_args(chosen.model, chosen.effort)]
            if self.playwright_mcp:
                # Last, since `--mcp-config` takes every value after it.
                argv += playwright.mcp_claude_args(
                    self.runner, self.paths.sessions_dir / "playwright-mcp" / project_id.replace("/", "-"),
                )
            unit = _unit_name(project_id)
            result = self.runner.run([
                "systemd-run", "--user", "--collect", "--scope", f"--unit={unit}",
                f"--setenv=PATH={user_path_env()}",
                *setenv_args(usage_client.launch_env(self.paths, project=project_id, job_type="agent.session")), *argv,
            ], cwd=project_dir)
            if not result.ok:
                raise SessionError(f"could not start a session for {project_id!r}: {(result.stderr or result.stdout).strip()}")
            handle.detail = name
            return name

    def stop(self, project_id: str) -> bool:
        winpid_path = self.paths.session_windows_pid_path(project_id)
        if winpid_path.exists():
            pid = int(winpid_path.read_text().strip())
            alive = windows_process.is_alive(self.runner, pid)
            if alive:
                windows_process.stop(self.runner, pid)
            winpid_path.unlink(missing_ok=True)
            return alive

        if not self.is_running(project_id):
            return False
        name = session_name(project_id)
        unit = _unit_name(project_id)
        self.runner.run(["systemctl", "--user", "stop", unit])
        self.runner.run(["systemctl", "--user", "reset-failed", unit])
        self.runner.run(["pkill", "-TERM", "-f", _match(self.paths, project_id)])
        # Defense in depth: dtach exiting should take claude down with it
        # (its PTY master closes, same as a terminal window closing), but
        # make sure nothing is left holding the session name.
        self.runner.run(["pkill", "-f", f"claude --remote-control {name}"])
        return True
