"""Every pillar's `python -m <pillar>` entrypoint — previously 0% covered
anywhere. Each one wires a DB connection (where the pillar has one) and a
FastAPI app to a Unix-domain-socket uvicorn server through
`common.serving.serve`; `mcp_server`'s is the one exception (a stdio server,
no socket, never Core-launched). None of these should ever really serve in a
test: `serve` runs (it removes a stale socket and builds the uvicorn config)
but its server is replaced to capture what it was given instead of serving
forever, and `mcp_server.server.main` is monkeypatched."""

from __future__ import annotations

import pytest

from common.paths import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def captured(monkeypatch):
    """What an entrypoint serves: its app, its socket and the stop's bound; `on_run`, when a test sets
    it, runs where the server would start serving."""
    from common import serving

    seen = {}

    class FakeServer:
        def __init__(self, config):
            seen.update(app=config.app, uds=config.uds, timeout_graceful_shutdown=config.timeout_graceful_shutdown)
            self.started = True

        def run(self):
            if "on_run" in seen:
                seen["on_run"]()

    monkeypatch.setattr(serving, "_Server", FakeServer)
    return seen


def test_core_main_builds_app_and_serves_on_the_core_socket(paths, monkeypatch, captured):
    import core.__main__ as core_main
    from core.supervisor import Supervisor

    monkeypatch.setattr(core_main.Paths, "from_env", staticmethod(lambda: paths))
    monkeypatch.setattr(Supervisor, "startup", lambda self: None)
    monkeypatch.setattr(Supervisor, "reconcile_once", lambda self: None)


    core_main.main()

    assert captured["uds"] == str(paths.core_socket_path)
    assert captured["app"] is not None


def test_orchestration_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import orchestration.__main__ as orch_main

    monkeypatch.setattr(orch_main.Paths, "from_env", staticmethod(lambda: paths))


    orch_main.main()

    assert captured["uds"] == str(paths.socket_path("orchestration"))
    assert captured["app"] is not None


def test_orchestration_main_removes_a_stale_socket_file_first(paths, monkeypatch, captured):
    import orchestration.__main__ as orch_main

    monkeypatch.setattr(orch_main.Paths, "from_env", staticmethod(lambda: paths))
    stale = paths.socket_path("orchestration")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")


    orch_main.main()

    assert not stale.exists() or stale.read_text() != "stale"


def test_registry_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import registry.__main__ as registry_main

    monkeypatch.setattr(registry_main.Paths, "from_env", staticmethod(lambda: paths))


    registry_main.main()

    assert captured["uds"] == str(paths.socket_path("registry"))
    assert captured["app"] is not None


def test_version_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import version.__main__ as version_main

    monkeypatch.setattr(version_main.Paths, "from_env", staticmethod(lambda: paths))


    version_main.main()

    assert captured["uds"] == str(paths.socket_path("version"))
    assert captured["app"] is not None


def test_deployment_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import deployment.__main__ as deployment_main

    monkeypatch.setattr(deployment_main.Paths, "from_env", staticmethod(lambda: paths))


    deployment_main.main()

    assert captured["uds"] == str(paths.socket_path("deployment"))
    assert captured["app"] is not None


def test_agent_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import agent.__main__ as agent_main

    monkeypatch.setattr(agent_main.Paths, "from_env", staticmethod(lambda: paths))


    agent_main.main()

    assert captured["uds"] == str(paths.socket_path("agent"))
    assert captured["app"] is not None


def test_agent_main_fails_runs_a_previous_process_left_running_before_serving(paths, monkeypatch, captured):
    import agent.__main__ as agent_main
    from agent import db as agent_db

    conn = agent_db.connect(paths.pillar_db_path("agent"))
    agent_db.create_run(conn, "orphan", job_id=None, project_id="proj", intent="task",
                        review_mode="auto_approve", title="t", source={"type": "manual"})
    conn.close()
    monkeypatch.setattr(agent_main.Paths, "from_env", staticmethod(lambda: paths))

    status_when_serving = {}

    def when_serving():
        check = agent_db.connect(paths.pillar_db_path("agent"))
        status_when_serving["orphan"] = agent_db.get_run(check, "orphan")["status"]
        check.close()

    captured["on_run"] = when_serving

    agent_main.main()

    assert status_when_serving["orphan"] == agent_db.FAILED


def test_dashboard_main_builds_app_and_serves_on_its_socket(paths, monkeypatch, captured):
    import dashboard.__main__ as dashboard_main

    monkeypatch.setattr(dashboard_main.Paths, "from_env", staticmethod(lambda: paths))


    dashboard_main.main()

    assert captured["uds"] == str(paths.socket_path("dashboard"))
    assert captured["app"] is not None


def test_dashboard_main_removes_a_stale_socket_file_first(paths, monkeypatch, captured):
    import dashboard.__main__ as dashboard_main

    monkeypatch.setattr(dashboard_main.Paths, "from_env", staticmethod(lambda: paths))
    stale = paths.socket_path("dashboard")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")


    dashboard_main.main()

    assert not stale.exists() or stale.read_text() != "stale"


def test_mcp_server_main_delegates_to_server_main_and_returns_its_exit_code(monkeypatch):
    import mcp_server.__main__ as mcp_main

    calls = []
    monkeypatch.setattr(mcp_main, "main", lambda: (calls.append(1), 0)[1])

    assert mcp_main.main() == 0
    assert calls == [1]


def test_orchestration_socket_lookup_returns_none_when_core_unreachable(paths):
    import orchestration.__main__ as orch_main

    lookup = orch_main.make_core_socket_lookup(paths)
    assert lookup("registry") is None  # nothing listening on paths.core_socket_path


def test_every_pillar_main_creates_the_state_directory_layout(paths, monkeypatch, captured):
    """A sanity check that from_env()/ensure_layout() actually ran before
    any pillar tries to open its db/socket — every *_main test above
    depends on this implicitly; this makes the dependency explicit."""
    import registry.__main__ as registry_main

    monkeypatch.setattr(registry_main.Paths, "from_env", staticmethod(lambda: paths))

    registry_main.main()

    assert paths.pillar_db_path("registry").parent.exists()
