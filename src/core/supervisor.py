"""Core's process-supervision logic: launches every enabled pillar,
tracks liveness, relaunches a crashed one, and — on Core's own
restart — reconnects to whatever is already running instead of blindly
relaunching everything (docs/design.md's "Core's own recovery").

Crash-loop accounting is owned here, not delegated to systemd's own
`StartLimitBurst`: Core needs the count itself (to expose it, and to
know when to stop trying and mark a pillar **faulted**), and the
1-minute-apart spacing docs/milestones.md M1 specifies is the cadence
`reconcile_once` is called at — each call represents one "try" — not a
sleep inside a single call. The unit's own `RestartSec=1`
(`common/systemd.py`) is what makes the underlying OS-level bounce
itself immediate, matching "relaunches ... immediately."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from common import systemd
from common.incident import KIND_CRASH, report_problem
from common.manifest import Manifest
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from core.health import HealthResult, probe_health
from core.registry import PillarStatus, Registry, RegistryEntry


@dataclass
class Supervisor:
    paths: Paths
    manifest: Manifest
    registry: Registry = field(default_factory=Registry)
    runner: CommandRunner = DEFAULT_RUNNER
    probe: Callable[..., HealthResult] = probe_health
    # How a crash is reported: common.incident.report_problem's signature (the agent's socket first).
    report: Callable[..., None] = report_problem
    # Each pillar unit's systemd restart count when last looked at (see check_restarts).
    _restarts_seen: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def _probe(self, pillar: str) -> HealthResult:
        return self.probe(
            self.paths.socket_path(pillar),
            timeout=self.manifest.health_probe_timeout_seconds,
            max_retries=self.manifest.health_probe_max_retries,
        )

    def _launch_environment(self, entry) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.manifest.workshop_repo_dir:
            env["LITTLE_WORKSHOP_REPO_DIR"] = self.manifest.workshop_repo_dir
        env.update(entry.environment or {})
        return env

    def startup(self) -> None:
        """Reconnect to what's already running; start only what's missing;
        confirm every disabled pillar is actually stopped."""
        for name, entry in self.manifest.pillars.items():
            if not entry.enabled:
                self._ensure_stopped(name)
                self.registry.set(
                    RegistryEntry(
                        name=name,
                        socket_path=self.paths.socket_path(name),
                        status=PillarStatus.DISABLED,
                    )
                )
                continue

            if entry.command is None:
                # Not installed/implemented yet — nothing to reconnect to
                # or start. Leave it out of the registry entirely.
                continue

            if systemd.is_active(name, runner=self.runner):
                result = self._probe(name)
                if result.ok:
                    self.registry.set(
                        RegistryEntry(
                            name=name,
                            socket_path=self.paths.socket_path(name),
                            status=PillarStatus.RECONNECTED,
                        )
                    )
                    continue
                # Unit is active but not answering health checks yet —
                # give it one relaunch attempt via the normal crash path.
                self._attempt_relaunch(name, entry)
            else:
                self._launch_fresh(name, entry)

    def _ensure_stopped(self, name: str) -> None:
        if systemd.is_active(name, runner=self.runner):
            systemd.stop(name, runner=self.runner)

    def _launch_fresh(self, name: str, entry) -> None:
        systemd.start(
            name,
            entry.command,
            working_directory=None,
            environment=self._launch_environment(entry),
            runner=self.runner,
        )
        result = self._probe(name)
        status = PillarStatus.HEALTHY if result.ok else PillarStatus.CRASHED
        self.registry.set(
            RegistryEntry(
                name=name,
                socket_path=self.paths.socket_path(name),
                status=status,
            )
        )

    def _attempt_relaunch(self, name: str, entry) -> RegistryEntry:
        existing = self.registry.get(name)
        crash_count = (existing.crash_count if existing else 0) + 1

        if crash_count > self.manifest.crash_relaunch_max_attempts:
            faulted = RegistryEntry(
                name=name,
                socket_path=self.paths.socket_path(name),
                status=PillarStatus.FAULTED,
                crash_count=crash_count,
            )
            self.registry.set(faulted)
            return faulted

        systemd.start(name, entry.command, environment=self._launch_environment(entry), runner=self.runner)
        result = self._probe(name)
        status = PillarStatus.HEALTHY if result.ok else PillarStatus.CRASHED
        entry_out = RegistryEntry(
            name=name,
            socket_path=self.paths.socket_path(name),
            status=status,
            crash_count=0 if result.ok else crash_count,
        )
        self.registry.set(entry_out)
        return entry_out

    def check_restarts(self) -> None:
        """systemd restarts a pillar that exits with a failure (`Restart=on-failure`) within a second,
        usually before Core ever sees it down: the unit's `NRestarts` is the only trace of the crash.
        A count higher than at the last look is recorded as an incident of kind crash, with the end of
        the pillar's log. The first look only learns the counts, and a unit started anew (a redeploy)
        counts from zero again, which is no crash."""
        units = {systemd.unit_name(name): name for name, entry in self.manifest.pillars.items() if entry.enabled and entry.command}
        if not units:
            return
        result = self.runner.run(["systemctl", "--user", "show", *units, "-p", "Id", "-p", "NRestarts"])
        if not result.ok:
            return
        for block in result.stdout.split("\n\n"):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            name = units.get(fields.get("Id", ""))
            if name is None or not fields.get("NRestarts", "").isdigit():
                continue
            restarts, seen = int(fields["NRestarts"]), self._restarts_seen.get(name)
            self._restarts_seen[name] = restarts
            if seen is None or restarts <= seen:
                continue
            unit = systemd.unit_name(name)
            log = self.runner.run(["journalctl", "--user", "-u", unit, "-n", "60", "--no-pager", "-o", "cat"])
            times = restarts - seen
            self.report(
                self.paths.socket_path("agent"), pillar=name, context=f"systemd unit {unit}", kind=KIND_CRASH, title="PillarCrashed",
                message=f"the {name} pillar failed and systemd restarted it {times} time{'' if times == 1 else 's'}",
                detail=log.stdout if log.ok else "",
            )

    def reconcile_once(self) -> None:
        """Meant to be called on a roughly-1-minute cadence: re-checks every
        enabled, non-faulted, installed pillar and relaunches anything
        found down, and records the crashes systemd recovered from by
        itself since the last call (`check_restarts`)."""
        self.check_restarts()
        for name, entry in self.manifest.pillars.items():
            if not entry.enabled or entry.command is None:
                continue
            current = self.registry.get(name)
            if current is not None and current.status == PillarStatus.FAULTED:
                continue  # faulted pillars stay down until a human intervenes

            if systemd.is_active(name, runner=self.runner):
                result = self._probe(name)
                if result.ok:
                    self.registry.set(
                        RegistryEntry(
                            name=name,
                            socket_path=self.paths.socket_path(name),
                            status=PillarStatus.HEALTHY,
                            crash_count=0,
                        )
                    )
                    continue

            self._attempt_relaunch(name, entry)
