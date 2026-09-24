"""agent.session_flow — ad-hoc, human-driven Remote Control sessions
(M16 `little-runner` parity: `lrun session start/stop/list`). Every
external process call is faked, matching the rest of this project's own
test style for expensive/non-deterministic externals (real `dtach`,
`systemd-run`, and `claude` are never actually invoked)."""

from __future__ import annotations

import json

import pytest

from agent import session_flow as session_flow_module
from agent.session_flow import SessionError, SessionFlow, claude_config_path, is_trusted, session_name
from common.paths import Paths
from common.shell import CommandResult


class FakeRunner:
    def __init__(self, *, dtach_available=True, running=False, start_ok=True, stop_ok=True):
        self.dtach_available = dtach_available
        self.running = running
        self.start_ok = start_ok
        self.stop_ok = stop_ok
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv == ["which", "dtach"]:
            return CommandResult(0 if self.dtach_available else 1, "", "")
        if argv[0] == "pgrep":
            return CommandResult(0 if self.running else 1, "", "")
        if argv[0] == "systemd-run":
            return CommandResult(0 if self.start_ok else 1, "", "boom" if not self.start_ok else "")
        return CommandResult(0, "", "")


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_session_name_uses_the_short_project_name():
    assert session_name("dfluxcbf/little-widget") == "lws-little-widget-session"
    assert session_name("little-widget") == "lws-little-widget-session"


def test_is_trusted_reads_claude_json(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    project_dir = tmp_path / "proj"
    config.write_text(json.dumps({"projects": {str(project_dir): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")
    monkeypatch.setattr(session_flow_module, "claude_config_path", lambda: config)
    assert is_trusted(project_dir) is True


def test_is_trusted_false_when_not_accepted(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    project_dir = tmp_path / "proj"
    config.write_text(json.dumps({"projects": {str(project_dir): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    monkeypatch.setattr(session_flow_module, "claude_config_path", lambda: config)
    assert is_trusted(project_dir) is False


def test_is_trusted_false_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "claude_config_path", lambda: tmp_path / "nonexistent.json")
    assert is_trusted(tmp_path / "proj") is False


def test_is_trusted_false_when_config_unreadable_json(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    config.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(session_flow_module, "claude_config_path", lambda: config)
    assert is_trusted(tmp_path / "proj") is False


def test_start_fails_when_dtach_missing(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    flow = SessionFlow(paths, runner=FakeRunner(dtach_available=False))
    with pytest.raises(SessionError, match="dtach is not installed"):
        flow.start("little-widget", tmp_path)


def test_start_fails_when_already_running(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    flow = SessionFlow(paths, runner=FakeRunner(running=True))
    with pytest.raises(SessionError, match="already running"):
        flow.start("little-widget", tmp_path)


def test_start_fails_when_not_trusted(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: False)
    flow = SessionFlow(paths, runner=FakeRunner())
    with pytest.raises(SessionError, match="not trusted"):
        flow.start("little-widget", tmp_path)


def test_start_succeeds_and_returns_session_name(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    runner = FakeRunner()
    flow = SessionFlow(paths, runner=runner)
    name = flow.start("little-widget", tmp_path)
    assert name == "lws-little-widget-session"
    launch = next(c for c in runner.calls if c[0] == "systemd-run")
    assert launch[:5] == ["systemd-run", "--user", "--collect", "--scope", "--unit=little-workshop-agent-session-little-widget"]
    assert "dtach" in launch
    assert "claude" in launch
    assert "--remote-control" in launch
    assert name in launch


def test_start_raises_on_launch_failure(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    flow = SessionFlow(paths, runner=FakeRunner(start_ok=False))
    with pytest.raises(SessionError, match="could not start a session"):
        flow.start("little-widget", tmp_path)


def test_start_removes_a_stale_socket_file(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    socket = paths.session_socket_path("little-widget")
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.write_text("stale", encoding="utf-8")

    flow = SessionFlow(paths, runner=FakeRunner())
    flow.start("little-widget", tmp_path)
    # dtach itself is faked, so the socket isn't recreated -- the point
    # proven here is that start() didn't refuse to proceed over the
    # leftover file.
    assert not socket.exists() or socket.read_text() != "stale"


def test_is_running_matches_the_deterministic_socket_path(paths):
    runner = FakeRunner(running=True)
    flow = SessionFlow(paths, runner=runner)
    assert flow.is_running("little-widget") is True
    pgrep_call = runner.calls[0]
    assert pgrep_call[0] == "pgrep"
    assert str(paths.session_socket_path("little-widget")) in pgrep_call[2]


def test_stop_returns_false_when_not_running(paths):
    flow = SessionFlow(paths, runner=FakeRunner(running=False))
    assert flow.stop("little-widget") is False


def test_stop_stops_the_scope_and_pkills_when_running(paths):
    runner = FakeRunner(running=True)
    flow = SessionFlow(paths, runner=runner)
    assert flow.stop("little-widget") is True

    argvs = runner.calls
    assert ["systemctl", "--user", "stop", "little-workshop-agent-session-little-widget"] in argvs
    assert ["systemctl", "--user", "reset-failed", "little-workshop-agent-session-little-widget"] in argvs
    assert any(c[:3] == ["pkill", "-TERM", "-f"] for c in argvs)
    assert any(c[0] == "pkill" and "claude --remote-control lws-little-widget-session" in c[-1] for c in argvs)


class NpxRunner(FakeRunner):
    def __init__(self, *, npx: bool, **kw):
        super().__init__(**kw)
        self.npx = npx

    def run(self, argv, timeout=None, cwd=None):
        if argv == ["which", "npx"]:
            self.calls.append(argv)
            return CommandResult(0 if self.npx else 1, "", "")
        return super().run(argv, timeout=timeout, cwd=cwd)


def test_start_attaches_the_playwright_mcp_server(paths, tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    runner = NpxRunner(npx=True)
    SessionFlow(paths, runner=runner).start("dfluxcbf/little-widget", tmp_path)
    launch = next(c for c in runner.calls if c[0] == "systemd-run")
    assert launch[-2] == "--mcp-config"
    server = json.loads(launch[-1])["mcpServers"]["playwright"]
    out_dir = server["args"][server["args"].index("--output-dir") + 1]
    assert out_dir == str(paths.sessions_dir / "playwright-mcp" / "dfluxcbf-little-widget")


def test_start_without_npx_or_when_disabled_has_no_playwright(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(session_flow_module, "is_trusted", lambda project_dir: True)
    for runner, enabled in ((NpxRunner(npx=False), True), (NpxRunner(npx=True), False)):
        SessionFlow(paths, runner=runner, playwright_mcp=enabled).start("little-widget", tmp_path)
        launch = next(c for c in runner.calls if c[0] == "systemd-run")
        assert "--mcp-config" not in launch
        assert launch[-1] == "lws-little-widget-session"
