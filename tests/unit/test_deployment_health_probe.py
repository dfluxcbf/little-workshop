"""A deploy's health probe confirms or rolls back on its own (REQ-000000521)."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from deployment import db
from deployment.flow import DeploymentError
from deployment.manifest import HealthManifest, ManifestError, parse_manifest, render_manifest
from deployment.netutil import http_is_healthy
from test_deployment_flow import FakeRunner, _git, make_flow, make_repo, manifest


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


@pytest.fixture
def paths(tmp_path):
    from common.paths import Paths

    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def _serve(status: int):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bump(repo, version):
    _git(["checkout", "dev"], repo)
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", f"bump {version}"], repo)


# --- REQ-000000521: A deploy's health probe confirms or rolls back on its own ---


def test_the_manifest_reads_and_writes_the_health_table():
    text = '[server]\nname = "x"\ncommand = "run"\nport = 8000\n\n[server.health]\nurl = "http://127.0.0.1:8000/health"\ntimeout_seconds = 30\n'
    parsed = parse_manifest(text)
    assert parsed.health == HealthManifest(url="http://127.0.0.1:8000/health", timeout_seconds=30)
    assert parse_manifest(render_manifest(parsed)).health == parsed.health
    assert parse_manifest('[server]\nname = "x"\ncommand = "run"\nport = 8000\n').health is None
    with pytest.raises(ManifestError):
        parse_manifest('[server]\nname = "x"\ncommand = "run"\nport = 1\n[server.health]\nurl = "ftp://x"\n')


def test_http_probe_against_a_real_server():
    healthy, failing = _serve(200), _serve(503)
    try:
        assert http_is_healthy(f"http://127.0.0.1:{healthy.server_port}/health")
        assert not http_is_healthy(f"http://127.0.0.1:{failing.server_port}/health")
        assert not http_is_healthy(f"http://127.0.0.1:{_free_port()}/health", timeout=0.5)
    finally:
        healthy.shutdown()
        failing.shutdown()


def test_a_healthy_deploy_is_confirmed_without_waiting_for_a_human(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    server = _serve(200)
    flow = make_flow(paths, FakeRunner())
    try:
        result = flow.up(conn, "little-meals", manifest(
            confirm_timeout_seconds=600, health=HealthManifest(url=f"http://127.0.0.1:{server.server_port}/")), repo, "main")
    finally:
        server.shutdown()
    assert "confirmed automatically" in result.confirm_message
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED and state["confirmed_version"] == "1.0.0"


def test_without_a_confirm_gate_a_healthy_deploy_becomes_the_rollback_baseline(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    flow = make_flow(paths, FakeRunner())  # its port probe answers once launched
    flow.up(conn, "little-meals", manifest(health=HealthManifest(port=8765)), repo, "main")
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED and state["confirmed_version"] == "1.0.0"


def test_an_unhealthy_deploy_is_rolled_back_at_once(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    flow = make_flow(paths, FakeRunner())
    flow.up(conn, "little-meals", manifest(health=HealthManifest(port=8765)), repo, "main")  # healthy 1.0.0: the baseline
    _bump(repo, "1.0.1")
    flow.http_probe = lambda url: False

    with pytest.raises(DeploymentError) as raised:
        flow.up(conn, "little-meals", manifest(health=HealthManifest(url="http://127.0.0.1:1/h", timeout_seconds=5)), repo, "dev")

    assert "not healthy within 5s" in str(raised.value) and "rolled back" in str(raised.value)
    assert db.get_server_state(conn, "little-meals")["version"] == "1.0.0"
    assert [d["outcome"] for d in db.list_deployments(conn, "little-meals")][:2] == ["rolled_back", "succeeded"]
    assert db.get_confirmation(conn, "little-meals")["status"] == db.CONFIRMED


def test_an_unhealthy_first_deploy_has_nothing_to_roll_back_to_and_says_so(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    flow = make_flow(paths, FakeRunner())
    flow.http_probe = lambda url: False
    with pytest.raises(DeploymentError, match="no confirmed deploy to roll back to"):
        flow.up(conn, "little-meals", manifest(health=HealthManifest(url="http://127.0.0.1:1/h", timeout_seconds=3)), repo, "main")


def test_without_health_nothing_changes(paths, conn, tmp_path):
    repo = make_repo(tmp_path)
    flow = make_flow(paths, FakeRunner())
    result = flow.up(conn, "little-meals", manifest(), repo, "main")
    assert result.confirm_message is None and db.get_confirmation(conn, "little-meals") is None
