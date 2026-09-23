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
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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


def is_trusted(project_dir: Path) -> bool:
    """Whether Claude Code's own workspace-trust dialog has already been
    accepted for this directory. A session launched via `dtach -n` runs
    fully detached — if the folder isn't trusted yet, `claude
    --remote-control` sits at the trust prompt forever with no one there
    to answer it, and `is_running` would otherwise call it "running"
    even though it never reaches Remote Control."""
    try:
        data = json.loads(claude_config_path().read_text())
    except (OSError, json.JSONDecodeError):
        return False
    entry = data.get("projects", {}).get(str(project_dir))
    return bool(isinstance(entry, dict) and entry.get("hasTrustDialogAccepted"))


def session_name(project_id: str) -> str:
    return f"lws-{project_id.split('/')[-1]}-session"


def _match(paths: Paths, project_id: str) -> str:
    return f"dtach -n {paths.session_socket_path(project_id)}"


def _unit_name(project_id: str) -> str:
    return f"little-workshop-agent-session-{project_id.replace('/', '-')}"


class SessionFlow:
    def __init__(self, paths: Paths, runner: CommandRunner = DEFAULT_RUNNER):
        self.paths = paths
        self.runner = runner

    def is_running(self, project_id: str) -> bool:
        return self.runner.run(["pgrep", "-f", _match(self.paths, project_id)]).ok

    def start(self, project_id: str, project_dir: Path) -> str:
        if not self.runner.run(["which", "dtach"]).ok:
            raise SessionError("dtach is not installed (sudo apt install -y dtach)")
        if self.is_running(project_id):
            raise SessionError(f"a session is already running for {project_id!r}; stop it first")
        if not is_trusted(project_dir):
            raise SessionError(
                f"{project_dir} is not trusted by Claude Code yet — a detached session would just hang "
                "at the trust prompt. Run `claude` in that directory once and accept the trust prompt, "
                "then retry."
            )

        socket = self.paths.session_socket_path(project_id)
        socket.parent.mkdir(parents=True, exist_ok=True)
        if socket.exists():
            # is_running() (a pgrep on this same path) just came back
            # negative, so nothing holds this socket — a leftover from a
            # session that died without cleaning up (e.g. its cgroup was
            # killed directly). `dtach -n` refuses to bind an existing
            # path even when nothing is listening on it.
            socket.unlink()

        name = session_name(project_id)
        argv = ["dtach", "-n", str(socket), "claude", "--remote-control", name]
        unit = _unit_name(project_id)
        result = self.runner.run([
            "systemd-run", "--user", "--collect", "--scope", f"--unit={unit}",
            f"--setenv=PATH={user_path_env()}", *argv,
        ], cwd=project_dir)
        if not result.ok:
            raise SessionError(f"could not start a session for {project_id!r}: {(result.stderr or result.stdout).strip()}")
        return name

    def stop(self, project_id: str) -> bool:
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
