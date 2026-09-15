from fastapi.testclient import TestClient

from common.manifest import Manifest, PillarEntry
from common.paths import Paths
from core.app import build_app
from core.health import HealthResult
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor


def make_supervisor(tmp_path) -> Supervisor:
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    manifest = Manifest()
    manifest.pillars = {"orchestration": PillarEntry(name="orchestration", enabled=True)}
    return Supervisor(paths=paths, manifest=manifest)


def test_health_endpoint(tmp_path):
    client = TestClient(build_app(make_supervisor(tmp_path)))
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_status_endpoint_reports_registry(tmp_path):
    supervisor = make_supervisor(tmp_path)
    supervisor.registry.set(
        RegistryEntry(
            name="orchestration",
            socket_path=supervisor.paths.socket_path("orchestration"),
            status=PillarStatus.HEALTHY,
        )
    )
    client = TestClient(build_app(supervisor))
    response = client.get("/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["pillars"]["orchestration"]["status"] == "healthy"


def test_reconcile_endpoint_invokes_supervisor(tmp_path):
    supervisor = make_supervisor(tmp_path)
    supervisor.manifest.pillars["orchestration"].command = ["run-orch"]
    supervisor.probe = lambda socket_path, timeout, max_retries: HealthResult(ok=True, attempts=1)

    class FakeRunner:
        def run(self, argv, timeout=None):
            from common.shell import CommandResult

            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return CommandResult(0, "inactive\n", "")
            return CommandResult(0, "", "")

    supervisor.runner = FakeRunner()

    client = TestClient(build_app(supervisor))
    response = client.post("/v1/reconcile")
    assert response.status_code == 200
    assert response.json()["pillars"]["orchestration"]["status"] == "healthy"
