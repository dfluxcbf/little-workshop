"""Dashboard routes: the Statistics page and its regular count."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dashboard import client, live, statistics
from dashboard.app import PILLAR_NAMES, STATISTICS_UPDATE_SECONDS, _try, pillar_name


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    paths, background, _render = ctx.paths, ctx.background, ctx.render

    # -- Statistics: per-day counts of the workshop's operations, kept by the Dashboard ----

    def _update_statistics() -> list[str]:
        """Count what the pillars hold now; returns the pillars that could not be read. Of the Jobs and policy
        check results, only those changed or recorded since the last count are read (Jobs as summaries),
        and all of them once a day, which lets the count forget what was deleted since (statistics.to_read)."""
        since = statistics.to_read(paths)
        read_at = datetime.now(timezone.utc)
        sources = {
            "projects": ("registry", lambda: [project["name"] for project in client.list_projects(paths)]),
            "jobs": ("orchestration", lambda: client.list_jobs(paths, updated_since=since["jobs"], summary=True, timeout=30.0)),
            "policy_results": ("policy", lambda: client.policy_check_results(paths, checked_since=since["policy_results"], timeout=30.0)),
            "policies": ("policy", lambda: client.policy_list(paths, summary=True)),
            "incidents": ("agent", lambda: client.list_incidents(paths)),
        }
        fetched = {name: _try(fetch) for name, (_, fetch) in sources.items()}
        if fetched["policies"] is None:  # without the policies a result cannot be named
            fetched["policy_results"] = None
        statistics.update(paths, **fetched, since=since, read_at=read_at, full_job=lambda job_id: _try(client.get_job, paths, job_id))
        return sorted({pillar_name(pillar) for name, (pillar, _) in sources.items() if fetched[name] is None})

    ctx.update_statistics = _update_statistics

    @router.get("/statistics", response_class=HTMLResponse)
    def statistics_page(request: Request):
        down = _update_statistics()  # every visit counts what is new before plotting
        error = f"Could not read {', '.join(down)}; what they would add is not counted yet." if down else None
        return _render(request, "statistics.html", stats=statistics.load(paths), pillar_names=PILLAR_NAMES, error=error, nav_active="statistics",
                       live_topics="statistics")

    # Nobody has to open the page for a day to be counted: the running Dashboard also counts on its own.
    def _count_statistics_regularly(stop: threading.Event) -> None:
        while True:
            try:
                _update_statistics()
            except Exception:  # noqa: BLE001 - a failed round must not end the loop; the next one retries
                pass
            if stop.wait(STATISTICS_UPDATE_SECONDS):
                return

    background.append(_count_statistics_regularly)
    background.extend(live.Watchers(paths, recount_statistics=_update_statistics).loops())

    return router
