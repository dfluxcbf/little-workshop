"""Managed Process lifecycle for *other* projects' servers — ported from
`little-runner`'s `server_flow.py`: worktree-based build-before-stop
deploys, defence-in-depth stop, confirm-gated deploys, Tailscale
exposure. Reflexive self-redeploy (this pillar, or Core, redeploying
itself) is explicitly not in scope here — docs/milestones.md M5.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from common import systemd
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from core.health import HealthResult, probe_health
from deployment import confirm, db, gitutil
from deployment.manifest import ServerManifest
from deployment.netutil import port_is_listening, wait_for_port, wait_for_port_free
from deployment.workshop_repo import workshop_repo_dir

# docs/design.md's "Planned upgrade": confirm-gating defaults on for Core
# and Deployment & Servers themselves (the two processes everything else
# depends on), opt-in for every other pillar — the same 10-minute figure
# already used elsewhere in this project as the human-override window.
DEFAULT_SELF_REDEPLOY_CONFIRM_SECONDS = 600
SELF_REDEPLOY_GATED_BY_DEFAULT = ("core", "deployment")


class DeploymentError(Exception):
    pass


class SelfRedeployRequiresDispatch(Exception):
    """Raised by `redeploy_pillar` when the target pillar is the one
    currently executing this code, inside its own unit's cgroup. The
    caller (`app.py`) must dispatch to an independent `systemd-run` unit
    instead of calling the inline path again — see `dispatch_self_redeploy`.
    """

    def __init__(self, pillar: str):
        super().__init__(f"redeploying {pillar!r} from inside its own unit — must dispatch")
        self.pillar = pillar


def _read_own_cgroup() -> str:
    try:
        return Path("/proc/self/cgroup").read_text(encoding="utf-8")
    except OSError:
        return ""


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
    own_cgroup: Callable[[], str] = _read_own_cgroup
    health_probe: Callable[..., HealthResult] = probe_health

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
            confirm_message = self.arm_confirm_gate(conn, project_id, branch, commit, version, manifest.confirm_timeout_seconds)

        return UpResult(branch=branch, version=version, commit=commit, how=how, exposure=exposure, confirm_message=confirm_message)

    # -- confirm-gated deploys (opt-in via manifest.confirm_timeout_seconds) -

    def arm_confirm_gate(self, conn: sqlite3.Connection, project_id: str, branch: str, commit: str, version: str, timeout_seconds: int) -> str:
        """Marks the deploy pending and dispatches a systemd --user timer
        that rolls it back if nobody confirms in time — a brand-new,
        independent transient unit, so it fires even if this deploy takes
        this very pillar's own unit down."""
        state = db.mark_pending(conn, project_id, branch, commit, version, timeout_seconds)
        unit = f"deploy-timeout-{project_id.replace('/', '-')}-{state['deploy_id']}"
        result = self.runner.run([
            "systemd-run", "--user", "--collect", f"--unit=little-workshop-{unit}", f"--on-active={timeout_seconds}s",
            f"--setenv=PATH={systemd.user_path_env()}",
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

    # -- self-redeploy: little-workshop's own pillars (M5) ----------------

    def _inside_own_cgroup(self, unit: str) -> bool:
        """Whether this process is itself currently running inside `unit`'s
        own cgroup — e.g. this very Deployment & Servers process, asked to
        redeploy itself. The forward redeploy and its rollback must never
        run inline in that case: stopping the unit would kill this process
        mid-deploy, before install/launch ever ran."""
        return f"/{unit}" in self.own_cgroup()

    def _pillar_install_dir(self, pillar: str, commit: str) -> Path:
        return self.paths.state_home / "pillar-code" / f"{pillar}-{commit[:12]}"

    def _pids_matching(self, match: str) -> list[int]:
        result = self.runner.run(["pgrep", "-A", "-f", match])
        return [int(p) for p in result.stdout.split() if p.strip().isdigit()]

    def _stop_pillar(self, pillar: str, *, stop_timeout_seconds: float = 30.0) -> None:
        if systemd.is_active(pillar, runner=self.runner):
            systemd.stop(pillar, runner=self.runner)
        systemd.reset_failed(pillar, runner=self.runner)
        match = f"-m {pillar}"
        pids = self._pids_matching(match)
        if pids:
            self.runner.run(["pkill", "-A", "-TERM", "-f", match])
            if not self._wait(lambda: not self._pids_matching(match), stop_timeout_seconds):
                self.runner.run(["pkill", "-A", "-KILL", "-f", match])

    def _xdg_env_for_own_paths(self) -> dict[str, str]:
        """Env vars that make a launched process's own `Paths.from_env()`
        resolve to this exact `self.paths` — not "whatever XDG_CONFIG_HOME
        this process's own environment happens to have," which breaks the
        moment `self.paths` was constructed directly (every test in this
        suite) rather than actually read from the environment. A
        `systemd-run --user` launch never inherits the launching process's
        environment on its own, so this has to be passed explicitly."""
        return {
            "XDG_CONFIG_HOME": str(self.paths.config_home.parent),
            "XDG_STATE_HOME": str(self.paths.state_home.parent),
        }

    def _launch_pillar(self, pillar: str, install_dir: Path) -> str:
        environment = {"PYTHONPATH": str(install_dir / "src"), **self._xdg_env_for_own_paths()}
        # A pipx-installed pillar can't locate the source checkout on its own; carry the override forward.
        repo_override = os.environ.get("LITTLE_WORKSHOP_REPO_DIR")
        if repo_override:
            environment["LITTLE_WORKSHOP_REPO_DIR"] = repo_override
        result = systemd.start(
            pillar, ["python3", "-m", pillar],
            working_directory=str(install_dir),
            environment=environment,
            runner=self.runner,
        )
        if not result.ok:
            raise DeploymentError(f"could not launch {pillar}: {(result.stderr or result.stdout).strip()}")
        how = f"systemd --user unit {systemd.unit_name(pillar)}"
        probed = self.health_probe(self.paths.socket_path(pillar), timeout=5.0, max_retries=20, sleep=self.sleep)
        if not probed.ok:
            raise DeploymentError(f"{pillar} did not become healthy after launch ({how}): {probed.error}")
        return how

    def redeploy_pillar_direct(
        self, conn: sqlite3.Connection, pillar: str, workshop_repo_dir: Path, commit: str,
    ) -> UpResult:
        """Build-before-stop, generalized from an external project's server
        to one of little-workshop's own pillars: check out `commit` into a
        scratch worktree, smoke-check that it actually imports, copy it
        into a fresh, *stable* install location (never the scratch
        worktree itself — that's removed immediately after, matching
        docs/design.md's "Planned upgrade" diagram's `git worktree remove
        --force`), and only then stop the old process and launch the new
        one. No cgroup check, no confirm-gate arming — callers that already
        know it's safe to run inline (because they ARE the dispatched
        worker, or the target isn't self-referential) use this directly;
        `redeploy_pillar`/`rollback_pillar_if_unconfirmed` are the checked
        entry points."""
        resolved = gitutil.run(["git", "rev-parse", commit], workshop_repo_dir)
        resolved_commit = resolved.stdout.strip() or commit

        worktree = self.paths.worktree_path(f"redeploy-{pillar}-{uuid.uuid4().hex[:8]}")
        gitutil.add_worktree(workshop_repo_dir, resolved_commit, worktree)
        try:
            check = self.runner.run(
                ["python3", "-c", f"import sys; sys.path.insert(0, 'src'); import {pillar}"],
                cwd=worktree, timeout=30,
            )
            if not check.ok:
                raise DeploymentError(
                    f"{pillar}: commit {resolved_commit[:12]} does not even import cleanly "
                    f"({(check.stderr or check.stdout).strip()[:2000]}); the running version was left untouched"
                )
            version_file = worktree / "VERSION"
            version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "?"

            install_dir = self._pillar_install_dir(pillar, resolved_commit)
            if install_dir.exists():
                shutil.rmtree(install_dir)
            shutil.copytree(worktree / "src", install_dir / "src")
        finally:
            gitutil.remove_worktree(workshop_repo_dir, worktree)

        previous = db.get_pillar_deployment(conn, pillar)

        self._stop_pillar(pillar)
        how = self._launch_pillar(pillar, install_dir)

        db.save_pillar_deployment(conn, pillar, str(install_dir), resolved_commit)
        db.save_server_state(conn, pillar, commit, version, resolved_commit)
        db.record_deployment(conn, pillar, commit, resolved_commit, version, "succeeded", how)

        if previous is not None and Path(previous["install_dir"]) != install_dir:
            shutil.rmtree(previous["install_dir"], ignore_errors=True)

        return UpResult(branch=commit, version=version, commit=resolved_commit, how=how, exposure=None)

    def redeploy_pillar(
        self, conn: sqlite3.Connection, pillar: str, workshop_repo_dir: Path, commit: str,
        *, confirm_timeout_seconds: int | None = None,
    ) -> UpResult:
        """The checked entry point for a forward self-redeploy — raises
        `SelfRedeployRequiresDispatch` when this process is itself running
        inside the target pillar's own unit."""
        if self._inside_own_cgroup(systemd.unit_name(pillar)):
            raise SelfRedeployRequiresDispatch(pillar)

        result = self.redeploy_pillar_direct(conn, pillar, workshop_repo_dir, commit)

        gate_timeout = confirm_timeout_seconds
        if gate_timeout is None and pillar in SELF_REDEPLOY_GATED_BY_DEFAULT:
            gate_timeout = DEFAULT_SELF_REDEPLOY_CONFIRM_SECONDS
        if gate_timeout:
            result.confirm_message = self.arm_confirm_gate(conn, pillar, commit, result.commit, result.version, gate_timeout)
        return result

    def rollback_pillar_if_unconfirmed(
        self, conn: sqlite3.Connection, pillar: str, workshop_repo_dir: Path, deploy_id: str
    ) -> str:
        """The pillar-redeploy equivalent of `rollback_if_unconfirmed` —
        raises `SelfRedeployRequiresDispatch` under the same condition as
        `redeploy_pillar` (rolling back `deployment` itself, from inside
        its own about-to-be-replaced unit)."""
        if self._inside_own_cgroup(systemd.unit_name(pillar)):
            raise SelfRedeployRequiresDispatch(pillar)

        state, reason = confirm.rollback_guard(conn, pillar, deploy_id)
        if reason is not None:
            return reason
        assert state is not None

        result = self.redeploy_pillar_direct(conn, pillar, workshop_repo_dir, state["confirmed_commit"])
        db.settle_after_rollback(conn, pillar)
        return f"{pillar}: rolled back to {state['confirmed_commit'][:10]} (version {result.version}, {result.how})"

    def dispatch_self_redeploy(
        self, pillar: str, commit: str, *, confirm_timeout_seconds: int | None = None, job_id: str | None = None,
    ) -> str:
        """The escape hatch for redeploying the very pillar this code is
        running inside of: a brand-new, independent transient unit — its
        own cgroup — that survives this process dying underneath it.
        Invokes `lws deployment redeploy-pillar-worker` directly (never
        this pillar's own HTTP endpoint again, which would just run inside
        the same doomed cgroup)."""
        argv = ["lws", "deployment", "redeploy-pillar-worker", pillar, "--commit", commit]
        if confirm_timeout_seconds:
            argv += ["--confirm-timeout", str(confirm_timeout_seconds)]
        if job_id:
            argv += ["--job-id", job_id]
        return self._dispatch_detached(f"redeploy-{pillar}", argv)

    def dispatch_self_rollback(self, pillar: str, deploy_id: str) -> str:
        """The rollback-side equivalent of `dispatch_self_redeploy`."""
        argv = ["lws", "deployment", "rollback-pillar-worker", pillar, "--deploy-id", deploy_id]
        return self._dispatch_detached(f"rollback-{pillar}", argv)

    def _dispatch_detached(self, label: str, argv: list[str]) -> str:
        """A `systemd-run --user` dispatch does NOT inherit this process's
        own environment — only the systemd user manager's default one — so
        anything the worker needs to resolve the *same* `Paths.from_env()`
        this process would (a custom XDG base, most concretely this test
        suite's own isolated one) and the *same* little-workshop checkout
        (this process's own, not wherever `lws` itself happens to be
        pipx-installed from) has to be forwarded explicitly."""
        unit = f"little-workshop-{label}-{uuid.uuid4().hex[:8]}"
        setenv = [f"--setenv=PATH={systemd.user_path_env()}", f"--setenv=LITTLE_WORKSHOP_REPO_DIR={workshop_repo_dir()}"]
        setenv += [f"--setenv={key}={value}" for key, value in self._xdg_env_for_own_paths().items()]
        result = self.runner.run([
            "systemd-run", "--user", "--collect", f"--unit={unit}", "--property=Restart=no", *setenv, *argv,
        ])
        if not result.ok:
            raise DeploymentError(f"could not dispatch {label}: {(result.stderr or result.stdout).strip()}")
        return f"dispatched as systemd --user transient unit {unit}"
