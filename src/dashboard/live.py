"""Live pages: each open page keeps one server-sent event stream (`/live/events`) subscribed to the
kinds of information it shows (its topics), and is told the moment one of them changes; the page then
fetches itself and replaces only the parts that changed (`static/live.js`). Nothing polls in the
browser, and nothing here runs while no page is open.

What changed is learned from the pillars as cheaply as each allows:

- Jobs, their steps and scheduled tasks: Orchestration's change feed, a long poll answered the moment
  something moves (`watch_orchestration`). Each change becomes the topics of the pages that show it
  (`job_topics`).
- The backlog, Claude usage and incidents: a version string each pillar serves for one cheap read,
  compared every few seconds while a page shows them (`watch_versions`). Statistics are recounted when
  a Job has settled, at most every `STATISTICS_EVERY` seconds, while a page shows them.
- What Claude is doing in a task (the peek): the new part of its transcript, read every
  `PEEK_SECONDS` while a page watches it (`watch_peeks`), sent with the event itself.

The hub holds no data of its own: an event says "look again", or carries the new transcript lines."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from common.paths import Paths
from dashboard import client

LONG_POLL_SECONDS = 25.0
VERSION_SECONDS = 3.0
PEEK_SECONDS = 1.5
STATISTICS_EVERY = 15.0
RETRY_SECONDS = 5.0
QUEUE_SIZE = 200

# Topics whose changes Orchestration's feed does not carry, and what tells them apart.
def pillars_version(paths: Paths) -> str:
    """What the Pillars and Dashboard pages show of each pillar (its health, crashes and interface), as one
    string that changes when any of it does. Core keeps it in memory, so asking is cheap."""
    pillars = client.core_status(paths).get("pillars") or {}
    return json.dumps({name: [entry.get("status"), entry.get("crash_count"), entry.get("interface_version")]
                       for name, entry in sorted(pillars.items())})


VERSION_SOURCES: dict[str, Callable[[Paths], str]] = {
    "backlog": lambda paths: client.backlog_version(paths),
    "usage": lambda paths: client.usage_version(paths),
    "incidents": lambda paths: client.incidents_version(paths),
    "pillars": pillars_version,
}
# An incident appearing is also something needing attention; a pillar's health is on the Dashboard page too.
ALSO_PUBLISH = {"incidents": ("attention",), "pillars": ("dashboard",)}

_TASK_TYPES = {"agent.run_task", "agent.finish_task", "agent.skip_task", "agent.dismiss_task", "agent.confirm_incident"}
_DASHBOARD_TYPES = {"agent.session_start", "agent.session_stop", "agent.ci_check", "project.bootstrap", "project.remove"}
_SETTLED = {"succeeded", "failed", "cancelled", "stopped"}


def job_topics(change: dict) -> set[str]:
    """The topics a change of Orchestration's feed concerns."""
    kind = change.get("kind")
    if kind == "scheduled_task":
        return {"schedule"}
    if kind == "jobs_deleted":
        return {"jobs", "schedule", "tasks"}
    job_id, job_type, status = change.get("id") or "", change.get("type") or "", change.get("status") or ""
    topics = {"jobs", f"job:{job_id}"}
    if kind == "step":
        return topics
    topics |= {"schedule", "attention"}
    if job_type in _TASK_TYPES:
        topics |= {"tasks", "dashboard"}
    if job_type.startswith(("deployment.", "requirements.")) or job_type in _DASHBOARD_TYPES:
        topics.add("dashboard")
    if job_type.startswith("deployment."):
        topics.add("pillars")  # a redeploy changes the commit a pillar runs
    if job_type.startswith("policy."):
        topics.add("policies")
    if job_type.startswith("registry.backlog_"):
        topics.add("backlog")
    if job_type in ("agent.confirm_incident", "agent.dismiss_incident"):
        topics.add("incidents")
    return topics


def settled(change: dict) -> bool:
    return change.get("kind") == "job" and change.get("status") in _SETTLED


@dataclass(eq=False)
class Subscriber:
    """One open page: the topics it shows, and the queue its event stream reads from."""

    topics: frozenset[str]
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE))

    def push(self, message: dict) -> None:
        """From any thread: hand the message to the page's stream (an old one gives way when it is full)."""
        def put() -> None:
            if self.queue.full():
                self.queue.get_nowait()
            self.queue.put_nowait(message)

        try:
            self.loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass  # the page's loop is gone: it unsubscribes on its way out


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[Subscriber] = set()

    def subscribe(self, topics, loop: asyncio.AbstractEventLoop) -> Subscriber:
        subscriber = Subscriber(frozenset(topics), loop)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def topics(self) -> set[str]:
        """Every topic some open page shows."""
        with self._lock:
            return set().union(*(s.topics for s in self._subscribers)) if self._subscribers else set()

    def publish(self, topic: str, data: dict | None = None) -> int:
        """Tell the pages showing `topic`; returns how many there were."""
        message = {"topic": topic, **({"data": data} if data is not None else {})}
        with self._lock:
            wanted = [s for s in self._subscribers if topic in s.topics]
        for subscriber in wanted:
            subscriber.push(message)
        return len(wanted)


HUB = Hub()


def publish(topic: str, data: dict | None = None) -> int:
    return HUB.publish(topic, data)


class Watchers:
    """The background loops behind the hub (started with the Dashboard). `recount_statistics` counts
    what the pillars hold into the Statistics store (`app._update_statistics`)."""

    def __init__(self, paths: Paths, hub: Hub = HUB, *, recount_statistics: Callable[[], object] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.paths = paths
        self.hub = hub
        self.recount_statistics = recount_statistics
        self.clock = clock
        self.statistics_due = False
        self._last_recount = float("-inf")
        self._versions: dict[str, str] = {}
        self._peeks: dict[str, int] = {}

    # -- Orchestration: Jobs, steps, scheduled tasks ------------------------------------------

    def follow_orchestration_once(self, cursor: dict | None, *, timeout: float = LONG_POLL_SECONDS) -> dict | None:
        """One round of the long poll. `cursor` is {"boot", "seq"} from the round before (None: learn
        where the feed stands, publishing nothing). Returns the next cursor."""
        if cursor is None:
            body = client.orchestration_changes(self.paths, after=None, boot=None, timeout=0)
            return {"boot": body["boot"], "seq": body["seq"]}
        body = client.orchestration_changes(self.paths, after=cursor["seq"], boot=cursor["boot"], timeout=timeout)
        if body.get("reset"):
            for topic in ("jobs", "tasks", "schedule", "dashboard", "attention", "policies", "backlog"):
                self.hub.publish(topic)
            self.statistics_due = True
            for topic in self.hub.topics():
                if topic.startswith("job:"):
                    self.hub.publish(topic)
        else:
            topics: set[str] = set()
            for change in body.get("changes", []):
                topics |= job_topics(change)
                self.statistics_due = self.statistics_due or settled(change)
            for topic in sorted(topics):
                self.hub.publish(topic)
        return {"boot": body["boot"], "seq": body["seq"]}

    def watch_orchestration(self, stop: threading.Event) -> None:
        cursor = None
        while not stop.is_set():
            if not self.hub.topics():
                cursor = None  # nobody is looking: start from where the feed stands when somebody does
                stop.wait(1.0)
                continue
            try:
                cursor = self.follow_orchestration_once(cursor)
            except client.PillarUnavailable:
                cursor = None
                stop.wait(RETRY_SECONDS)
            except Exception:  # noqa: BLE001 - one bad round must not end the loop; the next one retries
                cursor = None
                stop.wait(RETRY_SECONDS)

    # -- what the feed does not carry: versions, statistics, Claude's transcript -----------------

    def check_versions_once(self) -> None:
        wanted = self.hub.topics()
        for topic, read in VERSION_SOURCES.items():
            if not {topic, *ALSO_PUBLISH.get(topic, ())} & wanted:  # no page shows it: not asked
                self._versions.pop(topic, None)
                continue
            try:
                version = read(self.paths)
            except (client.PillarUnavailable, KeyError, ValueError):
                continue
            before = self._versions.get(topic)
            self._versions[topic] = version
            if before is not None and before != version:
                for name in (topic, *ALSO_PUBLISH.get(topic, ())):
                    self.hub.publish(name)
        if self.statistics_due and "statistics" in wanted and self.recount_statistics is not None:
            if self.clock() - self._last_recount >= STATISTICS_EVERY:
                self.statistics_due = False
                self._last_recount = self.clock()
                self.recount_statistics()
                self.hub.publish("statistics")

    def watch_versions(self, stop: threading.Event) -> None:
        while not stop.wait(VERSION_SECONDS):
            try:
                self.check_versions_once()
            except Exception:  # noqa: BLE001 - as above
                pass

    def check_peeks_once(self) -> None:
        """For each transcript a page watches: send what was added since the last look, and once more
        when the session stops running."""
        wanted = {topic.split(":", 1)[1] for topic in self.hub.topics() if topic.startswith("peek:")}
        for job_id in set(self._peeks) - wanted:
            del self._peeks[job_id]
        for job_id in wanted:
            try:
                if job_id not in self._peeks:  # first look: where the transcript ends now (the page has the rest)
                    peek = client.agent_peek(self.paths, job_id, after=-1) or {}
                    self._peeks[job_id] = {"next": peek.get("next", 0), "running": bool(peek.get("running"))}
                    continue
                seen = self._peeks[job_id]
                peek = client.agent_peek(self.paths, job_id, after=seen["next"])
            except client.PillarUnavailable:
                continue
            if peek is None:
                continue
            running, before = bool(peek.get("running")), seen["next"]
            if peek.get("events") or running != seen["running"]:
                self.hub.publish(f"peek:{job_id}", {"from": before, "next": peek.get("next", before), "events": peek.get("events", []),
                                                    "running": running, "skipped": peek.get("skipped", 0)})
            self._peeks[job_id] = {"next": peek.get("next", before), "running": running}

    def watch_peeks(self, stop: threading.Event) -> None:
        while not stop.wait(PEEK_SECONDS):
            try:
                self.check_peeks_once()
            except Exception:  # noqa: BLE001 - as above
                pass

    def loops(self) -> list[Callable[[threading.Event], None]]:
        return [self.watch_orchestration, self.watch_versions, self.watch_peeks]
