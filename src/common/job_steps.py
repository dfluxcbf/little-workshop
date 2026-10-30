"""A Job's checklist: the named actions it takes, in order, and how a pillar
reports and asks about them.

Every Job type has a list of `StepSpec`s (`common/step_specs/`); Orchestration
stores one row per step for each Job and shows it everywhere (`lws`, the
Dashboard, the MCP tools). The pillar running the Job reports each step as it
goes through a `StepReporter`; handler code never builds one, it uses the
context helpers (`step`, `ask`), which do nothing when no reporter is
installed -- a handler called directly, as unit tests do, runs unchanged.

A step in the state `attention` waits for the user: `ask` posts the question
and its options and blocks until the answer arrives, the Job is settled by
someone else, or `ATTENTION_TIMEOUT_SECONDS` pass, which fails the step and
the Job. Asking with no options fails the step at once: there is nothing the
user could choose.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import httpx

from common.paths import Paths
from common.pillar_socket import resolve_pillar_socket
from common.uds_client import client_for_socket

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
ATTENTION = "attention"
STEP_STATES = (PENDING, RUNNING, SUCCEEDED, FAILED, ATTENTION)
TERMINAL_STEP_STATES = (SUCCEEDED, FAILED)

# How long a step waits for the user before it, and its Job, fail.
ATTENTION_TIMEOUT_SECONDS = 12 * 60 * 60
# How many successful runs of a step the "expected runtime" averages.
EXPECTED_RUNTIME_SAMPLES = 50

_JOB_TERMINAL = ("succeeded", "failed", "cancelled", "stopped")


@dataclass(frozen=True)
class StepSpec:
    key: str
    title: str
    # Informational: this step may stop and ask the user how to proceed.
    needs_attention: bool = False


def default_steps(job_type: str) -> tuple[StepSpec, ...]:
    """A Job type nobody declared steps for is one action: the whole Job."""
    return (StepSpec("run", f"Run {job_type}"),)


@dataclass(frozen=True)
class Option:
    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label}


class StepFailed(Exception):
    """The current step cannot finish; its Job fails with this message."""


class StepAborted(StepFailed):
    """The Job was settled by someone else (stopped, timed out) while a step waited."""


Call = Callable[..., dict]


def _socket_call(paths: Paths) -> Call:
    def call(method: str, path: str, json: dict | None = None) -> dict:
        socket_path = resolve_pillar_socket(paths, "orchestration", timeout=5.0)
        if socket_path is None:
            raise ConnectionError("the orchestration pillar is unavailable")
        try:
            with client_for_socket(socket_path, timeout=10.0) as client:
                response = client.request(method, path, json=json)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            raise ConnectionError(f"could not reach the orchestration pillar: {exc}") from exc
        if response.status_code >= 400:
            raise ConnectionError(f"orchestration {method} {path}: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    return call


class StepReporter:
    """Reports one Job's steps to Orchestration. Progress reports are best
    effort (a blip must not fail the Job); a question is not: `ask` needs
    the answer, so it keeps trying for as long as it may wait."""

    def __init__(
        self, paths: Paths | None, job_id: str, *, call: Call | None = None, poll_seconds: float = 2.0,
        attention_timeout: float = ATTENTION_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if call is None and paths is None:
            raise ValueError("a StepReporter needs paths or a call")
        self.job_id = job_id
        self._call: Call = call or _socket_call(paths)  # type: ignore[arg-type]
        self.poll_seconds = poll_seconds
        self.attention_timeout = attention_timeout
        self._sleep = sleep
        self._clock = clock

    def report(self, key: str, state: str, *, detail: str | None = None, prompt: dict | None = None) -> None:
        try:
            self._call("POST", f"/v1/jobs/{self.job_id}/steps/{key}", json={"state": state, "detail": detail, "prompt": prompt})
        except ConnectionError:
            pass

    def _step(self, key: str) -> dict | None:
        job = self._call("GET", f"/v1/jobs/{self.job_id}")
        if job.get("status") in _JOB_TERMINAL:
            raise StepAborted(f"the job was {job['status']} while waiting for an answer: {job.get('error') or ''}".strip())
        return next((s for s in job.get("steps", []) if s["key"] == key), None)

    def ask(self, key: str, question: str, options: list[Option]) -> str:
        if not options:
            raise StepFailed(f"{question} (no option is available to continue)")
        ids = {option.id for option in options}
        try:
            self._call("POST", f"/v1/jobs/{self.job_id}/steps/{key}", json={
                "state": ATTENTION, "detail": None, "prompt": {"question": question, "options": [o.as_dict() for o in options]},
            })
        except ConnectionError as exc:
            raise StepFailed(f"{question} (could not ask: {exc})") from exc
        deadline = self._clock() + self.attention_timeout
        while True:
            try:
                row = self._step(key)
            except ConnectionError:
                row = None  # a blip while waiting is not an answer; keep asking until the deadline
            if row is not None:
                if row["state"] == FAILED:
                    raise StepAborted(row.get("detail") or "the step failed while waiting for an answer")
                if row["state"] == RUNNING and row.get("answer") in ids:
                    return row["answer"]
            if self._clock() >= deadline:
                message = f"no answer within {self.attention_timeout / 3600:g} hours: {question}"
                self.report(key, FAILED, detail=message)
                raise StepAborted(message)
            self._sleep(self.poll_seconds)


_current: contextvars.ContextVar[StepReporter | None] = contextvars.ContextVar("job_step_reporter", default=None)
_prefix: contextvars.ContextVar[str] = contextvars.ContextVar("job_step_prefix", default="")


@contextmanager
def key_prefix(prefix: str) -> Iterator[None]:
    """Run code that reports steps under `prefix` + its own keys: a Job built from other
    commands (the Release job runs release start and finish) lists their steps under its own."""
    token = _prefix.set(_prefix.get() + prefix)
    try:
        yield
    finally:
        _prefix.reset(token)


@contextmanager
def reporting(reporter: StepReporter | None) -> Iterator[None]:
    """Install `reporter` for the code run inside (and the threads it copies the context to)."""
    token = _current.set(reporter)
    try:
        yield
    finally:
        _current.reset(token)


def current_reporter() -> StepReporter | None:
    return _current.get()


@dataclass
class StepHandle:
    """What a `step` block can say about its outcome."""

    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@contextmanager
def step(key: str, *, detail: str | None = None) -> Iterator[StepHandle]:
    """Run one action of the Job: running on entry, succeeded on a normal exit
    (with `handle.detail`), failed -- with the exception's message -- when it raises."""
    reporter = current_reporter()
    key = _prefix.get() + key
    handle = StepHandle(detail=detail)
    if reporter is not None:
        reporter.report(key, RUNNING)
    try:
        yield handle
    except StepAborted:
        raise  # the step is already failed (or the whole Job settled)
    except BaseException as exc:
        if reporter is not None:
            reporter.report(key, FAILED, detail=str(exc) or type(exc).__name__)
        raise
    if reporter is not None:
        reporter.report(key, SUCCEEDED, detail=handle.detail)


def done(key: str, detail: str | None = None) -> None:
    """An action with nothing to wait for (a check that found nothing to do)."""
    with step(key, detail=detail):
        pass


def ask(key: str, question: str, options: list[Option]) -> str:
    """Ask the user how to proceed on step `key` and return the chosen option id.
    Without a reporter (no Job is watching) there is nobody to ask: the step fails."""
    reporter = current_reporter()
    if reporter is None:
        raise StepFailed(f"{question} (nobody to ask: the action was not run as a job)")
    return reporter.ask(_prefix.get() + key, question, options)


def can_ask() -> bool:
    """Whether a question put to `ask` reaches a user (a reporter is installed)."""
    return current_reporter() is not None


def format_seconds(seconds: float | None) -> str:
    """'4.2s', '38s', '3m05s', '1h02m' -- a step's runtime as people read it."""
    if seconds is None:
        return "?"
    if seconds < 10:
        return f"{seconds:.1f}s"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
