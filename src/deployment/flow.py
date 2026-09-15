"""Managed Process lifecycle for *other* projects' servers — ported from
`little-runner`'s `server_flow.py`: worktree-based build-before-stop
deploys, defence-in-depth stop, confirm-gated deploys, Tailscale
exposure. Reflexive self-redeploy (this pillar, or Core, redeploying
itself) is explicitly not in scope here — docs/milestones.md M5.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common import systemd
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from deployment import confirm, db, gitutil
from deployment.manifest import ServerManifest
from deployment.netutil import port_is_listening, wait_for_port, wait_for_port_free


class DeploymentError(Exception):
    pass


@dataclass
class DownReport:
    name: str
    unit_stopped: bool = False
    pids_killed: list[int] = field(default_factory=list)
    tailscale_off: bool = False
    port_free: bool = True


@dataclass
class UpResult:
    branch: str
    version: str
    commit: str
    how: str
    exposure: str | None
    confirm_message: str | None = None


@dataclass
class ServerStatus:
    name: str
    unit: str
    pids: list[int]
    listening: bool
    tailscale: str
    branch: str
    version: str

    @property
    def running(self) -> bool:
        return self.unit == "active" or bool(self.pids)


def _unit_key(project_id: str) -> str:
    """Reuses `common.systemd`'s pillar-launch machinery for a project's
    server unit — the "pillar" argument is just a unit-name component."""
    return f"deploy-{project_id}"


@dataclass
class DeploymentFlow:
    paths: Paths
    runner: CommandRunner = DEFAULT_RUNNER
    sleep: Callable[[float], None] = time.sleep
    probe: Callable[[int], bool] = port_is_listening
    clock: Callable[[], float] = time.monotonic

    # -- helpers --------------------------------------------------------

    def _pids(self, manifest: ServerManifest) -> list[int]:
        # -A/--ignore-ancestors: this pillar's own argv could itself contain
        # the configured match string as a substring; without -A a process
        # could find and kill itself.
        result = self.runner.run(["pgrep", "-A", "-f", manifest.match])
        return [int(p) for p in result.stdout.split() if p.strip().isdigit()]

    def _wait(self, predicate: Callable[[], bool], timeout: float, interval: float = 0.5) -> bool:
        deadline = self.clock() + timeout
        while True:
            if predicate():
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(interval)

    # -- down: defence-in-depth stop -------------------------------------

    def down(self, project_id: str, manifest: ServerManifest, *, tailscale_off: bool = True) -> DownReport:
        report = DownReport(manifest.name)

        if systemd.is_active(_unit_key(project_id), runner=self.runner):
            systemd.stop(_unit_key(project_id), runner=self.runner)
            report.unit_stopped = True
        systemd.reset_failed(_unit_key(project_id), runner=self.runner)

        pids = self._pids(manifest)
        if pids:
            self.runner.run(["pkill", "-A", "-TERM", "-f", manifest.match])
            report.pids_killed.extend(pids)
            if not self._wait(lambda: not self._pids(manifest), manifest.stop_timeout_seconds):
                self.runner.run(["pkill", "-A", "-KILL", "-f", manifest.match])

        if tailscale_off and manifest.tailscale:
            if self.runner.run(["tailscale", "serve", f"--https={manifest.tailscale.https_port}", "off"]).ok:
                report.tailscale_off = True
            if manifest.tailscale.service:
                self.runner.run([
                    "tailscale", "serve", f"--service=svc:{manifest.tailscale.service}",
                    f"--https={manifest.tailscale.service_https_port}", "off",
                ])

        report.port_free = wait_for_port_free(
            manifest.port, manifest.stop_timeout_seconds, sleep=self.sleep, probe=self.probe, clock=self.clock
        )
        return report

    # -- install / launch / expose ---------------------------------------

    def install(self, manifest: ServerManifest, *, cwd: Path) -> None:
        result = self.runner.run(manifest.install, timeout=3600, cwd=cwd)
        if not result.ok:
            raise DeploymentError(
                f"install failed ({' '.join(manifest.install)} exited {result.returncode}); "
                f"the previously running version was left untouched: {(result.stderr or result.stdout)[-2000:]}"
            )

    def launch(self, project_id: str, manifest: ServerManifest, *, cwd: Path) -> str:
        result = systemd.start(
            _unit_key(project_id),
            ["bash", "-c", manifest.command],
            working_directory=str(cwd),
            environment=manifest.env or None,
            runner=self.runner,
        )
        if not result.ok:
            raise DeploymentError(f"could not launch {manifest.name}: {(result.stderr or result.stdout).strip()}")
        how = f"systemd --user unit {systemd.unit_name(_unit_key(project_id))}"
        if not wait_for_port(manifest.port, manifest.ready_timeout_seconds, sleep=self.sleep, probe=self.probe, clock=self.clock):
            raise DeploymentError(
                f"{manifest.name} did not start listening on port {manifest.port} within "
                f"{manifest.ready_timeout_seconds}s ({how})"
            )
        return how

    def expose(self, manifest: ServerManifest) -> str | None:
        if not manifest.tailscale:
            return None
        if self.runner.run(["which", "tailscale"]).returncode != 0:
            return "tailscale not installed"
        result = self.runner.run([
            "tailscale", "serve", "--bg", f"--https={manifest.tailscale.https_port}", f"http://127.0.0.1:{manifest.port}",
        ])
        if not result.ok:
            return f"WARNING: tailscale serve failed: {(result.stderr or result.stdout).strip()}"
        description = f"tailscale serve --https={manifest.tailscale.https_port} -> 127.0.0.1:{manifest.port}"
        if manifest.tailscale.service:
            svc_result = self.runner.run([
                "tailscale", "serve", f"--service=svc:{manifest.tailscale.service}", "--bg",
                f"--https={manifest.tailscale.service_https_port}", f"http://127.0.0.1:{manifest.port}",
            ])
            if svc_result.ok:
                description += f"; svc:{manifest.tailscale.service} -> 127.0.0.1:{manifest.port}"
        return description

    # -- the worktree build-before-stop sequence --------------------------

    def _deploy(self, project_id: str, manifest: ServerManifest, project_dir: Path, commitish: str) -> tuple[str, str, str]:
        """Build+install a commitish in a throwaway worktree — never the
        project's own checkout — and only stop the currently running
        version and launch the new one once that succeeds. A bad
        build/install must never take a running server down."""
        worktree = self.paths.worktree_path(f"deploy-{project_id}")
        gitutil.add_worktree(project_dir, commitish, worktree)
        try:
            version = (worktree / "VERSION").read_text(encoding="utf-8").strip() if (worktree / "VERSION").exists() else "?"
            commit = gitutil.current_commit(worktree)
            self.install(manifest, cwd=worktree)
            self.down(project_id, manifest, tailscale_off=False)
            # Not cwd=worktree: the worktree is removed right after this call
            # returns, but the launched process's working directory has to
            # keep existing for as long as the unit does (Restart=on-failure
            # cds into it again on every restart).
            how = self.launch(project_id, manifest, cwd=self.paths.state_home)
        finally:
            gitutil.remove_worktree(project_dir, worktree)
        return version, commit, how

    def up(self, conn: sqlite3.Connection, project_id: str, manifest: ServerManifest, project_dir: Path, branch: str) -> UpResult:
        gitutil.run(["git", "fetch", "origin", "--prune"], project_dir)
        location = gitutil.branch_location(project_dir, branch)
        if location is None:
            raise DeploymentError(f"branch '{branch}' does not exist in {project_dir} or on origin; nothing was stopped")

        commitish = gitutil.deploy_commitish(project_dir, branch)
        version, commit, how = self._deploy(project_id, manifest, project_dir, commitish)
        exposure = self.expose(manifest)
        db.save_server_state(conn, project_id, branch, version, commit)
        db.record_deployment(conn, project_id, branch, commit, version, "succeeded", how)

        confirm_message = None
        if manifest.confirm_timeout_seconds:
            confirm_message = self._arm_confirm_gate(conn, project_id, branch, commit, version, manifest.confirm_timeout_seconds)

        return UpResult(branch=branch, version=version, commit=commit, how=how, exposure=exposure, confirm_message=confirm_message)

    # -- confirm-gated deploys (opt-in via manifest.confirm_timeout_seconds) -

    def _arm_confirm_gate(self, conn: sqlite3.Connection, project_id: str, branch: str, commit: str, version: str, timeout_seconds: int) -> str:
        """Marks the deploy pending and dispatches a systemd --user timer
        that rolls it back if nobody confirms in time — a brand-new,
        independent transient unit, so it fires even if this deploy takes
        this very pillar's own unit down."""
        state = db.mark_pending(conn, project_id, branch, commit, version, timeout_seconds)
        unit = f"deploy-timeout-{project_id.replace('/', '-')}-{state['deploy_id']}"
        result = self.runner.run([
            "systemd-run", "--user", "--collect", f"--unit=little-workshop-{unit}", f"--on-active={timeout_seconds}s",
            "lws", "deployment", "rollback-timeout", project_id, "--deploy-id", state["deploy_id"],
        ])
        if not result.ok:
            raise DeploymentError(
                f"{project_id}: deployed but could not arm the confirm-gate watchdog "
                f"({(result.stderr or result.stdout).strip()}); confirm or roll back manually"
            )
        return f"awaiting confirmation (auto-rollback in {timeout_seconds}s unless confirmed)"

    def seed_confirmation(self, conn: sqlite3.Connection, project_id: str) -> None:
        """Establishes the confirmed baseline from whatever's currently
        cached as deployed — the "what's running right now" answer — so a
        server's *first* confirm-gated deploy has something real to fall
        back to if its watchdog fires unconfirmed. Must be called before
        that first gated deploy; there is deliberately no baseline to make
        up out of nothing."""
        cached = db.get_server_state(conn, project_id)
        if cached is None:
            raise DeploymentError(f"{project_id}: nothing has been deployed yet — no baseline to seed from")
        db.seed_confirmation(conn, project_id, cached["branch"], cached["commit_sha"], cached["version"])

    def rollback_if_unconfirmed(
        self, conn: sqlite3.Connection, project_id: str, manifest: ServerManifest, project_dir: Path, deploy_id: str
    ) -> str:
        """The watchdog's action. A clean no-op — never an exception — once
        the deploy has already been settled one way or another."""
        state, reason = confirm.rollback_guard(conn, project_id, deploy_id)
        if reason is not None:
            return reason
        assert state is not None

        version, commit, how = self._deploy(project_id, manifest, project_dir, state["confirmed_commit"])
        exposure = self.expose(manifest)
        db.save_server_state(conn, project_id, state["confirmed_branch"], version, commit)
        db.record_deployment(conn, project_id, state["confirmed_branch"], commit, version, "rolled_back", how)
        db.settle_after_rollback(conn, project_id)
        summary = f"{project_id}: rolled back to {state['confirmed_branch']}@{commit[:10]} (version {version}, {how})"
        if exposure:
            summary += f"; {exposure}"
        return summary

    # -- status -----------------------------------------------------------

    def status(self, conn: sqlite3.Connection, project_id: str, manifest: ServerManifest) -> ServerStatus:
        unit = "active" if systemd.is_active(_unit_key(project_id), runner=self.runner) else "inactive"
        cached = db.get_server_state(conn, project_id)
        branch = cached["branch"] if cached else "?"
        version = cached["version"] if cached else "?"
        ts_status = self.runner.run(["tailscale", "serve", "status"])
        if manifest.tailscale:
            ts = "mapped" if f"127.0.0.1:{manifest.port}" in ts_status.stdout else "not mapped"
        else:
            ts = "n/a"
        return ServerStatus(
            name=manifest.name, unit=unit, pids=self._pids(manifest), listening=self.probe(manifest.port),
            tailscale=ts, branch=branch, version=version,
        )
