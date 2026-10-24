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


def test_job_type_categories_covers_the_four_2026_09_16_additions():
    """`self_redeploy`, `server_stop`, `package_publish`, and
    `project_registration` were split out of `deploy_redeploy`/`release`
    (or newly added) once real usage showed the original six categories
    conflated meaningfully different blast radii — see GatePoint's own
    docstring in policy/models.py."""
    categories = policy_gate.JOB_TYPE_CATEGORIES
    assert categories["build.publish_pypi"] == {"package_publish"}
    assert categories["build.publish_vsix"] == {"package_publish"}
    assert categories["deployment.stop"] == {"server_stop"}
    assert categories["deployment.redeploy_pillar"] == {"self_redeploy"}
    # project_registration is deliberately absent from this table — OQ-15
    # (resolved 2026-09-17): it's verified after the fact, never
    # pre-dispatch-gated. See POST_COMPLETION_VERIFY_JOB_TYPES below.
    assert "project.bootstrap" not in categories
    assert "project.import" not in categories


def test_post_completion_verify_job_types_covers_project_registration():
    categories = policy_gate.POST_COMPLETION_VERIFY_JOB_TYPES
    assert categories["project.bootstrap"] == {"project_registration"}
    assert categories["project.import"] == {"project_registration"}


def test_resolve_gating_policies_returns_empty_when_socket_lookup_has_no_policy_entry():
    assert policy_gate.resolve_gating_policies(lambda p: None, "proj-a", {"server_launch"}) == []


def test_resolve_gating_policies_returns_empty_on_connect_error():
    assert policy_gate.resolve_gating_policies(lambda p: "/nonexistent/socket.sock", "proj-a", {"server_launch"}, timeout=0.5) == []


def test_resolve_gating_policies_parses_a_real_response(monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1", "gating": True}, {"id": "pol2", "gating": False}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    assert policy_gate.resolve_gating_policies(socket_lookup, "proj-a", {"server_launch"}) == [
        {"id": "pol1", "gating": True, "bypassed": False}, {"id": "pol2", "gating": False, "bypassed": False},
    ]


def test_resolve_gating_policies_empty_when_nothing_governs(monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return []

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    assert policy_gate.resolve_gating_policies(socket_lookup, "proj-a", {"server_launch"}) == []


def test_a_response_without_a_gating_flag_defaults_to_gating_on(monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    assert policy_gate.resolve_gating_policies(socket_lookup, "proj-a", {"server_launch"}) == [{"id": "pol1", "gating": True, "bypassed": False}]


def test_attach_policy_gate_is_a_noop_for_an_uncategorized_job_type(conn):
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

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return []

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)
    assert job.depends_on == []


def test_attach_policy_gate_creates_a_real_check_dependency(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)

    assert len(job.depends_on) == 1
    check_job = db.get_job(conn, job.depends_on[0])
    assert check_job.type == "policy.check_project"
    assert check_job.target_pillar == "policy"
    assert check_job.params == {"policy_id": "pol1", "gating": True, "bypassed": False, "origin": "unknown"}
    # persisted, not just held on the in-memory object
    assert db.get_job(conn, job.id).depends_on == job.depends_on


def test_attach_policy_gate_creates_one_dependency_per_gating_policy(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}, {"id": "pol2"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)

    assert len(job.depends_on) == 2
    policy_ids = {db.get_job(conn, dep_id).params["policy_id"] for dep_id in job.depends_on}
    assert policy_ids == {"pol1", "pol2"}


def test_attach_policy_gate_sends_the_jobs_own_categories(conn, monkeypatch):
    captured = {}
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        captured["categories"] = categories
        return []

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)

    assert set(captured["categories"].split(",")) == {"server_launch", "deploy_redeploy"}


def test_verify_after_completion_does_nothing_for_a_job_that_did_not_succeed(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="project.bootstrap", target_pillar="registry", project_id="proj-a", status=JobStatus.FAILED)
    db.insert_job(conn, job)
    policy_gate.verify_after_completion(conn, socket_lookup, job)
    assert db.list_jobs(conn, status=JobStatus.QUEUED) == []


def test_verify_after_completion_does_nothing_for_an_unrelated_job_type(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, job)
    policy_gate.verify_after_completion(conn, socket_lookup, job)
    assert db.list_jobs(conn, status=JobStatus.QUEUED) == []


def test_verify_after_completion_creates_a_real_check_job_after_a_real_bootstrap_succeeds(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        assert project_id == "proj-a"
        assert categories == "project_registration"
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="project.bootstrap", target_pillar="registry", project_id="proj-a", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, job)
    policy_gate.verify_after_completion(conn, socket_lookup, job)

    created = db.list_jobs(conn, status=JobStatus.QUEUED)
    assert len(created) == 1
    assert created[0].type == "policy.check_project"
    assert created[0].target_pillar == "policy"
    assert created[0].project_id == "proj-a"
    assert created[0].params == {"policy_id": "pol1", "gating": True, "origin": "unknown"}
    assert created[0].depends_on == []  # fire-and-forget, never wired back


def test_verify_after_completion_never_blocks_even_when_it_would_have_failed(conn, monkeypatch):
    """The verification Job it creates is never wired as a dependency of
    anything, and nothing here inspects its eventual verdict — a `fail`
    outcome is recorded, not enforced, since the registration already
    happened for real."""
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1"}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="project.import", target_pillar="registry", project_id="proj-a", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, job)
    result = policy_gate.verify_after_completion(conn, socket_lookup, job)
    assert result.status == JobStatus.SUCCEEDED  # untouched


def test_maybe_gate_false_for_an_ungated_job_type(conn):
    job = Job(type="deployment.restart_sandbox", target_pillar="deployment", project_id="proj-a")
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


def test_a_failing_check_whose_gating_is_off_runs_but_never_holds_the_job(conn):
    analysis = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", params={"policy_id": "p1", "gating": False},
                   status=JobStatus.SUCCEEDED, result={"outcome": "fail"})
    db.insert_job(conn, analysis)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[analysis.id])
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job, runner=FakeRunner()) is False


def test_a_gating_check_still_holds_the_job_alongside_an_analysis_only_one(conn):
    analysis = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", params={"policy_id": "p1", "gating": False},
                   status=JobStatus.SUCCEEDED, result={"outcome": "fail"})
    gating = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", params={"policy_id": "p2", "gating": True},
                 status=JobStatus.SUCCEEDED, result={"outcome": "fail"})
    db.insert_job(conn, analysis)
    db.insert_job(conn, gating)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[analysis.id, gating.id])
    db.insert_job(conn, job)
    assert policy_gate.maybe_gate(conn, job, runner=FakeRunner()) is True


def test_attach_policy_gate_records_the_gating_flag_on_each_check(conn, monkeypatch):
    app = FastAPI()

    @app.get("/v1/policies/gating-for-job")
    def gating(project_id: str, categories: str) -> list[dict]:
        return [{"id": "pol1", "gating": True}, {"id": "pol2", "gating": False}]

    socket_lookup = _policy_socket_lookup(app, monkeypatch)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a")
    db.insert_job(conn, job)
    policy_gate.attach_policy_gate(conn, socket_lookup, job)
    flags = {db.get_job(conn, d).params["policy_id"]: db.get_job(conn, d).params["gating"] for d in job.depends_on}
    assert flags == {"pol1": True, "pol2": False}


def test_maybe_gate_true_when_any_of_several_gating_policies_fails(conn):
    """A Job can now be gated by more than one policy at once (one per
    topic) — any single failure is enough to hold it, even if every
    other gating policy passed."""
    passing = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", status=JobStatus.SUCCEEDED, result={"outcome": "pass"})
    failing = Job(type="policy.check_project", target_pillar="policy", project_id="proj-a", status=JobStatus.SUCCEEDED, result={"outcome": "fail"})
    db.insert_job(conn, passing)
    db.insert_job(conn, failing)
    job = Job(type="deployment.deploy", target_pillar="deployment", project_id="proj-a", depends_on=[passing.id, failing.id])
    db.insert_job(conn, job)

    assert policy_gate.maybe_gate(conn, job, runner=FakeRunner()) is True
    assert db.get_job(conn, job.id).status == JobStatus.PENDING_CONFIRMATION


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


def test_the_release_job_is_gated_like_a_release_finish_and_never_stopped_mid_way():
    from orchestration.dispatch import DECLARED_UNSTOPPABLE_JOB_TYPES, JOB_TYPE_TIMEOUTS
    from orchestration.policy_gate import JOB_TYPE_CATEGORIES

    assert JOB_TYPE_CATEGORIES["version.release"] == JOB_TYPE_CATEGORIES["version.release_finish"]
    assert "version.release" in DECLARED_UNSTOPPABLE_JOB_TYPES and JOB_TYPE_TIMEOUTS["version.release"] >= 60


# --- REQ-000000433: usage.analyze has a dispatch ceiling above the 20s default ---
def test_usage_analyze_dispatch_timeout_exceeds_default():
    from orchestration.dispatch import DEFAULT_DISPATCH_TIMEOUT_SECONDS, dispatch_timeout_for

    assert dispatch_timeout_for("usage.analyze") >= 300 > DEFAULT_DISPATCH_TIMEOUT_SECONDS
