import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.shell import CommandResult
from orchestration import db, policy_gate
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        return CommandResult(0, "", "")


def _policy_socket_lookup(app, monkeypatch):
    """A socket_lookup whose 'policy' entry actually reaches a real
    FastAPI TestClient's ASGI app, via socket_test_helpers' patching of
    httpx — same technique test_mcp_client.py already uses for a real
    HTTP round trip without a real Unix socket."""
    from socket_test_helpers import patch_socket_apps

    def socket_lookup(pillar: str):
        return f"fake-socket-{pillar}" if pillar == "policy" else None

    patch_socket_apps(monkeypatch, policy_gate, {"fake-socket-policy": app})
    return socket_lookup


def test_resolve_winning_policy_id_returns_none_when_socket_lookup_has_no_policy_entry():
    assert policy_gate.resolve_winning_policy_id(lambda p: None, "t", "proj-a") is None


def test_resolve_winning_policy_id_returns_none_on_connect_error():
    assert policy_gate.resolve_winning_policy_id(lambda p: "/nonexistent/socket.sock", "t", "proj-a", timeout=0.5) is None


def test_resolve_winning_policy_id_parses_a_real_response(monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/winning")
    def winning(topic: str, project_id: str) -> dict:
        return {"topic": topic, "project_id": project_id, "policy": {"id": "pol1"}}

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    assert policy_gate.resolve_winning_policy_id(socket_lookup, "t", "proj-a") == "pol1"


def test_resolve_winning_policy_id_none_when_nothing_governs(monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/winning")
    def winning(topic: str, project_id: str) -> dict:
        return {"topic": topic, "project_id": project_id, "policy": None}

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    assert policy_gate.resolve_winning_policy_id(socket_lookup, "t", "proj-a") is None


def test_attach_policy_gate_is_a_noop_for_an_ungated_job_type(conn):
    job = Job(type="deployment.stop", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, lambda p: None, job)
    assert job.depends_on == []


def test_attach_policy_gate_is_a_noop_without_a_project_id(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id=None)
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, lambda p: None, job)
    assert job.depends_on == []


def test_attach_policy_gate_is_a_noop_when_no_policy_governs(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/winning")
    def winning(topic: str, project_id: str) -> dict:
        return {"topic": topic, "project_id": project_id, "policy": None}

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)
    assert job.depends_on == []


def test_attach_policy_gate_creates_a_real_check_dependency(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/winning")
    def winning(topic: str, project_id: str) -> dict:
        return {"topic": topic, "project_id": project_id, "policy": {"id": "pol1"}}

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)

    assert len(job.depends_on) == 1
    check_job = db.get_job(conn, job.depends_on[0])
    assert check_job.type == "policy.check_project"
    assert check_job.target_pillar == "policy"
    assert check_job.params == {"policy_id": "pol1"}
    # persisted, not just held on the in-memory object
    assert db.get_job(conn, job.id).depends_on == job.depends_on


def test_maybe_gate_false_for_an_ungated_job_type(conn):
    job = Job(type="deployment.stop", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job) is False


def test_maybe_gate_false_when_there_is_no_check_dependency(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job) is False


def test_maybe_gate_false_when_verdict_is_pass(conn):
    check = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", status=JobStatus.SUCCEEDED, result={"outcome": "pass"})
    db.insert_job(conn, check)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[check.id])
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job) is False


def test_maybe_gate_true_and_flips_to_pending_confirmation_when_verdict_is_fail(conn):
    check = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", status=JobStatus.SUCCEEDED, result={"outcome": "fail"})
    db.insert_job(conn, check)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[check.id])
    db.insert_job(conn, job)

    fake_runner = FakeRunner()
    assert policy_gate.maybe_gate(conn, job, runner=fake_runner) is True
    assert db.get_job(conn, job.id).status == JobStatus.PENDING_CONFIRMATION
    assert fake_runner.calls  # the watchdog was armed for real


def test_maybe_gate_false_for_not_applicable_verdict(conn):
    check = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", status=JobStatus.SUCCEEDED, result={"outcome": "not_applicable"})
    db.insert_job(conn, check)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[check.id])
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job) is False


def test_arm_confirm_timeout_shells_systemd_run_with_the_right_unit_and_callback():
    fake_runner = FakeRunner()
    policy_gate._arm_confirm_timeout("job-123", timeout_seconds=600, runner=fake_runner)
    argv = fake_runner.calls[0]
    assert "systemd-run" in argv
    assert any("job-123" in unit for unit in argv if "unit=" in unit)
    assert argv[-3:] == ["lws", "job", "policy-confirm-timeout"] or argv[-4:] == ["lws", "job", "policy-confirm-timeout", "job-123"]


def test_confirm_refuses_when_job_is_not_pending_confirmation(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.QUEUED)
    db.insert_job(conn, job)
    result = policy_gate.confirm(conn, job, lambda p: None)
    assert result["confirmed"] is False
    assert "not pending confirmation" in result["reason"]


def test_confirm_dispatches_a_pending_confirmation_job_for_real(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.PENDING_CONFIRMATION)
    db.insert_job(conn, job)
    result = policy_gate.confirm(conn, job, lambda p: None)  # no socket for "deployment" -> pillar disabled
    assert result["confirmed"] is True
    assert result["job"]["status"] == "failed"  # dispatch_job's own real "pillar disabled" outcome
    assert result["job"]["error"] == "pillar disabled"


def test_confirm_timeout_settles_a_pending_confirmation_job_as_failed(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.PENDING_CONFIRMATION)
    db.insert_job(conn, job)
    result = policy_gate.confirm_timeout(conn, job)
    assert result["timed_out"] is True
    settled = db.get_job(conn, job.id)
    assert settled.status == JobStatus.FAILED
    assert settled.error == "policy check unconfirmed"


def test_confirm_timeout_is_a_noop_when_already_settled(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, job)
    result = policy_gate.confirm_timeout(conn, job)
    assert result["timed_out"] is False
    assert db.get_job(conn, job.id).status == JobStatus.SUCCEEDED


def test_confirm_timeout_cascades_failure_to_dependents(conn):
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.PENDING_CONFIRMATION)
    db.insert_job(conn, job)
    dependent = Job(type="deployment.stop", target_pillar="deployment", project_id="proj-a", depends_on=[job.id])
    db.insert_job(conn, dependent)

    policy_gate.confirm_timeout(conn, job)

    assert db.get_job(conn, dependent.id).status == JobStatus.FAILED
