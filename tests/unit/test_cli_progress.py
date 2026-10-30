"""What `lws` prints while a Job runs and how it asks the user."""

from __future__ import annotations

import io

import pytest

from cli import client, progress

Progress = progress.Progress
from common.paths import Paths


def _step(key, state, position, **extra):
    return {"key": key, "title": f"Do {key}", "position": position, "state": state, "detail": None, "prompt": None,
            "needs_attention": False, "runtime_seconds": None, "expected_seconds": None, **extra}


def _job(*steps, status="running"):
    return {"id": "job1", "type": "version.feature_finish", "status": status, "error": None, "steps": list(steps)}


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")


def _progress(paths, **kwargs):
    out = io.StringIO()
    return Progress(paths, out=out, **kwargs), out


# --- REQ-000000312: The CLI and the Dashboard show what a job is doing ---

def test_the_checklist_is_printed_first_then_each_action_as_it_starts_and_ends(paths):
    shown, out = _progress(paths, interactive=False)
    shown.update(_job(_step("a", "pending", 0), _step("b", "pending", 1, expected_seconds=3.0)))
    shown.update(_job(_step("a", "running", 0), _step("b", "pending", 1)))
    shown.update(_job(_step("a", "succeeded", 0, runtime_seconds=1.25, detail="ok"), _step("b", "running", 1)))
    shown.update(_job(_step("a", "succeeded", 0), _step("b", "failed", 1, detail="boom"), status="failed"))
    text = out.getvalue()
    assert text.index("will:") < text.index("○ 1/2 Do a") < text.index("▶ Do a") < text.index("✓ Do a (1.2s): ok") < text.index("▶ Do b") < text.index("✗ Do b: boom")
    assert "(usually 3.0s)" in text
    assert text.count("▶ Do a") == 1  # a state is announced once


def test_a_one_action_job_prints_nothing(paths):
    shown, out = _progress(paths, interactive=False)
    shown.update(_job(_step("run", "running", 0)))
    shown.finish(_job(_step("run", "succeeded", 0), status="succeeded"))
    assert out.getvalue() == ""


def test_the_end_of_a_listed_job_is_announced(paths):
    shown, out = _progress(paths, interactive=False)
    shown.finish({**_job(_step("a", "succeeded", 0), _step("b", "failed", 1), status="failed"), "error": "step b failed"})
    assert "Job job1 failed: step b failed" in out.getvalue()


_ASKING = _step("a", "attention", 0, needs_attention=True, prompt={
    "question": "dirty tree?", "options": [{"id": "commit", "label": "Commit"}, {"id": "stop", "label": "Stop"}]})


def test_without_a_terminal_a_question_is_printed_with_how_to_answer_it(paths):
    shown, out = _progress(paths, interactive=False)
    with pytest.raises(progress.AwaitingUserAnswer, match="lws job answer job1 a <commit\\|stop>"):
        shown.update(_job(_ASKING, _step("b", "pending", 1)))
    assert "dirty tree?" in out.getvalue() and "1) Commit" in out.getvalue()


def test_in_a_terminal_the_user_picks_by_number_or_id_and_the_answer_is_sent(paths, monkeypatch):
    sent = []
    monkeypatch.setattr(client, "answer_step", lambda p, job, key, choice: sent.append((job, key, choice)))
    replies = iter(["9", "wat", "2"])
    shown, out = _progress(paths, interactive=True, input_fn=lambda prompt: next(replies))
    shown.update(_job(_ASKING, _step("b", "pending", 1)))
    assert sent == [("job1", "a", "stop")] and out.getvalue().count("not one of the options") == 2
    shown2, _ = _progress(paths, interactive=True, input_fn=lambda prompt: "commit")
    shown2.update(_job(_ASKING, _step("b", "pending", 1)))
    assert sent[-1] == ("job1", "a", "commit")


def test_no_input_left_means_the_job_keeps_waiting(paths):
    def eof(prompt):
        raise EOFError

    shown, _ = _progress(paths, interactive=True, input_fn=eof)
    with pytest.raises(progress.AwaitingUserAnswer, match="lws job answer"):
        shown.update(_job(_ASKING, _step("b", "pending", 1)))


def test_create_and_follow_shows_steps_while_the_creation_call_runs(paths, monkeypatch):
    calls = []
    snapshots = iter([None, _job(_step("a", "running", 0), _step("b", "pending", 1))])
    monkeypatch.setattr(progress, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(progress, "Progress", lambda p: progress_obj)
    progress_obj, out = _progress(paths, interactive=False)
    release = __import__("threading").Event()

    def post(p, *, timeout, **fields):
        calls.append(fields)
        release.wait(2)
        return _job(_step("a", "succeeded", 0), _step("b", "succeeded", 1), status="succeeded")

    monkeypatch.setattr(client, "post_job", post)
    monkeypatch.setattr(progress, "_fetch", lambda p, job_id: (release.set() if (snap := next(snapshots, None)) else None) or snap)
    result = progress.create_and_follow(paths, timeout=5, type="version.feature_finish", target_pillar="version")
    assert result["status"] == "succeeded" and calls[0]["id"] and "▶ Do a" in out.getvalue()
    assert "Job job1 succeeded" in out.getvalue()


def test_a_job_that_can_ask_is_followed_until_it_ends(paths, monkeypatch):
    monkeypatch.setattr(progress, "POLL_SECONDS", 0.01)
    asking = [_step("a", "running", 0, needs_attention=True), _step("b", "pending", 1)]
    done = [_step("a", "succeeded", 0, needs_attention=True), _step("b", "succeeded", 1)]
    snapshots = iter([_job(*asking), _job(*done, status="succeeded")])
    monkeypatch.setattr(progress, "_fetch", lambda p, job_id: next(snapshots))
    shown, _ = _progress(paths, interactive=False)
    assert progress.follow_while_asked(paths, _job(*asking), shown)["status"] == "succeeded"
    plain = _job(_step("a", "running", 0), _step("b", "pending", 1))
    assert progress.follow_while_asked(paths, plain, shown) is plain  # long jobs that never ask are not waited for


def test_watch_follows_an_existing_job_to_its_end(paths, monkeypatch):
    monkeypatch.setattr(progress, "POLL_SECONDS", 0.01)
    states = iter([_job(_step("a", "running", 0), _step("b", "pending", 1)), _job(_step("a", "succeeded", 0), _step("b", "succeeded", 1), status="succeeded")])
    monkeypatch.setattr(client, "get_job", lambda p, job_id: next(states))
    monkeypatch.setattr(progress, "_fetch", lambda p, job_id: next(states))
    assert progress.watch(paths, "job1")["status"] == "succeeded"


def test_progress_is_off_with_the_environment_switch(paths, monkeypatch):
    monkeypatch.setenv("LWS_NO_PROGRESS", "1")
    monkeypatch.setattr(client, "post_job", lambda p, **fields: {"posted": fields})
    assert client.create_job(paths, type="x") == {"posted": {"timeout": 30.0, "type": "x"}}


def test_format_helpers():
    assert progress.render_checklist([_step("a", "pending", 0), _step("b", "attention", 1)]).splitlines()[1].strip().startswith("⚠ 2/2")
