"""Redeploying several of little-workshop's own pillars: which go first, which can go together.

`REDEPLOY_AFTER` says, for a pillar, which pillars must be redeployed before it when both are being
redeployed. `redeploy_batches` turns the pillars you chose into batches: every pillar in a batch has
nothing to wait for among the others, so a batch's redeploys run side by side, and a batch starts when
the one before it has succeeded. Choose two pillars with no rule between them and one batch is enough;
choose them all and there are four.

The order is the one the maintainer proved by hand (2026-10-03: Orchestration alone, then Build &
Packages, Agentic Development and the Dashboard, then the rest), written as rules so it holds for
any selection:

- **Orchestration first.** Every pillar creates and settles its Jobs through it, and the redeploy
  Jobs themselves are dispatched by it: the others should report to the new one, not the old.
- **Build, Agent, Dashboard second.** They are the pillars that run work for the others (builds,
  tasks, the user's actions) and have to be on the new Orchestration before anything else restarts.
  The proven order puts the four pillars below after them; nothing in the code says why, so the rule
  is recorded as observed, and changing it is a one-line edit here.
- **Policy, Registry, Requirements, Version next** (and the MCP server, a proxy over all of them).
- **Deployment last.** It performs the redeploys, so its own restart interrupts the machinery; it
  also arms a confirmation window. **Core**, if named, goes after even that.

A pillar this table does not know goes with the third group.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

_FIRST = "orchestration"
_RUNNERS = ("build", "agent", "dashboard")
_REST = ("policy", "registry", "requirements", "version", "mcp", "usage")
_DEPLOYER = "deployment"
_CORE = "core"

REDEPLOY_AFTER: dict[str, frozenset[str]] = {
    _FIRST: frozenset(),
    **{name: frozenset({_FIRST}) for name in _RUNNERS},
    **{name: frozenset({_FIRST, *_RUNNERS}) for name in _REST},
    _DEPLOYER: frozenset({_FIRST, *_RUNNERS, *_REST}),
    _CORE: frozenset({_FIRST, *_RUNNERS, *_REST, _DEPLOYER}),
}


def _must_follow(pillar: str) -> frozenset[str]:
    if pillar in REDEPLOY_AFTER:
        return REDEPLOY_AFTER[pillar]
    return frozenset({_FIRST, *_RUNNERS})  # an unknown pillar goes with the third group


def redeploy_batches(pillars: Iterable[str]) -> list[list[str]]:
    """`pillars` as batches in the order they must run, each batch sorted by name. A pillar's batch is one
    after the last batch holding a pillar it must follow, so an unselected pillar never adds a batch."""
    chosen = list(dict.fromkeys(pillars))
    level: dict[str, int] = {}
    remaining = set(chosen)
    while remaining:
        ready = {p for p in remaining if not (_must_follow(p) & remaining)}
        if not ready:
            raise ValueError(f"redeploy rules form a cycle among {sorted(remaining)}")
        for pillar in ready:
            level[pillar] = 1 + max((level[d] for d in _must_follow(pillar) if d in level), default=-1)
        remaining -= ready
    batches: list[list[str]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for pillar, n in level.items():
        batches[n].append(pillar)
    return [sorted(batch) for batch in batches if batch]


# `src/<directory>` -> the pillar it is, where the two differ.
_DIRECTORY_PILLAR = {"mcp_server": "mcp"}
# Code every pillar imports: a change there is a change to all of them.
_SHARED_DIRECTORIES = ("common",)


def changed_pillars(changed_files: Iterable[str], live_pillars: Iterable[str]) -> list[str]:
    """The live pillars whose code `changed_files` (paths relative to the repository) touch: the
    pillar of each `src/<pillar>/` directory, and every live pillar when shared code changed. Files
    outside `src/` (docs, tests, tools) and the CLI redeploy nothing."""
    live = list(dict.fromkeys(live_pillars))
    touched: set[str] = set()
    for path in changed_files:
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "src":
            continue
        if parts[1] in _SHARED_DIRECTORIES:
            return sorted(live)
        touched.add(_DIRECTORY_PILLAR.get(parts[1], parts[1]))
    return sorted(pillar for pillar in live if pillar in touched)


CreateJob = Callable[[dict], object]


def submit_redeploy_batches(
    create_job: CreateJob, pillars: Iterable[str], commit: str, *, created_by: str, depends_on: Iterable[str] = (),
    confirm_timeout_seconds: int | None = None,
) -> list[list[dict]]:
    """Create one `deployment.redeploy_pillar` Job per pillar, each waiting (`depends_on`) for every Job of the
    batch before it, and for `depends_on` when it is in the first. Every Job gets its id here, so all of them
    can be created at once: a Job whose dependency does not exist yet just stays queued. `create_job(template)`
    creates one Job; a first-batch Job with nothing to wait for runs inside that call, so creation is
    concurrent, and the call returns once those redeploys have finished. Returns the plan,
    [[{"pillar", "job_id"}, ...], ...], as soon as it is made."""
    plan: list[list[dict]] = []
    templates: list[dict] = []
    previous = list(depends_on)
    for batch in redeploy_batches(pillars):
        row = []
        for pillar in batch:
            job_id = uuid.uuid4().hex
            params: dict = {"pillar": pillar, "commit": commit}
            if confirm_timeout_seconds:
                params["confirm_timeout_seconds"] = confirm_timeout_seconds
            templates.append({
                "id": job_id, "type": "deployment.redeploy_pillar", "target_pillar": "deployment", "project_id": pillar,
                "params": params, "queue_key": pillar, "depends_on": list(previous), "created_by": created_by,
            })
            row.append({"pillar": pillar, "job_id": job_id})
        plan.append(row)
        previous = [r["job_id"] for r in row]
    if templates:
        with ThreadPoolExecutor(max_workers=len(templates)) as pool:
            list(pool.map(create_job, templates))
    return plan
