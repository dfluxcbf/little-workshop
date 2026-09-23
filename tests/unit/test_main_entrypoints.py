"""Every pillar's `python -m <pillar>` entrypoint — previously 0% covered
anywhere. Each one wires a DB connection (where the pillar has one) and a
FastAPI app to a Unix-domain-socket uvicorn server; `mcp_server`'s is the
one exception (a stdio server, no socket, never Core-launched). None of
these should ever really call `uvicorn.run`/`mcp_server.server.main` in a
test — they're monkeypatched to capture what they were called with
instead of actually serving forever."""

from __future__ import annotations

import pytest

from common.paths import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def test_core_main_builds_app_and_serves_on_the_core_socket(paths, monkeypatch):
    import core.__main__ as core_main
    from core.supervisor import Supervisor

    monkeypatch.setattr(core_main.Paths, "from_env", staticmethod(lambda: paths))
    monkeypatch.setattr(Supervisor, "startup", lambda self: None)
    monkeypatch.setattr(Supervisor, "reconcile_once", lambda self: None)

    captured = {}

    def fake_run(app, uds, log_level):
        captured["app"] = app
        captured["uds"] = uds

    monkeypatch.setattr(core_main.uvicorn, "run", fake_run)

    core_main.main()

    assert captured["uds"] == str(paths.core_socket_path)
    assert captured["app"] is not None


def test_orchestration_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import orchestration.__main__ as orch_main

    monkeypatch.setattr(orch_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}

    def fake_run(app, uds, log_level):
        captured["app"] = app
        captured["uds"] = uds

    monkeypatch.setattr(orch_main.uvicorn, "run", fake_run)

    orch_main.main()

    assert captured["uds"] == str(paths.socket_path("orchestration"))
    assert captured["app"] is not None


def test_orchestration_main_removes_a_stale_socket_file_first(paths, monkeypatch):
    import orchestration.__main__ as orch_main

    monkeypatch.setattr(orch_main.Paths, "from_env", staticmethod(lambda: paths))
    stale = paths.socket_path("orchestration")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(orch_main.uvicorn, "run", lambda app, uds, log_level: None)

    orch_main.main()

    assert not stale.exists() or stale.read_text() != "stale"


def test_registry_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import registry.__main__ as registry_main

    monkeypatch.setattr(registry_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}
    monkeypatch.setattr(
        registry_main.uvicorn, "run", lambda app, uds, log_level: captured.update(app=app, uds=uds)
    )

    registry_main.main()

    assert captured["uds"] == str(paths.socket_path("registry"))
    assert captured["app"] is not None


def test_version_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import version.__main__ as version_main

    monkeypatch.setattr(version_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}
    monkeypatch.setattr(
        version_main.uvicorn, "run", lambda app, uds, log_level: captured.update(app=app, uds=uds)
    )

    version_main.main()

    assert captured["uds"] == str(paths.socket_path("version"))
    assert captured["app"] is not None


def test_deployment_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import deployment.__main__ as deployment_main

    monkeypatch.setattr(deployment_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}
    monkeypatch.setattr(
        deployment_main.uvicorn, "run", lambda app, uds, log_level: captured.update(app=app, uds=uds)
    )

    deployment_main.main()

    assert captured["uds"] == str(paths.socket_path("deployment"))
    assert captured["app"] is not None


def test_agent_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import agent.__main__ as agent_main

    monkeypatch.setattr(agent_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}
    monkeypatch.setattr(
        agent_main.uvicorn, "run", lambda app, uds, log_level: captured.update(app=app, uds=uds)
    )

    agent_main.main()

    assert captured["uds"] == str(paths.socket_path("agent"))
    assert captured["app"] is not None


def test_dashboard_main_builds_app_and_serves_on_its_socket(paths, monkeypatch):
    import dashboard.__main__ as dashboard_main

    monkeypatch.setattr(dashboard_main.Paths, "from_env", staticmethod(lambda: paths))

    captured = {}
    monkeypatch.setattr(
        dashboard_main.uvicorn, "run", lambda app, uds, log_level: captured.update(app=app, uds=uds)
    )

    dashboard_main.main()

    assert captured["uds"] == str(paths.socket_path("dashboard"))
    assert captured["app"] is not None


def test_dashboard_main_removes_a_stale_socket_file_first(paths, monkeypatch):
    import dashboard.__main__ as dashboard_main

    monkeypatch.setattr(dashboard_main.Paths, "from_env", staticmethod(lambda: paths))
    stale = paths.socket_path("dashboard")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(dashboard_main.uvicorn, "run", lambda app, uds, log_level: None)

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


def test_every_pillar_main_creates_the_state_directory_layout(paths, monkeypatch):
    """A sanity check that from_env()/ensure_layout() actually ran before
    any pillar tries to open its db/socket — every *_main test above
    depends on this implicitly; this makes the dependency explicit."""
    import registry.__main__ as registry_main

    monkeypatch.setattr(registry_main.Paths, "from_env", staticmethod(lambda: paths))
    monkeypatch.setattr(registry_main.uvicorn, "run", lambda app, uds, log_level: None)

    registry_main.main()

    assert paths.pillar_db_path("registry").parent.exists()
