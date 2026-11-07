"""deployment.flow's Windows-hosted branch (`on_windows=True`): a server with no `systemd --user`
unit to launch, stop or query, tracked instead by a plain PID file
(`Paths.deployment_windows_pid_path`) holding the Windows PID `common/windows_process.py`'s
`start_server`/`run_foreground` reported. Every Windows-side call is faked, same as
`test_windows_process.py` and `test_agent_session_flow.py`'s own Windows-path tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from common import windows_process
from common.paths import Paths
from common.shell import CommandResult
from deployment import db
from deployment.flow import DeploymentError, DeploymentFlow
from deployment.manifest import ServerManifest


class FakeRunner:
    def __init__(self, *, windows_pid=4242, windows_alive=False, windows_start_ok=True, install_ok=True):
        self.windows_pid = windows_pid
        self.windows_alive = windows_alive
        self.windows_start_ok = windows_start_ok
        self.install_ok = install_ok
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[0] == "powershell.exe":
            if not self.windows_start_ok:
                return CommandResult(1, "", "boom")
            if "-EncodedCommand" in argv:
                # install's run_foreground: exit code must reflect install_ok, no PID expected.
                script_index = argv.index("-EncodedCommand") + 1
                import base64
                script = base64.b64decode(argv[script_index]).decode("utf-16-le")
                if "Set-Location" in script:
                    return CommandResult(0 if self.install_ok else 1, "", "" if self.install_ok else "install failed")
            return CommandResult(0, f"{self.windows_pid}\n", "")
        if argv[0] == "tasklist.exe":
            return CommandResult(0, f"claude.exe {self.windows_pid} Console 1 10,000 K\n" if self.windows_alive else
                                  "INFO: No tasks are running which match the specified criteria.\n", "")
        if argv[0] == "taskkill.exe":
            return CommandResult(0, "", "")
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
    fields = dict(name="little-render", command="python -m uvicorn app:app --port 8765", port=8765, match="uvicorn",
                  install=["pip", "install", "-e", "."])
    fields.update(overrides)
    return ServerManifest(**fields)


def make_flow(paths, runner, *, listening=True) -> DeploymentFlow:
    return DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda port: listening,
                           clock=_fake_clock())


def _fake_clock():
    state = {"t": 0.0}

    def clock():
        state["t"] += 0.1
        return state["t"]

    return clock


WINDOWS_PROJECT_DIR = Path("/mnt/c/little-projects/little-render")


def test_install_runs_as_a_native_windows_process(paths, tmp_path):
    runner = FakeRunner(install_ok=True)
    flow = make_flow(paths, runner)

    flow.install(manifest(), cwd=tmp_path / "worktree", on_windows=True)

    assert not any(c[0] == "pip" for c in runner.calls)
    assert any(c[0] == "powershell.exe" for c in runner.calls)


def test_install_raises_on_a_windows_failure(paths, tmp_path):
    runner = FakeRunner(install_ok=False)
    flow = make_flow(paths, runner)
    with pytest.raises(DeploymentError, match="install failed"):
        flow.install(manifest(), cwd=tmp_path / "worktree", on_windows=True)


def test_launch_starts_a_native_windows_process_and_writes_the_pid_file(paths):
    runner = FakeRunner(windows_pid=5555)
    flow = make_flow(paths, runner)

    how = flow.launch("little-render", manifest(), cwd=paths.state_home, on_windows=True)

    assert "5555" in how
    assert not any(c[0] == "systemd-run" for c in runner.calls)
    record = windows_process.read_pid_record(paths.deployment_windows_pid_path("little-render"))
    assert record["pid"] == 5555 and record["started"] and record["cwd"]


def test_launch_raises_when_the_native_start_fails(paths):
    runner = FakeRunner(windows_start_ok=False)
    flow = make_flow(paths, runner)
    with pytest.raises(DeploymentError, match="could not launch"):
        flow.launch("little-render", manifest(), cwd=paths.state_home, on_windows=True)


def test_launch_raises_when_the_port_never_opens(paths):
    runner = FakeRunner()
    flow = make_flow(paths, runner, listening=False)
    with pytest.raises(DeploymentError, match="did not start listening"):
        flow.launch("little-render", manifest(), cwd=paths.state_home, on_windows=True)


def test_pids_reads_the_pid_file_and_checks_liveness(paths):
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    flow = make_flow(paths, FakeRunner(windows_alive=True, windows_pid=4242))

    assert flow._pids("little-render", manifest(), on_windows=True) == [4242]


def test_pids_empty_when_no_pid_file_exists(paths):
    flow = make_flow(paths, FakeRunner())
    assert flow._pids("little-render", manifest(), on_windows=True) == []


def test_pids_empty_when_the_tracked_pid_is_dead(paths):
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    flow = make_flow(paths, FakeRunner(windows_alive=False))

    assert flow._pids("little-render", manifest(), on_windows=True) == []


def test_down_tree_kills_the_tracked_pid_and_removes_the_file(paths):
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    runner = FakeRunner(windows_alive=True, windows_pid=4242)
    flow = make_flow(paths, runner, listening=False)

    report = flow.down("little-render", manifest(), on_windows=True, tailscale_off=False)

    assert report.pids_killed == [4242]
    assert ["taskkill.exe", "/PID", "4242", "/T", "/F"] in runner.calls
    assert not pid_path.exists()


def test_down_is_a_clean_noop_when_nothing_is_tracked(paths):
    flow = make_flow(paths, FakeRunner(), listening=False)
    report = flow.down("little-render", manifest(), on_windows=True, tailscale_off=False)
    assert report.pids_killed == []


def test_status_reports_active_with_pids_when_the_windows_process_is_alive(paths, conn):
    pid_path = paths.deployment_windows_pid_path("little-render")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4242")
    flow = make_flow(paths, FakeRunner(windows_alive=True, windows_pid=4242))

    result = flow.status(conn, "little-render", manifest(), on_windows=True)

    assert result.unit == "active"
    assert result.pids == [4242]
    assert result.running is True


def test_status_reports_inactive_when_nothing_is_tracked(paths, conn):
    flow = make_flow(paths, FakeRunner())
    result = flow.status(conn, "little-render", manifest(), on_windows=True)
    assert result.unit == "inactive"
    assert result.pids == []
