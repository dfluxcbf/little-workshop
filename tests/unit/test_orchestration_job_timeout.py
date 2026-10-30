"""The running-Job timeout: a Job still `running` six hours after its last
change is presumed lost (its pillar's worker died, or never called back)
and is failed, so it stops holding its queue_key and its dependants."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from orchestration import db, dispatch
from orchestration.dispatch import RUNNING_JOB_TIMEOUT, expire_stuck_jobs
from orchestration.models import Job, JobStatus
from orchestration.scheduler import SchedulerThread


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


def _job(conn, *, status=JobStatus.RUNNING, age=timedelta(0), **fields) -> Job:
    job = Job(type=fields.pop("type", "policy.check_project"), target_pillar=fields.pop("target_pillar", "policy"), status=status, **fields)
    db.insert_job(conn, job)
    stamp = (datetime.now(timezone.utc) - age).isoformat()
    conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (stamp, job.id))
    conn.commit()
    return job


def _no_pillar(pillar):
    return None


def test_the_timeout_is_six_hours():
    assert RUNNING_JOB_TIMEOUT == timedelta(hours=6)


def test_a_job_running_past_six_hours_is_failed_and_a_younger_one_is_left_alone(conn):
    stuck = _job(conn, age=timedelta(hours=10))
    busy = _job(conn, age=timedelta(hours=1))
    expired = expire_stuck_jobs(conn, _no_pillar)
    assert [j.id for j in expired] == [stuck.id]
    after = db.get_job(conn, stuck.id)
    assert after.status == JobStatus.FAILED and after.error == "timed out: still running after 6 hours"
    assert db.get_job(conn, busy.id).status == JobStatus.RUNNING


def test_only_running_jobs_are_timed_out(conn):
    queued = _job(conn, status=JobStatus.QUEUED, age=timedelta(days=2), depends_on=["never-finishes"])
    pending = _job(conn, status=JobStatus.PENDING_CONFIRMATION, age=timedelta(days=2))
    assert expire_stuck_jobs(conn, _no_pillar) == []
    assert db.get_job(conn, queued.id).status == JobStatus.QUEUED
    assert db.get_job(conn, pending.id).status == JobStatus.PENDING_CONFIRMATION


def test_expiring_frees_the_queue_key_and_fails_dependants(conn):
    stuck = _job(conn, age=timedelta(hours=7), queue_key="little-meals")
    waiting = _job(conn, status=JobStatus.QUEUED, type="deployment.deploy", target_pillar="deployment", queue_key="little-meals")
    dependant = _job(conn, status=JobStatus.QUEUED, type="deployment.deploy", target_pillar="deployment", depends_on=[stuck.id])
    expire_stuck_jobs(conn, _no_pillar)
    # the queue_key is free again, so the waiting Job was dispatched (here: to a pillar that is not there)
    assert db.get_job(conn, waiting.id).error == "pillar disabled"
    assert db.get_job(conn, dependant.id).status == JobStatus.FAILED
    assert db.get_job(conn, dependant.id).error == f"dependency {stuck.id} failed"


def test_a_pillar_that_can_stop_the_job_is_asked_first(conn, monkeypatch):
    class Stoppable:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json=None):
            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"stopped": True}

            return R()

    monkeypatch.setattr(dispatch, "client_for_socket", Stoppable)
    stuck = _job(conn, age=timedelta(hours=8), type="agent.run_task", target_pillar="agent")
    expire_stuck_jobs(conn, lambda pillar: Path("/fake.sock"))
    assert db.get_job(conn, stuck.id).status == JobStatus.STOPPED


def test_the_scheduler_thread_expires_stuck_jobs_as_soon_as_it_starts(conn):
    stuck = _job(conn, age=timedelta(hours=9))
    # The thread shares this connection, so every use of it holds the lock, as in orchestration/__main__.py.
    lock = threading.Lock()
    thread = SchedulerThread(conn, socket_lookup=_no_pillar, interval_seconds=60, lock=lock)
    thread.start()

    def status():
        with lock:
            return db.get_job(conn, stuck.id).status

    try:
        for _ in range(100):
            if status() == JobStatus.FAILED:
                break
            time.sleep(0.02)
        assert status() == JobStatus.FAILED
    finally:
        thread.stop()
