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

Every outage is an incident of kind "crash" (REQ-000000400, REQ-000000401). It lasts from the first
check that finds a pillar down, or the first failure systemd logged for its unit (`core.journal`),
until the pillar answers again or is marked faulted, and its incident is filed then, with all of it:
the unit's state and the probe's error at each check, what each relaunch did, systemd's lines for the
run that failed and what its process logged last. An outage in which no process failed is filed too:
a pillar shown crashed while a redeploy was stopping it is explained, not left a mystery (2026-10-05).
"""

from __future__ import annotations

import re
import signal as signals
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from common import systemd
from common.incident import KIND_CRASH, TRACEBACK_LIMIT, report_problem
from common.manifest import Manifest
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from core.health import HealthResult, probe_health
from core.journal import Journal, UnitFailure
from core.registry import PillarStatus, Registry, RegistryEntry

# An incident's title by systemd's result for the run that ended.
_TITLES = {
    "exit-code": "PillarCrashed", "core-dump": "PillarCrashed", "signal": "PillarKilled", "timeout": "PillarTimedOut",
    "oom-kill": "PillarOutOfMemory", "start-limit-hit": "PillarCrashLoop", "watchdog": "PillarHung",
}
_STATE_PHRASES = {
    "deactivating": "its unit was being stopped (a redeploy or a stop was under way)",
    "activating": "its unit was still starting",
    "inactive": "its unit was not running",
    "failed": "its unit had failed",
    "active": "its unit was running but did not answer",
}
CHECKS_KEPT = 12
_LEFT_BEHIND = re.compile(r"Killing process \d+ \((.+?)\) with signal")
# A unit in one of these states is being started or stopped by someone: a redeploy, systemd itself.
_TRANSITIONS = ("activating", "deactivating", "reloading", "refreshing")
# A unit active for less than this is still starting up (a pillar settles what its last run left before
# it serves): not answering yet is no hang.
STARTUP_GRACE_SECONDS = 120.0


@dataclass
class _Outage:
    since: datetime
    checks: list[str] = field(default_factory=list)
    failures: list[UnitFailure] = field(default_factory=list)
    states: list[str] = field(default_factory=list)  # the unit's ActiveState at each check
    invocation: str = ""  # the unit run Core last found not answering
    hung: bool = False  # Core stopped a running unit that did not answer


def _signal_name(status: str) -> str:
    try:
        return signals.Signals(int(status)).name
    except (TypeError, ValueError):
        return status or "?"


def describe_failure(failure: UnitFailure) -> str:
    """What systemd did, as a clause: "its process exited with status 1"."""
    killed = f" ({_signal_name(failure.exit_status)})" if failure.exit_code in ("killed", "dumped") else ""
    if failure.result == "timeout":
        if any("'final-sigterm' timed out" in line for line in failure.systemd_lines):
            # The main process had exited; a process it started outlived it in the unit.
            left = next((m.group(1) for line in failure.systemd_lines if (m := _LEFT_BEHIND.search(line))), "?")
            return f"its process exited when asked to stop, but one it left behind ({left}) did not, so systemd killed it after the stop timeout"
        return f"it overran systemd's {'stop' if failure.stop_requested else 'start'} timeout and was killed{killed}"
    if failure.result == "exit-code":
        return f"its process exited with status {failure.exit_status or '?'}"
    if failure.result == "signal":
        return f"its process was killed by {_signal_name(failure.exit_status)}"
    if failure.result == "core-dump":
        return f"its process dumped core ({_signal_name(failure.exit_status)})"
    if failure.result == "oom-kill":
        return "the out-of-memory killer killed it"
    if failure.result == "start-limit-hit":
        return "it failed to start too often in a row, so systemd stopped trying"
    if failure.result == "watchdog":
        return "its watchdog timed out"
    return f"systemd ended its run with result '{failure.result}'"


def _lasted(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def _fit(sections: list[tuple[str, list[str]]], limit: int = TRACEBACK_LIMIT - 100) -> str:
    """The sections as text within `limit` (an incident keeps that much): the oldest lines of the
    last sections give way first."""
    kept = [(title, list(lines)) for title, lines in sections if lines]

    def text() -> str:
        return "\n\n".join(title + "\n" + "\n".join("  " + line for line in lines) for title, lines in kept)

    for _title, lines in reversed(kept):
        while len(text()) > limit and len(lines) > 1:
            del lines[0]
    return text()[-limit:]


@dataclass
class Supervisor:
    paths: Paths
    manifest: Manifest
    registry: Registry = field(default_factory=Registry)
    runner: CommandRunner = DEFAULT_RUNNER
    probe: Callable[..., HealthResult] = probe_health
    # How an outage is reported: common.incident.report_problem's signature (the agent's socket first).
    report: Callable[..., None] = report_problem
    # Where unit failures are read from; by default the user journal, from Core's kept position.
    journal: Journal | None = None
    clock: Callable[[], datetime] = datetime.now
    monotonic: Callable[[], float] = time.monotonic  # systemd's ActiveEnterTimestampMonotonic clock
    _outages: dict[str, _Outage] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.journal is None:
            self.journal = Journal(self.paths.state_home / "pillars" / "core-journal.json", runner=self.runner)

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
                self._attempt_relaunch(name, entry, f"not answering: {result.error}")
                continue
            state = systemd.unit_state(name, runner=self.runner)
            if state.get("ActiveState") in _TRANSITIONS:
                # Core restarted in the middle of a redeploy: whoever is stopping or starting it finishes.
                self._note(name, f"unit {state['ActiveState']}/{state.get('SubState', '?')} when Core started; "
                                 "not launched: it is being started or stopped", state)
                self.registry.set(RegistryEntry(name=name, socket_path=self.paths.socket_path(name), status=PillarStatus.CRASHED))
            else:
                self._launch_fresh(name, entry)

    def _ensure_stopped(self, name: str) -> None:
        if systemd.is_active(name, runner=self.runner):
            systemd.stop(name, runner=self.runner)

    def _launch_fresh(self, name: str, entry) -> None:
        systemd.reset_failed(name, runner=self.runner)  # a failed unit still loaded refuses a new start
        systemd.start(
            name,
            entry.command,
            working_directory=None,
            environment=self._launch_environment(entry),
            runner=self.runner,
        )
        result = self._probe(name)
        status = PillarStatus.HEALTHY if result.ok else PillarStatus.CRASHED
        if not result.ok:
            self._note(name, f"started with Core but not answering: {result.error}", systemd.unit_state(name, runner=self.runner))
        self.registry.set(
            RegistryEntry(
                name=name,
                socket_path=self.paths.socket_path(name),
                status=status,
            )
        )

    def _attempt_relaunch(self, name: str, entry, why: str = "not answering") -> RegistryEntry:
        existing = self.registry.get(name)
        crash_count = (existing.crash_count if existing else 0) + 1
        state = systemd.unit_state(name, runner=self.runner)
        unit = f"unit {state.get('ActiveState', '?')}/{state.get('SubState', '?')}"

        if crash_count > self.manifest.crash_relaunch_max_attempts:
            faulted = RegistryEntry(
                name=name,
                socket_path=self.paths.socket_path(name),
                status=PillarStatus.FAULTED,
                crash_count=crash_count,
            )
            self.registry.set(faulted)
            self._note(name, f"{why}; {unit}; no more relaunches: marked faulted", state)
            self._file(name, faulted_after=crash_count - 1)
            return faulted

        active = state.get("ActiveState", "")
        if active in _TRANSITIONS or (active == "active" and self._just_started(state)):
            # Something is starting or stopping it (a redeploy, systemd after a failure, its own start-up):
            # a relaunch now would race that, and systemd refuses a unit that still exists anyway.
            self._note(name, f"{why}; {unit}; not relaunched: it is being started or stopped", state)
            crashed = RegistryEntry(
                name=name, socket_path=self.paths.socket_path(name), status=PillarStatus.CRASHED, crash_count=crash_count,
            )
            self.registry.set(crashed)
            return crashed

        hung = active == "active"
        if hung:
            # Running for minutes yet silent through a whole probe: hung (REQ-000000408). A relaunch alone
            # is refused while the unit exists, so stop it first; systemd kills it if it ignores the stop.
            systemd.stop(name, runner=self.runner)
            self._outage(name).hung = True
        systemd.reset_failed(name, runner=self.runner)  # a failed unit still loaded refuses a new start
        launched = systemd.start(name, entry.command, environment=self._launch_environment(entry), runner=self.runner)
        result = self._probe(name)
        stopped = "hung: stopped and " if hung else ""
        relaunch = f"{stopped}relaunched" if launched.ok else f"{stopped}relaunch refused by systemd: {(launched.stderr or launched.stdout).strip()}"
        after = "answering" if result.ok else f"still not answering: {result.error}"
        self._note(name, f"{why}; {unit}; {relaunch}; {after}", state)
        status = PillarStatus.HEALTHY if result.ok else PillarStatus.CRASHED
        entry_out = RegistryEntry(
            name=name,
            socket_path=self.paths.socket_path(name),
            status=status,
            crash_count=0 if result.ok else crash_count,
        )
        self.registry.set(entry_out)
        if result.ok:
            self._file(name)
        return entry_out

    def check_failures(self) -> None:
        """Every abnormal end of a watched unit's run that systemd logged since the last look
        (REQ-000000400). A supervised pillar's failure joins its outage, filed when the pillar answers
        again (in this very pass when systemd or a redeploy already started it anew). Core's own
        failure, found once Core is back, and a faulted pillar's are filed straight away."""
        supervised = {systemd.unit_name(n): n for n, e in self.manifest.pillars.items() if e.enabled and e.command}
        for failure in self.journal.failures([*supervised, systemd.INSTALLED_CORE_UNIT]):
            pillar = supervised.get(failure.unit)
            current = self.registry.get(pillar) if pillar else None
            if pillar is None or (current is not None and current.status == PillarStatus.FAULTED):
                self._send(pillar or "core", _Outage(since=failure.at, failures=[failure]))
                continue
            self._outage(pillar, since=failure.at).failures.append(failure)

    def reconcile_once(self) -> None:
        """Meant to be called on a roughly-1-minute cadence: records the unit
        failures systemd logged since the last call (`check_failures`), then
        re-checks every enabled, non-faulted, installed pillar and relaunches
        anything found down."""
        self.check_failures()
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
                    self._file(name)
                    continue
                why = f"not answering: {result.error}"
            else:
                why = "unit not active"

            self._attempt_relaunch(name, entry, why)

    def _just_started(self, state: dict[str, str]) -> bool:
        entered = state.get("ActiveEnterTimestampMonotonic", "")
        if not entered.isdigit() or entered == "0":
            return False
        return self.monotonic() - int(entered) / 1e6 < STARTUP_GRACE_SECONDS

    # -- outages: one incident each ------------------------------------------------------------

    def _outage(self, name: str, *, since: datetime | None = None) -> _Outage:
        outage = self._outages.get(name)
        if outage is None:
            outage = self._outages[name] = _Outage(since=since or self.clock())
        return outage

    def _note(self, name: str, text: str, state: dict[str, str]) -> None:
        outage = self._outage(name)
        outage.checks.append(f"{self.clock():%H:%M:%S}  {text}")
        outage.states.append(state.get("ActiveState", ""))
        outage.invocation = state.get("InvocationID") or outage.invocation

    def _file(self, name: str, *, faulted_after: int | None = None) -> None:
        """The pillar answers again, or was just marked faulted: its outage, if one is open, ends."""
        outage = self._outages.pop(name, None)
        if outage is not None:
            self._send(name, outage, faulted_after=faulted_after)

    def _send(self, name: str, outage: _Outage, *, faulted_after: int | None = None) -> None:
        ended = self.clock()
        who = "Core" if name == "core" else f"the {name} pillar"
        unit = systemd.INSTALLED_CORE_UNIT if name == "core" else systemd.unit_name(name)
        failures = outage.failures
        lead = next((f for f in failures if f.result == "start-limit-hit"), failures[0]) if failures else None

        if faulted_after is not None:
            title = "PillarFaulted"
            relaunches = f"{faulted_after} relaunch{'' if faulted_after == 1 else 'es'}"
            message = (f"Core gave up on {who} after {relaunches} in a row failed: it stays down, "
                       "marked faulted, until someone intervenes")
            if lead:
                message += f"; systemd: {describe_failure(lead)}"
        elif outage.hung:
            title = "PillarHung"
            message = (f"{who} was running but did not answer Core's health check, so Core stopped it and started it "
                       f"again; it could not be reached for {_lasted(ended - outage.since)}")
            if lead:
                message += f"; systemd: {describe_failure(lead)}"
        elif lead:
            title = _TITLES.get(lead.result, "PillarFailed")
            message = f"{who}: {describe_failure(lead)}"
            if lead.stop_requested and lead.result != "timeout":
                message += " while it was being stopped"
            if len(failures) > 1:
                message += f" ({len(failures)} failed runs)"
            if outage.checks:
                message += f"; Core could not reach it for {_lasted(ended - outage.since)}"
        else:
            title = "PillarUnavailable"
            state = next((s for s in outage.states if s and s != "active"), outage.states[-1] if outage.states else "")
            phrase = _STATE_PHRASES.get(state, f"its unit was {state or 'in an unknown state'}")
            message = f"{who} did not answer Core's health check for {_lasted(ended - outage.since)}: {phrase}; no process failed"

        checks = outage.checks[-CHECKS_KEPT:]
        if checks and faulted_after is None:
            checks = [*checks, f"{ended:%H:%M:%S}  answering again"]
        sections: list[tuple[str, list[str]]] = [("Core's checks:", checks)]
        for failure in failures[:1] + failures[1:][-1:]:  # the first and the last failed run
            sections.append((f"systemd, run {failure.invocation[:12]} of {failure.unit}:", failure.systemd_lines))
            sections.append(("What its process logged last:", failure.process_lines))
        if not failures and outage.invocation:
            sections.append((f"What its process logged last (run {outage.invocation[:12]}):", self.journal.process_lines(outage.invocation)))

        self.report(
            self.paths.socket_path("agent"), pillar=name, context=f"systemd unit {unit}", kind=KIND_CRASH, title=title,
            message=message, detail=_fit(sections),
        )
