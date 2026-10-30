"""Every pillar outage Core sees, and every abnormal end of a unit's run that systemd logs, becomes one
incident of kind "crash" that says what happened and why."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from common.manifest import Manifest, PillarEntry
from common.paths import Paths
from common.shell import CommandResult
from core.health import HealthResult
from core.journal import FAILURE_MESSAGE_ID, PROCESS_EXIT_MESSAGE_ID, STOPPING_MESSAGE_ID, Journal, UnitFailure
from core.registry import PillarStatus
from core.supervisor import Supervisor


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


class FakeSystemd:
    """`systemctl is-active`, `systemctl show` and `systemd-run`, answered from what a test sets."""

    def __init__(self):
        self.active: dict[str, bool] = {}
        self.states: dict[str, str] = {}
        self.refuse_start: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0, "active\n" if self.active.get(argv[3]) else "deactivating\n", "")
        if argv[:3] == ["systemctl", "--user", "show"]:
            return CommandResult(0, self.states.get(argv[3], ""), "")
        if argv[0] == "systemd-run":
            unit = next(a for a in argv if a.startswith("--unit=")).removeprefix("--unit=") + ".service"
            if unit in self.refuse_start:
                return CommandResult(1, "", self.refuse_start[unit])
            return CommandResult(0, "", "")
        return CommandResult(0, "", "")


class FakeJournal:
    def __init__(self):
        self.pending: list[UnitFailure] = []
        self.lines: dict[str, list[str]] = {}
        self.asked: list[list[str]] = []

    def failures(self, units):
        self.asked.append(sorted(units))
        found, self.pending = self.pending, []
        return found

    def process_lines(self, invocation, *, keep=25):
        return self.lines.get(invocation, [])


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 5, 19, 49, 30)

    def __call__(self):
        return self.now


def make(paths, *pillars, max_attempts=10):
    manifest = Manifest(pillars={p: PillarEntry(name=p, enabled=True, command=["python3", "-m", p]) for p in pillars})
    manifest.crash_relaunch_max_attempts = max_attempts
    systemd, journal, clock, reported, health = FakeSystemd(), FakeJournal(), Clock(), [], {}
    supervisor = Supervisor(
        paths=paths, manifest=manifest, runner=systemd, journal=journal, clock=clock,
        probe=lambda socket_path, timeout, max_retries: health.get(socket_path.stem, HealthResult(ok=False, attempts=10, error="[Errno 111] Connection refused")),
        report=lambda target, **kw: reported.append((target, kw)),
    )
    return supervisor, systemd, journal, clock, reported, health


def healthy():
    return HealthResult(ok=True, attempts=1, report={"ok": True})


# --- REQ-000000400: Every abnormal end of a pillar's process is a crash incident ---


def test_a_stop_that_ended_in_sigkill_is_a_crash_incident_that_says_why(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "orchestration", "policy")
    systemd.active = {"little-workshop-orchestration.service": True, "little-workshop-policy.service": True}
    health.update(orchestration=healthy(), policy=healthy())  # both already back under a new unit run
    journal.pending = [UnitFailure(
        unit="little-workshop-orchestration.service", invocation="da3eddcafafa44c9", result="timeout",
        at=datetime(2026, 10, 4, 19, 40, 41), exit_code="killed", exit_status="9", stop_requested=True,
        systemd_lines=["19:39:10 Stopping little-workshop-orchestration.service...",
                       "19:40:41 little-workshop-orchestration.service: State 'stop-sigterm' timed out. Killing."],
        process_lines=["19:39:10 INFO:     Shutting down", "19:39:10 INFO:     Waiting for connections to close. (CTRL+C to force quit)"],
    )]

    supervisor.reconcile_once()

    assert journal.asked == [["little-workshop-orchestration.service", "little-workshop-policy.service", "little-workshop.service"]]
    ((target, kw),) = reported
    assert target == paths.socket_path("agent")
    assert kw["kind"] == "crash" and kw["pillar"] == "orchestration" and kw["title"] == "PillarTimedOut"
    assert kw["context"] == "systemd unit little-workshop-orchestration.service"
    assert kw["message"] == "the orchestration pillar: it overran systemd's stop timeout and was killed (SIGKILL)"
    assert "State 'stop-sigterm' timed out" in kw["detail"] and "Waiting for connections to close" in kw["detail"]
    assert supervisor.registry.get("orchestration").status == PillarStatus.HEALTHY


def test_a_crash_systemd_restarted_and_cores_own_crash_are_filed(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "agent")
    systemd.active = {"little-workshop-agent.service": True}
    health["agent"] = healthy()
    at = datetime(2026, 10, 5, 19, 0)
    journal.pending = [
        UnitFailure(unit="little-workshop-agent.service", invocation="a1", result="exit-code", at=at, exit_code="exited", exit_status="1",
                    process_lines=["19:00:00 ValueError: bad config"]),
        UnitFailure(unit="little-workshop.service", invocation="c1", result="signal", at=at, exit_code="killed", exit_status="11"),
    ]

    supervisor.reconcile_once()

    by_pillar = {kw["pillar"]: kw for _target, kw in reported}
    assert set(by_pillar) == {"agent", "core"}
    assert by_pillar["agent"]["title"] == "PillarCrashed" and "exited with status 1" in by_pillar["agent"]["message"]
    assert "ValueError: bad config" in by_pillar["agent"]["detail"]
    assert by_pillar["core"]["title"] == "PillarKilled" and by_pillar["core"]["message"] == "Core: its process was killed by SIGSEGV"
    assert by_pillar["core"]["context"] == "systemd unit little-workshop.service"


def test_a_process_left_behind_in_the_stopping_unit_is_named():
    from core.supervisor import describe_failure

    failure = UnitFailure(
        unit="little-workshop-policy.service", invocation="7cfc", result="timeout", at=datetime(2026, 10, 4, 19, 42, 25),
        stop_requested=True, systemd_lines=[
            "19:42:25 little-workshop-policy.service: State 'final-sigterm' timed out. Killing.",
            "19:42:25 little-workshop-policy.service: Killing process 1066563 (docker) with signal SIGKILL.",
        ],
    )

    assert describe_failure(failure) == ("its process exited when asked to stop, but one it left behind (docker) did not, "
                                         "so systemd killed it after the stop timeout")


class FakeJournalctl:
    """`journalctl --user -o json` over a fixed set of entries."""

    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        matches = [a for a in argv if "=" in a and not a.startswith("-")]
        fields: dict[str, set[str]] = {}
        for match in matches:
            key, value = match.split("=", 1)
            fields.setdefault(key, set()).add(value)
        chosen = [e for e in self.entries if all(e.get(k) in values for k, values in fields.items())]
        return CommandResult(0, "".join(json.dumps(e) + "\n" for e in chosen), "")


def _entry(cursor, at, message, **fields):
    return {"__CURSOR": cursor, "__REALTIME_TIMESTAMP": str(int(at.timestamp() * 1e6)), "MESSAGE": message, **fields}


def test_the_journal_is_read_from_where_it_stopped_and_tells_the_whole_run(tmp_path):
    at = datetime(2026, 10, 4, 19, 40, 41)
    unit, run = "little-workshop-policy.service", "7cfc693b"
    entries = [
        _entry("c1", at - timedelta(seconds=90), "Stopping little-workshop-policy.service...", MESSAGE_ID=STOPPING_MESSAGE_ID, USER_UNIT=unit, USER_INVOCATION_ID=run),
        _entry("c2", at, "little-workshop-policy.service: State 'final-sigterm' timed out. Killing.", USER_UNIT=unit, USER_INVOCATION_ID=run),
        _entry("c3", at, "Main process exited", MESSAGE_ID=PROCESS_EXIT_MESSAGE_ID, EXIT_CODE="killed", EXIT_STATUS="9", USER_UNIT=unit, USER_INVOCATION_ID=run),
        _entry("c4", at, "little-workshop-policy.service: Failed with result 'timeout'.", MESSAGE_ID=FAILURE_MESSAGE_ID, UNIT_RESULT="timeout",
               USER_UNIT=unit, USER_INVOCATION_ID=run),
        _entry("p1", at - timedelta(seconds=91), 'INFO:      - "GET /v1/health HTTP/1.1" 200 OK', _SYSTEMD_INVOCATION_ID=run),
        _entry("p2", at - timedelta(seconds=90), "INFO:     Finished server process [910067]", _SYSTEMD_INVOCATION_ID=run),
    ]
    runner, clock = FakeJournalctl(entries), [1_000_000.0]
    position = tmp_path / "core-journal.json"

    assert Journal(position, runner=runner, clock=lambda: clock[0]).failures([unit]) == []  # the first read only starts here
    clock[0] += 60
    (failure,) = Journal(position, runner=runner, clock=lambda: clock[0]).failures([unit])  # a new Core: same position

    since = next(a for a in runner.calls[-3] if a.startswith("--since=@"))
    assert since == "--since=@999995"  # where the last read ended, a little before
    assert (failure.unit, failure.invocation, failure.result) == (unit, run, "timeout")
    assert (failure.exit_code, failure.exit_status, failure.stop_requested) == ("killed", "9", True)
    assert any("final-sigterm" in line for line in failure.systemd_lines)
    assert failure.process_lines == [f"{at - timedelta(seconds=90):%H:%M:%S} INFO:     Finished server process [910067]"]  # no access log
    assert Journal(position, runner=runner, clock=lambda: clock[0] + 60).failures([unit]) == []  # each failure once


# --- REQ-000000401: A pillar shown crashed or faulted gets an incident that explains it ---


def test_a_pillar_shown_crashed_while_a_redeploy_stopped_it_gets_one_incident_once_it_answers(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "dashboard")
    unit = "little-workshop-dashboard.service"
    systemd.states[unit] = "ActiveState=deactivating\nSubState=stop-sigterm\nInvocationID=2484849abc\nLoadState=loaded\n"
    systemd.refuse_start[unit] = f"Failed to start transient service unit: Unit {unit} was already loaded or has a fragment file."
    journal.lines["2484849abc"] = ["19:49:24 INFO:     Shutting down", "19:49:24 INFO:     Waiting for connections to close. (CTRL+C to force quit)"]

    supervisor.reconcile_once()  # caught mid-stop
    assert supervisor.registry.get("dashboard").status == PillarStatus.CRASHED and reported == []  # the outage is still open

    clock.now += timedelta(seconds=58)
    systemd.active[unit] = True
    health["dashboard"] = healthy()
    supervisor.reconcile_once()
    supervisor.reconcile_once()  # still healthy: nothing more

    ((_target, kw),) = reported
    assert kw["kind"] == "crash" and kw["pillar"] == "dashboard" and kw["title"] == "PillarUnavailable"
    assert kw["message"] == ("the dashboard pillar did not answer Core's health check for 58 s: its unit was being stopped "
                             "(a redeploy or a stop was under way); no process failed")
    detail = kw["detail"]
    assert "unit deactivating/stop-sigterm" in detail and "not relaunched: it is being started or stopped" in detail
    assert "answering again" in detail and "Waiting for connections to close" in detail
    assert not any(call[0] == "systemd-run" for call in systemd.calls)  # never raced the redeploy


def test_a_pillar_marked_faulted_is_filed_at_once(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "version", max_attempts=1)
    systemd.states["little-workshop-version.service"] = "ActiveState=inactive\nSubState=dead\nInvocationID=\nLoadState=not-found\n"

    supervisor.reconcile_once()  # relaunch 1 fails
    clock.now += timedelta(minutes=1)
    supervisor.reconcile_once()  # no more: faulted

    assert supervisor.registry.get("version").status == PillarStatus.FAULTED
    ((_target, kw),) = reported
    assert kw["title"] == "PillarFaulted" and kw["pillar"] == "version"
    assert kw["message"].startswith("Core gave up on the version pillar after 1 relaunch in a row failed")
    assert "unit not active; unit inactive/dead; relaunched; still not answering" in kw["detail"]
    assert "marked faulted" in kw["detail"]


# --- REQ-000000408: A pillar that runs but does not answer is restarted ---


def test_a_pillar_running_for_a_while_but_not_answering_is_stopped_and_started_again(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "registry")
    unit = "little-workshop-registry.service"
    systemd.active[unit] = True
    systemd.states[unit] = "ActiveState=active\nSubState=running\nInvocationID=hung1\nActiveEnterTimestampMonotonic=100000000\n"
    supervisor.monotonic = lambda: 1000.0  # active for 900 s
    answers = iter([HealthResult(ok=False, attempts=10, error="timed out"), healthy()])
    supervisor.probe = lambda socket_path, timeout, max_retries: next(answers)

    supervisor.reconcile_once()

    commands = [call[:3] for call in systemd.calls]
    stop, start = commands.index(["systemctl", "--user", "stop"]), next(i for i, c in enumerate(commands) if c[0] == "systemd-run")
    assert stop < start  # the hung process goes before a new one starts
    assert supervisor.registry.get("registry").status == PillarStatus.HEALTHY
    ((_target, kw),) = reported
    assert kw["title"] == "PillarHung" and kw["pillar"] == "registry"
    assert kw["message"].startswith("the registry pillar was running but did not answer Core's health check, so Core stopped it and started it again")
    assert "hung: stopped and relaunched" in kw["detail"]


@pytest.mark.parametrize("state, active", [
    ("ActiveState=deactivating\nSubState=stop-sigterm\n", False),  # a redeploy or a stop under way
    ("ActiveState=activating\nSubState=start\n", False),  # systemd starting it
    ("ActiveState=active\nSubState=running\nActiveEnterTimestampMonotonic=990000000\n", True),  # up 10 s ago, still starting up
])
def test_a_unit_being_started_or_stopped_is_left_alone(paths, state, active):
    supervisor, systemd, journal, clock, reported, health = make(paths, "agent")
    unit = "little-workshop-agent.service"
    systemd.active[unit] = active
    systemd.states[unit] = state
    supervisor.monotonic = lambda: 1000.0

    supervisor.reconcile_once()

    assert not any(call[0] == "systemd-run" or call[:3] == ["systemctl", "--user", "stop"] for call in systemd.calls)
    assert supervisor.registry.get("agent").status == PillarStatus.CRASHED
    assert supervisor._outages["agent"].checks[-1].endswith("not relaunched: it is being started or stopped")


def test_a_failed_unit_is_reset_before_it_is_started_again(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "build")
    systemd.states["little-workshop-build.service"] = "ActiveState=failed\nSubState=failed\n"

    supervisor.reconcile_once()

    commands = [call[:3] for call in systemd.calls]
    reset = commands.index(["systemctl", "--user", "reset-failed"])
    assert reset < next(i for i, c in enumerate(commands) if c[0] == "systemd-run")


def test_a_pillar_that_stays_healthy_is_never_an_incident(paths):
    supervisor, systemd, journal, clock, reported, health = make(paths, "registry")
    systemd.active["little-workshop-registry.service"] = True
    health["registry"] = healthy()

    supervisor.startup()
    supervisor.reconcile_once()

    assert reported == []
