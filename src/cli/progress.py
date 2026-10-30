"""What `lws` shows while a Job runs: its checklist on start, each action as it begins and ends, and
the question when an action needs the user.

Everything goes to stderr, so a command's JSON result on stdout stays parseable. The Job is created
under an id chosen here and polled while its creation call is still running (most Jobs run inside
that call), so the steps show live for every Job type.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from typing import Callable, TextIO

from cli import client
from common.job_steps import format_seconds
from common.paths import Paths

POLL_SECONDS = 1.0
_ICONS = {"pending": "○", "running": "▶", "succeeded": "✓", "failed": "✗", "attention": "⚠"}
_TERMINAL = ("succeeded", "failed", "cancelled", "stopped")


class AwaitingUserAnswer(client.DaemonUnavailable):
    """A step needs an answer and there is no terminal to ask on; the Job waits (up to 12 hours)."""


def render_checklist(steps: list[dict]) -> str:
    lines = []
    width = len(str(len(steps)))
    for step in steps:
        extra = f"  (usually {format_seconds(step['expected_seconds'])})" if step.get("expected_seconds") else ""
        lines.append(f"  {_ICONS[step['state']]} {step['position'] + 1:>{width}}/{len(steps)} {step['title']}{extra}")
    return "\n".join(lines)


class Progress:
    """Prints what changed between one snapshot of a Job and the next."""

    def __init__(
        self, paths: Paths, *, out: TextIO | None = None, interactive: bool | None = None,
        input_fn: Callable[[str], str] = input,
    ) -> None:
        self.paths = paths
        self.out = out or sys.stderr
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self.input_fn = input_fn
        self._listed = False
        self._seen: dict[str, str] = {}

    def _say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def update(self, job: dict) -> None:
        steps = job.get("steps") or []
        if len(steps) < 2:
            return  # a one-action Job has no checklist worth showing
        if not self._listed:
            self._listed = True
            self._say(f"Job {job['id']} ({job['type']}) will:")
            self._say(render_checklist(steps))
            self._seen = {step["key"]: "pending" for step in steps}
        for step in steps:
            state = step["state"]
            if self._seen.get(step["key"]) == state:
                continue
            self._seen[step["key"]] = state
            self._report(job, step)

    def _report(self, job: dict, step: dict) -> None:
        state, title, detail = step["state"], step["title"], step.get("detail")
        if state == "running":
            self._say(f"▶ {title}")
        elif state == "succeeded":
            took = format_seconds(step.get("runtime_seconds"))
            self._say(f"✓ {title} ({took}){f': {detail}' if detail else ''}")
        elif state == "failed":
            self._say(f"✗ {title}{f': {detail}' if detail else ''}")
        elif state == "attention":
            self._ask(job, step)

    def _ask(self, job: dict, step: dict) -> None:
        prompt = step.get("prompt") or {}
        options = prompt.get("options", [])
        self._say(f"⚠ {step['title']} needs your attention")
        self._say(f"  {prompt.get('question', '')}")
        for number, option in enumerate(options, 1):
            self._say(f"  {number}) {option['label']}")
        if not self.interactive:
            answer_with = f"lws job answer {job['id']} {step['key']} <{'|'.join(o['id'] for o in options)}>"
            raise AwaitingUserAnswer(
                f"the job is waiting for your answer (it fails after 12 hours without one): {answer_with}  "
                f"-- or answer in the Dashboard; `lws job watch {job['id']}` follows it again"
            )
        while True:
            try:
                raw = self.input_fn(f"Choose [1-{len(options)}]: ").strip()
            except EOFError:
                raise AwaitingUserAnswer(f"no input to answer with; `lws job answer {job['id']} {step['key']} <option>` answers later")
            chosen = options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else next(
                (o for o in options if o["id"] == raw), None)
            if chosen is None:
                self._say("  not one of the options")
                continue
            client.answer_step(self.paths, job["id"], step["key"], chosen["id"])
            self._say(f"  -> {chosen['label']}")
            self._seen[step["key"]] = "running"
            return

    def finish(self, job: dict) -> None:
        self.update(job)
        if len(job.get("steps") or []) >= 2 and job.get("status") in _TERMINAL:
            self._say(f"Job {job['id']} {job['status']}" + (f": {job['error']}" if job.get("error") else ""))


def _fetch(paths: Paths, job_id: str) -> dict | None:
    try:
        return client.get_job(paths, job_id)
    except client.DaemonUnavailable:
        return None  # not inserted yet, or a blip: ask again


def create_and_follow(paths: Paths, *, timeout: float, **fields) -> dict:
    """Create the Job, showing its steps while the creation call runs, then for as long as it waits for the user."""
    job_id = uuid.uuid4().hex
    progress = Progress(paths)
    box: dict = {}

    def post() -> None:
        try:
            box["job"] = client.post_job(paths, timeout=timeout, id=job_id, **fields)
        except BaseException as exc:  # handed to the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=post, daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            snapshot = _fetch(paths, job_id)
            if snapshot is not None:
                progress.update(snapshot)
            worker.join(POLL_SECONDS)
        if "error" in box:
            raise box["error"]
        job = box["job"]
        progress.update(job)
        job = follow_while_asked(paths, job, progress)
    except KeyboardInterrupt:
        print(f"\ninterrupted: job {job_id} goes on; `lws job watch {job_id}` follows it again", file=sys.stderr)
        raise
    progress.finish(job)
    return job


def follow_while_asked(paths: Paths, job: dict, progress: Progress) -> dict:
    """A running Job that can stop and ask is followed until it ends: whoever started it expects its outcome."""
    asks = any(step.get("needs_attention") for step in job.get("steps") or [])
    while asks and job.get("status") == "running":
        time.sleep(POLL_SECONDS)
        snapshot = _fetch(paths, job["id"])
        if snapshot is not None:
            job = snapshot
            progress.update(job)
    return job


def watch(paths: Paths, job_id: str) -> dict:
    """Follow an existing Job to its end (`lws job watch`)."""
    progress = Progress(paths)
    job = client.get_job(paths, job_id)
    progress.update(job)
    while job.get("status") not in _TERMINAL:
        time.sleep(POLL_SECONDS)
        snapshot = _fetch(paths, job_id)
        if snapshot is not None:
            job = snapshot
            progress.update(job)
    progress.finish(job)
    return job
