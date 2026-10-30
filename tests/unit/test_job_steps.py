"""A Job's checklist: storage and reporting in Orchestration, the pillar-side reporter, the question flow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from common import job_steps
from common.job_steps import Option, StepAborted, StepFailed, StepReporter, ask, can_ask, done, key_prefix, reporting, step
from common.step_specs import steps_for
from orchestration import db, dispatch, steps as ostore
from orchestration.app import build_app
from orchestration.models import Job, JobStatus


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "orchestration.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn):
    return TestClient(build_app(conn, socket_lookup=lambda pillar: None))


def _running_finish_job(conn) -> Job:
    job = Job(type="version.feature_finish", target_pillar="version", project_id="p")
    db.insert_job(conn, job)
    job.status = JobStatus.RUNNING
    db.update_job(conn, job)
    return job


def _fake_call(client: TestClient):
    def call(method, path, json=None):
        response = client.request(method, path, json=json)
        if response.status_code >= 400:
            raise ConnectionError(f"HTTP {response.status_code} {response.text}")
        return response.json()
    return call


# --- REQ-000000310: Every job has a checklist of its actions ---

def test_a_job_type_without_declared_steps_has_one_step_that_is_the_job(client, conn):
    created = client.post("/v1/jobs", json={"type": "noop", "target_pillar": "reference"}).json()
    assert [s["key"] for s in created["steps"]] == ["run"]
    assert created["steps"][0]["title"] == "Run noop"


def test_a_declared_type_lists_its_steps_in_order_all_not_started(conn):
    job = Job(type="version.feature_finish", target_pillar="version")
    db.insert_job(conn, job)
    listed = ostore.list_steps(conn, job)
    assert [s["key"] for s in listed] == [spec.key for spec in steps_for("version.feature_finish")]
    assert {s["state"] for s in listed} == {"pending"}
    assert listed[0]["needs_attention"] is True


def test_the_release_job_lists_the_steps_of_the_release_it_starts_and_finishes(conn):
    keys = [spec.key for spec in steps_for("version.release")]
    assert keys[0] == "check_conditions" and keys[-1] == "relaunch"
    assert "start_check_branch" in keys and "finish_merge_production" in keys and len(set(keys)) == len(keys)


def test_a_single_step_job_runs_while_the_job_runs_and_finishes_with_it(conn):
    job = Job(type="noop", target_pillar="x")
    db.insert_job(conn, job)
    job.status = JobStatus.RUNNING
    db.update_job(conn, job)
    assert ostore.list_steps(conn, job)[0]["state"] == "running"
    job.status = JobStatus.SUCCEEDED
    db.update_job(conn, job)
    assert ostore.list_steps(conn, job)[0]["state"] == "succeeded"


def test_a_failed_job_fails_the_step_in_progress_and_leaves_later_ones_not_started(conn):
    job = _running_finish_job(conn)
    ostore.report(conn, job, "check_clean_tree", "succeeded")
    ostore.report(conn, job, "check_branch", "running")
    job.status, job.error = JobStatus.FAILED, "boom"
    db.update_job(conn, job)
    states = {s["key"]: s["state"] for s in ostore.list_steps(conn, job)}
    assert states["check_clean_tree"] == "succeeded" and states["check_branch"] == "failed" and states["save_state"] == "pending"


def test_a_succeeded_job_counts_steps_it_never_reported_as_done(conn):
    job = _running_finish_job(conn)
    job.status = JobStatus.SUCCEEDED
    db.update_job(conn, job)
    assert {s["state"] for s in ostore.list_steps(conn, job)} == {"succeeded"}


def test_a_job_that_predates_steps_gets_them_from_its_status(conn):
    job = Job(type="noop", target_pillar="x", status=JobStatus.SUCCEEDED)
    db.insert_job(conn, job)
    conn.execute("DELETE FROM job_steps")
    conn.commit()
    assert ostore.list_steps(conn, job)[0]["state"] == "succeeded"


def test_runtime_and_expected_runtime_come_from_the_last_fifty_successful_runs(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for n in range(60):  # the first ten are older than the window
        job = Job(type="version.feature_finish", target_pillar="version")
        db.insert_job(conn, job)
        job.status = JobStatus.RUNNING
        db.update_job(conn, job)
        seconds = 1000 if n < 10 else 10
        ostore.report(conn, job, "rebase", "running", now=base)
        ostore.report(conn, job, "rebase", "succeeded", now=base + timedelta(seconds=seconds))
    probe = Job(type="version.feature_finish", target_pillar="version")
    db.insert_job(conn, probe)
    rebase = next(s for s in ostore.list_steps(conn, probe) if s["key"] == "rebase")
    assert rebase["expected_seconds"] == pytest.approx(10)
    assert conn.execute("SELECT COUNT(*) FROM step_durations").fetchone()[0] == 50
    other = next(s for s in ostore.list_steps(conn, probe) if s["key"] == "squash")
    assert other["expected_seconds"] is None


def test_runtime_excludes_the_time_a_step_waited_for_the_user(conn):
    job = _running_finish_job(conn)
    t0 = datetime.now(timezone.utc)
    ostore.report(conn, job, "check_clean_tree", "running", now=t0)
    ostore.report(conn, job, "check_clean_tree", "attention", prompt={"question": "q", "options": [{"id": "a", "label": "A"}]}, now=t0 + timedelta(seconds=2))
    ostore.answer(conn, job, "check_clean_tree", "a", now=t0 + timedelta(seconds=102))
    ostore.report(conn, job, "check_clean_tree", "succeeded", now=t0 + timedelta(seconds=105))
    step_row = next(s for s in ostore.list_steps(conn, job) if s["key"] == "check_clean_tree")
    assert step_row["runtime_seconds"] == pytest.approx(5)


def test_reporting_rules(client, conn):
    created = client.post("/v1/jobs", json={"type": "version.feature_finish", "target_pillar": "version"}).json()
    # the pillar is unavailable, so the job failed at once; nothing more can be reported on it
    assert client.post(f"/v1/jobs/{created['id']}/steps/rebase", json={"state": "running"}).status_code == 409
    job = _running_finish_job(conn)
    assert client.post(f"/v1/jobs/{job.id}/steps/nope", json={"state": "running"}).status_code == 404
    assert client.post(f"/v1/jobs/{job.id}/steps/rebase", json={"state": "pending"}).status_code == 422
    assert client.post("/v1/jobs/missing/steps/rebase", json={"state": "running"}).status_code == 404
    assert client.post(f"/v1/jobs/{job.id}/steps/rebase", json={"state": "running"}).json()["state"] == "running"
    assert client.post(f"/v1/jobs/{job.id}/steps/rebase", json={"state": "running"}).json()["state"] == "running"
    progress = [j for j in client.get("/v1/jobs").json() if j["id"] == job.id][0]["progress"]
    assert progress == {"total": 9, "done": 0, "current": steps_for("version.feature_finish")[3].title, "attention": False}


def test_a_step_can_be_run_again_after_it_failed(conn):
    job = _running_finish_job(conn)
    ostore.report(conn, job, "rebase", "running")
    ostore.report(conn, job, "rebase", "failed", detail="conflict")
    ostore.report(conn, job, "rebase", "running")
    row = next(s for s in ostore.list_steps(conn, job) if s["key"] == "rebase")
    assert row["state"] == "running" and row["detail"] is None and row["finished_at"] is None


def test_old_jobs_are_deleted_with_their_steps_but_durations_stay(conn):
    job = _running_finish_job(conn)
    ostore.report(conn, job, "rebase", "running")
    ostore.report(conn, job, "rebase", "succeeded")
    job.status = JobStatus.SUCCEEDED
    db.update_job(conn, job)
    db.prune_finished_jobs(conn, datetime.now(timezone.utc) + timedelta(hours=1))
    assert conn.execute("SELECT COUNT(*) FROM job_steps").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM step_durations").fetchone()[0] >= 1


# --- REQ-000000311: An action that needs the user asks, and times out after 12 hours ---

_OPTIONS = [Option("go", "Go on"), Option("stop", "Stop")]


def _attention(conn):
    job = _running_finish_job(conn)
    ostore.report(conn, job, "check_clean_tree", "running")
    ostore.report(conn, job, "check_clean_tree", "attention", prompt={"question": "dirty?", "options": [o.as_dict() for o in _OPTIONS]})
    return job


def test_an_answer_must_be_one_of_the_options_and_resumes_the_step(conn):
    job = _attention(conn)
    with pytest.raises(ostore.StepError) as refused:
        ostore.answer(conn, job, "check_clean_tree", "nope")
    assert refused.value.status_code == 422
    assert ostore.answer(conn, job, "check_clean_tree", "go")["state"] == "running"
    with pytest.raises(ostore.StepError) as again:
        ostore.answer(conn, job, "check_clean_tree", "go")
    assert again.value.status_code == 409
    with pytest.raises(ostore.StepError):
        ostore.answer(conn, job, "ghost", "go")


def test_the_answer_endpoint(client, conn):
    job = _attention(conn)
    assert client.post(f"/v1/jobs/{job.id}/steps/check_clean_tree/answer", json={"choice": "zzz"}).status_code == 422
    assert client.post("/v1/jobs/missing/steps/x/answer", json={"choice": "go"}).status_code == 404
    assert client.post(f"/v1/jobs/{job.id}/steps/check_clean_tree/answer", json={"choice": "go"}).json()["answer"] == "go"
    shown = client.get(f"/v1/jobs/{job.id}").json()["steps"][0]
    assert shown["state"] == "running" and shown["answer"] == "go"


def test_a_waiting_step_shows_its_question_and_when_it_expires(conn):
    job = _attention(conn)
    row = ostore.list_steps(conn, job)[0]
    assert row["state"] == "attention" and row["prompt"]["question"] == "dirty?"
    assert row["attention_expires_at"] > row["attention_since"]
    assert ostore.progress(conn, job.id)["attention"] is True


def test_a_step_unanswered_for_twelve_hours_fails_with_its_job(conn):
    job = _attention(conn)
    now = datetime.now(timezone.utc) + timedelta(hours=12, minutes=1)
    assert ostore.expired_attention(conn, now=now - timedelta(hours=1)) == []
    failed = dispatch.expire_attention_steps(conn, lambda pillar: None, now=now)
    assert [j.id for j in failed] == [job.id]
    assert db.get_job(conn, job.id).status == JobStatus.FAILED
    assert "12 hours" in db.get_job(conn, job.id).error
    assert ostore.list_steps(conn, job)[0]["state"] == "failed"


def test_a_job_waiting_for_the_user_is_not_presumed_lost_after_six_hours(conn):
    job = _attention(conn)
    later = datetime.now(timezone.utc) + timedelta(hours=7)
    assert dispatch.expire_stuck_jobs(conn, lambda pillar: None, now=later) == []
    assert db.get_job(conn, job.id).status == JobStatus.RUNNING


def test_ask_returns_the_option_the_user_chose(client, conn):
    job = _running_finish_job(conn)
    answers = iter([None, None, "go"])

    def sleep(_):
        choice = next(answers)
        if choice:
            client.post(f"/v1/jobs/{job.id}/steps/check_clean_tree/answer", json={"choice": choice})

    reporter = StepReporter(None, job.id, call=_fake_call(client), sleep=sleep)
    with reporting(reporter):
        assert can_ask()
        with step("check_clean_tree") as handle:
            assert ask("check_clean_tree", "dirty?", _OPTIONS) == "go"
            handle.detail = "went on"
    row = ostore.list_steps(conn, job)[0]
    assert row["state"] == "succeeded" and row["detail"] == "went on"


def test_ask_with_no_options_fails_without_asking(client, conn):
    job = _running_finish_job(conn)
    with reporting(StepReporter(None, job.id, call=_fake_call(client))):
        with pytest.raises(StepFailed, match="no option"):
            with step("check_clean_tree"):
                ask("check_clean_tree", "dirty?", [])
    assert ostore.list_steps(conn, job)[0]["state"] == "failed"


def test_ask_gives_up_after_the_limit_and_fails_the_step(client, conn):
    job = _running_finish_job(conn)
    ticks = iter(range(0, 100000, 4 * 3600))
    reporter = StepReporter(None, job.id, call=_fake_call(client), sleep=lambda _: None, clock=lambda: next(ticks))
    with reporting(reporter):
        with pytest.raises(StepAborted, match="no answer within 12 hours"):
            with step("check_clean_tree"):
                ask("check_clean_tree", "dirty?", _OPTIONS)
    row = ostore.list_steps(conn, job)[0]
    assert row["state"] == "failed" and "12 hours" in row["detail"]


def test_ask_stops_waiting_when_the_job_is_settled_meanwhile(client, conn):
    job = _running_finish_job(conn)

    def sleep(_):
        job.status, job.error = JobStatus.STOPPED, "stopped by user"
        db.update_job(conn, job)

    with reporting(StepReporter(None, job.id, call=_fake_call(client), sleep=sleep)):
        with pytest.raises(StepAborted, match="stopped"):
            ask("check_clean_tree", "dirty?", _OPTIONS)


def test_ask_cannot_ask_a_step_the_job_does_not_have(client, conn):
    job = _running_finish_job(conn)
    with reporting(StepReporter(None, job.id, call=_fake_call(client))):
        with pytest.raises(StepFailed, match="could not ask"):
            ask("not_a_step", "q", _OPTIONS)


def test_without_a_reporter_nothing_is_reported_and_nobody_can_be_asked():
    assert not can_ask()
    with step("anything") as handle:
        handle.detail = "fine"
    done("anything")
    with pytest.raises(StepFailed, match="nobody to ask"):
        ask("anything", "q", _OPTIONS)


def test_a_reporter_needs_paths_or_a_call():
    with pytest.raises(ValueError):
        StepReporter(None, "j")


def test_progress_reports_survive_an_unreachable_orchestration():
    def broken(method, path, json=None):
        raise ConnectionError("down")

    with reporting(StepReporter(None, "j", call=broken)):
        with step("a"):
            pass
        with pytest.raises(RuntimeError):
            with step("b"):
                raise RuntimeError("real failure")


def test_key_prefix_namespaces_the_steps_of_a_composed_job():
    seen = []
    reporter = StepReporter(None, "j", call=lambda method, path, json=None: seen.append((path, json["state"])) or {})
    with reporting(reporter), key_prefix("start_"):
        done("check_branch")
    assert seen == [("/v1/jobs/j/steps/start_check_branch", "running"), ("/v1/jobs/j/steps/start_check_branch", "succeeded")]


def test_format_seconds():
    assert [job_steps.format_seconds(s) for s in (None, 0.44, 12.4, 185, 3725)] == ["?", "0.4s", "12s", "3m05s", "1h02m"]


def test_the_checklists_of_several_jobs_come_in_one_call(client, conn):
    a = client.post("/v1/jobs", json={"type": "version.feature_finish", "target_pillar": "version"}).json()
    b = client.post("/v1/jobs", json={"type": "noop", "target_pillar": "x"}).json()
    body = client.get("/v1/jobs/steps", params={"ids": f"{a['id']},{b['id']},missing"}).json()
    assert set(body) == {a["id"], b["id"]}
    assert len(body[a["id"]]) == 9 and body[b["id"]][0]["key"] == "run"
