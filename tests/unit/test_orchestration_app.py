import pytest
from fastapi.testclient import TestClient

from orchestration import db
from orchestration.app import build_app
from orchestration.models import JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def always_unavailable(pillar: str):
    return None


@pytest.fixture
def client(conn):
    return TestClient(build_app(conn, socket_lookup=always_unavailable))


def test_health_endpoint(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_create_job_dispatches_and_fails_when_pillar_unavailable(client):
    response = client.post(
        "/v1/jobs",
        json={"type": "noop", "target_pillar": "reference", "params": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "pillar disabled"


def test_get_job_not_found(client):
    response = client.get("/v1/jobs/does-not-exist")
    assert response.status_code == 404


def test_list_jobs_filters_by_status(client):
    client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"})
    response = client.get("/v1/jobs", params={"status": "failed"})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_pipeline_save_get_and_trigger(client):
    pipeline = {
        "name": "release",
        "version": 1,
        "nodes": [
            {"node_id": "build", "job_type": "build.run", "target_pillar": "build"},
            {
                "node_id": "deploy",
                "job_type": "deployment.deploy",
                "target_pillar": "deployment",
                "depends_on": ["build"],
            },
        ],
    }
    save_response = client.post("/v1/pipelines", json=pipeline)
    assert save_response.status_code == 200

    get_response = client.get("/v1/pipelines/release")
    assert get_response.status_code == 200
    assert len(get_response.json()["nodes"]) == 2

    trigger_response = client.post(
        "/v1/pipelines/release/trigger", json={"project_id": "little-meals", "created_by": "lws"}
    )
    assert trigger_response.status_code == 200
    run = trigger_response.json()
    assert set(run["job_ids"]) == {"build", "deploy"}

    run_get = client.get(f"/v1/pipeline_runs/{run['id']}")
    assert run_get.status_code == 200


def test_trigger_unknown_pipeline_404s(client):
    response = client.post("/v1/pipelines/ghost/trigger", json={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Scheduled tasks -- docs/milestones.md M9
# ---------------------------------------------------------------------------

_ONCE_PAYLOAD = {
    "name": "nightly-test-run",
    "job_template": {"type": "noop", "target_pillar": "reference"},
    "schedule": {"kind": "once", "at": "2026-01-01T02:00:00"},
}

_RECURRING_PAYLOAD = {
    "name": "nightly-test-run",
    "job_template": {"type": "noop", "target_pillar": "reference"},
    "schedule": {"kind": "recurring", "days_of_week": [0, 1, 2, 3, 4], "time_of_day": "02:00"},
}


def test_create_scheduled_task_computes_next_run_at(client):
    response = client.post("/v1/scheduled_tasks", json=_ONCE_PAYLOAD)
    assert response.status_code == 200
    body = response.json()
    assert body["next_run_at"] == "2026-01-01T02:00:00"
    assert body["missed_run_at"] is None


def test_get_and_list_scheduled_tasks(client):
    created = client.post("/v1/scheduled_tasks", json=_ONCE_PAYLOAD).json()

    get_response = client.get(f"/v1/scheduled_tasks/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "nightly-test-run"

    list_response = client.get("/v1/scheduled_tasks")
    assert len(list_response.json()) == 1


def test_get_scheduled_task_not_found(client):
    assert client.get("/v1/scheduled_tasks/ghost").status_code == 404


def test_edit_scheduled_task_updates_job_template(client):
    created = client.post("/v1/scheduled_tasks", json=_ONCE_PAYLOAD).json()

    response = client.patch(
        f"/v1/scheduled_tasks/{created['id']}",
        json={"job_template": {"type": "always_fail", "target_pillar": "reference"}},
    )
    assert response.status_code == 200
    assert response.json()["job_template"]["type"] == "always_fail"
    # Unrelated fields (the schedule) are untouched by a partial edit.
    assert response.json()["schedule"]["kind"] == "once"


def test_edit_scheduled_task_changing_schedule_recomputes_next_run_at(client):
    created = client.post("/v1/scheduled_tasks", json=_RECURRING_PAYLOAD).json()

    response = client.patch(
        f"/v1/scheduled_tasks/{created['id']}",
        json={"schedule": {"kind": "once", "at": "2030-06-01T09:00:00"}},
    )
    assert response.status_code == 200
    assert response.json()["next_run_at"] == "2030-06-01T09:00:00"


def test_remove_scheduled_task_deletes_it(client):
    created = client.post("/v1/scheduled_tasks", json=_ONCE_PAYLOAD).json()

    response = client.delete(f"/v1/scheduled_tasks/{created['id']}")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert client.get(f"/v1/scheduled_tasks/{created['id']}").status_code == 404


def test_trigger_scheduled_task_creates_a_job_now(client):
    created = client.post("/v1/scheduled_tasks", json=_RECURRING_PAYLOAD).json()

    response = client.post(f"/v1/scheduled_tasks/{created['id']}/trigger")
    assert response.status_code == 200
    assert response.json()["created_by"] == "scheduler"

    reloaded = client.get(f"/v1/scheduled_tasks/{created['id']}").json()
    assert reloaded["last_job_id"] == response.json()["id"]
    # trigger never touches next_run_at.
    assert reloaded["next_run_at"] == created["next_run_at"]


def test_cancel_scheduled_task_skips_the_next_occurrence(client):
    created = client.post("/v1/scheduled_tasks", json=_RECURRING_PAYLOAD).json()

    response = client.post(f"/v1/scheduled_tasks/{created['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["cancelled"] == "next_occurrence"


def test_postpone_scheduled_task_retargets_next_run_at(client):
    created = client.post("/v1/scheduled_tasks", json=_RECURRING_PAYLOAD).json()

    response = client.post(
        f"/v1/scheduled_tasks/{created['id']}/postpone", json={"at": "2030-01-01T05:00:00"}
    )
    assert response.status_code == 200
    assert response.json()["next_run_at"] == "2030-01-01T05:00:00"


def test_scheduled_task_operations_404_on_unknown_id(client):
    assert client.post("/v1/scheduled_tasks/ghost/trigger").status_code == 404
    assert client.post("/v1/scheduled_tasks/ghost/cancel").status_code == 404
    assert client.post("/v1/scheduled_tasks/ghost/postpone", json={"at": "2030-01-01T00:00:00"}).status_code == 404
    assert client.delete("/v1/scheduled_tasks/ghost").status_code == 404
    assert client.patch("/v1/scheduled_tasks/ghost", json={"name": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# Queue capacity -- the `queue` operation
# ---------------------------------------------------------------------------

def test_queue_capacity_defaults_to_1(client):
    response = client.get("/v1/queue_keys/little-meals/capacity")
    assert response.status_code == 200
    assert response.json() == {"queue_key": "little-meals", "capacity": 1}


def test_set_queue_capacity_round_trips(client):
    post_response = client.post("/v1/queue_keys/little-meals/capacity", json={"capacity": 3})
    assert post_response.status_code == 200
    assert post_response.json()["capacity"] == 3

    get_response = client.get("/v1/queue_keys/little-meals/capacity")
    assert get_response.json()["capacity"] == 3


def test_set_queue_capacity_rejects_zero(client):
    response = client.post("/v1/queue_keys/little-meals/capacity", json={"capacity": 0})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# stop -- docs/milestones.md M9
# ---------------------------------------------------------------------------

def test_stop_job_endpoint_404s_on_unknown_job(client):
    assert client.post("/v1/jobs/ghost/stop").status_code == 404


def test_stop_job_endpoint_reports_not_running(client):
    created = client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"}).json()
    response = client.post(f"/v1/jobs/{created['id']}/stop")
    assert response.status_code == 200
    assert response.json()["stopped"] is False


def test_complete_job_callback(conn):
    def available(pillar):
        return None

    app_client = TestClient(build_app(conn, socket_lookup=available))
    create = app_client.post(
        "/v1/jobs",
        json={"type": "agent.run_task", "target_pillar": "agent", "params": {}},
    )
    job_id = create.json()["id"]
    # With no pillar reachable the job failed at once; a real one is `running` while its pillar works on it.
    job = db.get_job(conn, job_id)
    job.status, job.error = JobStatus.RUNNING, None
    db.update_job(conn, job)

    response = app_client.post(
        f"/v1/jobs/{job_id}/complete",
        json={"succeeded": True, "result": {"pr": 1}, "error": None},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


# -- job history cleanup ---------------------------------------------------------------


from datetime import datetime, timedelta, timezone  # noqa: E402

from orchestration.models import Job, JobStatus  # noqa: E402


def _old_job(conn, days, status=JobStatus.SUCCEEDED):
    when = datetime.now(timezone.utc) - timedelta(days=days)
    job = Job(type="noop", target_pillar="reference", status=status, created_at=when, updated_at=when)
    db.insert_job(conn, job)
    return job


def test_cleanup_preview_counts_each_age_without_deleting(client, conn):
    _old_job(conn, 3); _old_job(conn, 10); _old_job(conn, 40); _old_job(conn, 40, status=JobStatus.FAILED)
    _old_job(conn, 500, status=JobStatus.RUNNING)  # never counted
    assert client.get("/v1/job-cleanup").json() == {"prunable": {"12": 4, "48": 4, "168": 3, "672": 2, "1344": 0, "4032": 0}}
    assert client.get("/v1/job-cleanup", params={"hours": "120,720"}).json() == {"prunable": {"120": 3, "720": 2}}
    assert len(db.list_jobs(conn)) == 5


@pytest.mark.parametrize("hours", ["", "x", "0", "-3", "87601", "7,abc"])
def test_cleanup_preview_rejects_bad_ages(client, hours):
    assert client.get("/v1/job-cleanup", params={"hours": hours}).status_code == 400


def test_cleanup_deletes_finished_jobs_older_than_the_chosen_age(client, conn):
    old = _old_job(conn, 30)
    recent = _old_job(conn, 3)
    running = _old_job(conn, 30, status=JobStatus.RUNNING)
    response = client.post("/v1/job-cleanup", json={"older_than_hours": 168})
    assert response.status_code == 200
    assert response.json() == {"deleted": 1, "pipeline_runs_deleted": 0, "kept_for_safety": 0}
    remaining = {j["id"] for j in client.get("/v1/jobs").json()}
    assert remaining == {recent.id, running.id} and old.id not in remaining


def test_cleanup_can_delete_jobs_older_than_twelve_hours(client, conn):
    def job_aged(hours, status=JobStatus.SUCCEEDED):
        when = datetime.now(timezone.utc) - timedelta(hours=hours)
        job = Job(type="noop", target_pillar="reference", status=status, created_at=when, updated_at=when)
        db.insert_job(conn, job)
        return job

    old, fresh, running = job_aged(13), job_aged(11), job_aged(13, status=JobStatus.RUNNING)
    assert client.get("/v1/job-cleanup", params={"hours": "12"}).json() == {"prunable": {"12": 1}}
    assert client.post("/v1/job-cleanup", json={"older_than_hours": 12}).json()["deleted"] == 1
    assert {j["id"] for j in client.get("/v1/jobs").json()} == {fresh.id, running.id} and old.id not in {j.id for j in db.list_jobs(conn)}


def test_cleanup_is_repeatable_and_a_second_run_deletes_nothing(client, conn):
    _old_job(conn, 30)
    assert client.post("/v1/job-cleanup", json={"older_than_hours": 168}).json()["deleted"] == 1
    assert client.post("/v1/job-cleanup", json={"older_than_hours": 168}).json()["deleted"] == 0


@pytest.mark.parametrize("body", [{}, {"older_than_hours": 0}, {"older_than_hours": -1}, {"older_than_hours": 87601}, {"older_than_hours": "soon"}])
def test_cleanup_rejects_invalid_requests_and_deletes_nothing(client, conn, body):
    _old_job(conn, 30)
    assert client.post("/v1/job-cleanup", json=body).status_code == 422
    assert len(db.list_jobs(conn)) == 1


def test_the_cleanup_paths_are_not_read_as_a_job_id(client):
    assert client.get("/v1/job-cleanup").status_code == 200
    assert client.get("/v1/jobs/job-cleanup").status_code == 404
