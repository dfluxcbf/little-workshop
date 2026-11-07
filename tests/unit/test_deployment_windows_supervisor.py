"""deployment.windows_supervisor.WindowsServerSupervisor — restart-on-failure for a Windows-hosted
server, polling its tracked PID (there is no systemd unit to delegate that to). `tick()` is tested
directly, one deterministic pass at a time; `run_forever` is just a timer loop around it."""

from __future__ import annotations

import threading

import pytest

from common import windows_process
from common.paths import Paths
from common.shell import CommandResult
from deployment import db
from deployment.flow import DeploymentFlow
from deployment.manifest import ServerManifest
from deployment.windows_supervisor import WindowsServerSupervisor


class FakeRunner:
    def __init__(self, *, windows_alive=False, windows_pid=4242, start_ok=True):
        self.windows_alive = windows_alive
        self.windows_pid = windows_pid
        self.start_ok = start_ok
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[0] == "tasklist.exe":
            return CommandResult(0, f"x.exe {self.windows_pid}\n" if self.windows_alive else "INFO: none\n", "")
        if argv[0] == "powershell.exe":
            return CommandResult(0 if self.start_ok else 1, f"{self.windows_pid}\n" if self.start_ok else "", "" if self.start_ok else "boom")
        return CommandResult(0, "", "")


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


def manifest(**overrides) -> ServerManifest:
    fields = dict(name="little-render", command="python -m uvicorn app:app", port=8765, match="uvicorn")
    fields.update(overrides)
    return ServerManifest(**fields)


def make_supervisor(paths, conn, runner, *, load_manifest=None) -> WindowsServerSupervisor:
    flow = DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda port: True)
    return WindowsServerSupervisor(paths, flow, conn, load_manifest or (lambda paths, project_id: manifest()), threading.Lock())


def test_tick_does_nothing_when_no_server_state_is_cached(paths, conn):
    supervisor = make_supervisor(paths, conn, FakeRunner())
    assert supervisor.tick() == []


def test_tick_does_nothing_for_a_project_with_no_windows_pid_file(paths, conn):
    db.save_server_state(conn, "little-meals", "dev", "1.0.0", "abc123")
    supervisor = make_supervisor(paths, conn, FakeRunner())
    assert supervisor.tick() == []


def test_tick_leaves_an_alive_server_alone(paths, conn):
    db.save_server_state(conn, "little-render", "dev", "1.0.0", "abc123")
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    runner = FakeRunner(windows_alive=True, windows_pid=4242)
    supervisor = make_supervisor(paths, conn, runner)

    assert supervisor.tick() == []
    assert not any(c[0] == "powershell.exe" for c in runner.calls)
    assert pid_path.read_text() == "4242"


def test_tick_relaunches_a_dead_server_and_updates_the_pid_file(paths, conn):
    db.save_server_state(conn, "little-render", "dev", "1.0.0", "abc123")
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    runner = FakeRunner(windows_alive=False, windows_pid=5555)
    supervisor = make_supervisor(paths, conn, runner)

    assert supervisor.tick() == ["little-render"]

    assert windows_process.read_pid_record(pid_path)["pid"] == 5555
    assert any(c[0] == "powershell.exe" for c in runner.calls)


def test_tick_skips_a_dead_server_whose_manifest_no_longer_loads(paths, conn):
    db.save_server_state(conn, "little-render", "dev", "1.0.0", "abc123")
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")

    def missing_manifest(paths, project_id):
        raise FileNotFoundError("no manifest")

    supervisor = make_supervisor(paths, conn, FakeRunner(windows_alive=False), load_manifest=missing_manifest)

    assert supervisor.tick() == []
    # Left as-is for the next tick to retry, rather than guessing at a relaunch with no manifest.
    assert pid_path.read_text() == "4242"


def test_tick_ignores_a_pid_file_that_is_not_a_plain_integer(paths, conn):
    db.save_server_state(conn, "little-render", "dev", "1.0.0", "abc123")
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("not-a-pid")
    supervisor = make_supervisor(paths, conn, FakeRunner())

    assert supervisor.tick() == []


def test_tick_relaunches_only_the_dead_one_among_several_tracked_servers(paths, conn):
    db.save_server_state(conn, "little-render", "dev", "1.0.0", "abc123")
    db.save_server_state(conn, "little-library", "dev", "1.0.0", "def456")
    for name, pid in (("little-render", 4242), ("little-library", 9999)):
        p = paths.deployment_windows_pid_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(pid))

    class SelectiveRunner(FakeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "tasklist.exe":
                self.calls.append(argv)
                return CommandResult(0, "x.exe 9999\n" if "9999" in argv[2] else "INFO: none\n", "")
            return super().run(argv, timeout=timeout, cwd=cwd)

    runner = SelectiveRunner(windows_pid=4242)
    supervisor = make_supervisor(paths, conn, runner)

    assert supervisor.tick() == ["little-render"]
    assert paths.deployment_windows_pid_path("little-library").read_text() == "9999"


def test_stop_ends_run_forever_promptly():
    supervisor = WindowsServerSupervisor.__new__(WindowsServerSupervisor)
    supervisor._stop = threading.Event()
    supervisor.interval = 9999  # would hang the test if stop() did not interrupt the wait
    supervisor.tick = lambda: []
    thread = threading.Thread(target=supervisor.run_forever, daemon=True)
    thread.start()
    supervisor.stop()
    thread.join(timeout=2)
    assert not thread.is_alive()
