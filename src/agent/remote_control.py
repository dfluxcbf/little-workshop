"""Remote Control on a running Claude session of a Job, switched on from the Dashboard.

A Job's Claude (a task run, an update-doc session, a query) may run without Remote Control. Activating
it does not touch that process: the session is *resumed* (`claude --resume <id> --remote-control <name>`)
in an interactive Claude of its own, under `dtach` in its own systemd scope, so the owner can pick it up
in the Claude app. Deactivating stops only that scope. A session that already had Remote Control (a task
run started with `--remote-control`) is shown, never duplicated, and cannot be deactivated: the only way
to drop its connection would be to end the Claude that is working.

A row of `remote_control_sessions` is active from `started_at` until `stopped_at`, and counts as active
only while its Job's Claude is still running; `end` (called when that work ends) sets `stopped_at`.
Rows are keyed by `agent_run_id`: the run's id for a task, the Job's id for any other Job."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from common.paths import Paths
from common.registry_client import ProjectResolutionError, resolve_project
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.systemd import user_path_env
from agent import db, peek
from agent.query import session_dir
from agent.session_flow import is_trusted

ACTIVATED_MARK = "-rc-"  # in the name of a session this module started (the others are not ours to end)


class RemoteControlError(RuntimeError):
    pass


@dataclass
class Target:
    """The Claude session of a Job that Remote Control would attach to."""

    key: str  # `remote_control_sessions.agent_run_id`
    project_id: str
    running: bool
    session_id: str | None
    cwd: Path | None
    skip_permissions: bool = False  # a task's Claude runs unattended with them skipped; its resumed copy does too


def unit_name(key: str) -> str:
    return f"lws-rc-{key[:12]}"


def activated_name(project_id: str, key: str) -> str:
    return f"lws-{project_id.split('/')[-1]}{ACTIVATED_MARK}{key[:8]}"


def resolve(conn: sqlite3.Connection, paths: Paths, job_id: str, *, project_id: str = "", project_dir_of=None) -> Target | None:
    """The Claude session behind `job_id`; None when the Job has none (it does not use Claude)."""
    project_dir_of = project_dir_of or _project_dir
    run = db.get_run_by_job(conn, job_id)
    if run is not None:
        cwd = project_dir_of(paths, run["project_id"])
        return Target(run["id"], run["project_id"], run["status"] == db.RUNNING, run.get("session_id"), cwd, skip_permissions=True)
    update = db.get_doc_update(conn, job_id)
    if update is not None:
        worktree = Path(update["worktree_path"]) if update.get("worktree_path") else None
        newest = peek.newest_transcript(worktree) if worktree else None
        running = update["status"] in (db.DOC_UPDATE_STARTING, db.DOC_UPDATE_RUNNING)
        return Target(job_id, update["project_id"], running, newest.stem if newest else None, worktree)
    queries = db.query_sessions(conn, job_id=job_id)
    if queries:
        session = queries[-1]
        return Target(job_id, project_id, session["finished_at"] is None, session["session_id"], session_dir(paths, session))
    return None


def _project_dir(paths: Paths, project_id: str) -> Path | None:
    try:
        return Path(resolve_project(paths, project_id)["path"])
    except ProjectResolutionError:
        return None


def status(conn: sqlite3.Connection, target: Target) -> dict:
    """What the Job's page shows: whether the button is offered (`can_activate`), whether the connection is
    `active` (a session without `stopped_at`, of a Job still running), and the newest session as history."""
    sessions = db.list_sessions(conn, target.key)
    newest = sessions[-1] if sessions else None
    active = newest is not None and newest["stopped_at"] is None and target.running
    return {
        "running": target.running,
        "active": active,
        # Nothing active and a session to resume: the button. An active one is shown instead.
        "can_activate": target.running and not active and bool(target.session_id),
        "can_deactivate": active and newest is not None and ACTIVATED_MARK in newest["session_name"],
        "already_set_up": active and newest is not None and ACTIVATED_MARK not in newest["session_name"],
        "session_name": newest["session_name"] if newest else None,
        "started_at": newest["started_at"] if newest else None,
        "stopped_at": newest["stopped_at"] if newest else None,
    }


class RemoteControl:
    def __init__(self, paths: Paths, runner: CommandRunner = DEFAULT_RUNNER) -> None:
        self.paths = paths
        self.runner = runner

    def activate(self, conn: sqlite3.Connection, target: Target) -> str:
        """Resume the Job's session with Remote Control; returns the session's name."""
        current = status(conn, target)
        if not target.running:
            raise RemoteControlError("Claude is not running this job: there is nothing to attach to")
        if current["active"]:
            raise RemoteControlError("Remote control is already set up for this job")
        if not target.session_id or target.cwd is None or not target.cwd.is_dir():
            raise RemoteControlError("this job's Claude session is not known yet; try again in a moment")
        if not self.runner.run(["which", "dtach"]).ok:
            raise RemoteControlError("dtach is not installed (sudo apt install -y dtach)")
        if not is_trusted(target.cwd):
            raise RemoteControlError(f"Claude Code does not trust {target.cwd} yet: open Claude there once and accept the prompt")
        name = activated_name(target.project_id or "job", target.key)
        unit = unit_name(target.key)
        socket = self.paths.sessions_dir / f"rc-{target.key[:12]}.sock"
        socket.parent.mkdir(parents=True, exist_ok=True)
        socket.unlink(missing_ok=True)
        self.runner.run(["systemctl", "--user", "reset-failed", f"{unit}.scope"])  # a scope of an earlier activation
        argv = ["dtach", "-n", str(socket), "claude", "--resume", target.session_id, "--remote-control", name]
        if target.skip_permissions:
            argv += ["--dangerously-skip-permissions"]
        result = self.runner.run(
            ["systemd-run", "--user", "--collect", "--scope", f"--unit={unit}", f"--setenv=PATH={user_path_env()}", *argv], cwd=target.cwd,
        )
        if not result.ok:
            raise RemoteControlError(f"could not resume the session with remote control: {(result.stderr or result.stdout).strip()}")
        db.record_session_start(conn, target.key, target.project_id, name)
        return name

    def deactivate(self, conn: sqlite3.Connection, target: Target) -> None:
        """End the remote-control connection this module started, and only it: the job's own Claude is another process."""
        current = status(conn, target)
        if not current["can_deactivate"]:
            raise RemoteControlError(
                "remote control is not active here" if not current["active"]
                else "this session had remote control from its start: it cannot be dropped without ending Claude")
        self.end(conn, target.key)

    def end(self, conn: sqlite3.Connection, key: str) -> None:
        """The Job's Claude work ended (or the user deactivated): stop what activation started, set `stopped_at`."""
        sessions = db.list_sessions(conn, key)
        if any(ACTIVATED_MARK in s["session_name"] and s["stopped_at"] is None for s in sessions):
            unit = f"{unit_name(key)}.scope"
            self.runner.run(["systemctl", "--user", "stop", unit])
            self.runner.run(["systemctl", "--user", "reset-failed", unit])
        db.stop_open_sessions(conn, key)
