"""Orchestration's Job reads, for Dashboard tests: `list_jobs` and `job_counts` over the Jobs a test gives,
selecting, ordering, paging and counting them as Orchestration does in its store (`orchestration.db`
`find_jobs` and `count_jobs`), so a page gets only what its filters ask for."""

from __future__ import annotations

from datetime import datetime, timezone

SEARCHED = ("type", "project_id", "target_pillar", "created_by", "id")


def _moment(value) -> datetime | None:
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None  # a test's malformed time: no time filter selects it
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _several(value) -> list | None:
    return None if value is None else [value] if isinstance(value, str) else list(value)


def select(jobs: list[dict], *, status=None, types=None, project_id=None, created_by=None, since=None, until=None,
           updated_since=None, ids=None, q=None) -> list[dict]:
    statuses, types, ids = _several(status), _several(types), _several(ids)
    needle = (q or "").strip().lower()
    found = []
    for job in jobs:
        created = _moment(job.get("created_at")) if since is not None or until is not None else None
        updated = _moment(job.get("updated_at")) if updated_since is not None else None
        if (statuses is not None and job.get("status") not in statuses) or (types is not None and job.get("type") not in types) \
                or (ids is not None and job.get("id") not in ids) or (project_id is not None and job.get("project_id") != project_id) \
                or (created_by is not None and job.get("created_by") != created_by):
            continue
        if (since is not None and (created is None or created < _moment(since))) or (until is not None and (created is None or created >= _moment(until))) \
                or (updated_since is not None and (updated is None or updated < _moment(updated_since))):
            continue
        if needle and needle not in " ".join(str(job.get(key) or "") for key in SEARCHED).lower():
            continue
        found.append(job)
    return found


def patch_jobs(monkeypatch, client, jobs: list[dict]) -> list[dict]:
    """Have `client` (dashboard.client) read `jobs` (a list a test may still change) as Orchestration would
    give them; returns the reads made, each the keyword arguments of one `list_jobs` call."""
    reads: list[dict] = []

    def list_jobs(paths, *, summary=False, newest_first=False, limit=None, timeout=5.0, **filters):
        reads.append({**filters, "summary": summary, "newest_first": newest_first, "limit": limit})
        found = sorted(select(jobs, **filters), key=lambda job: job.get("created_at") or "", reverse=newest_first)
        return found[:limit] if limit is not None else found

    def job_counts(paths, *, timeout=5.0, **filters):
        counts: dict[str, int] = {}
        for job in select(jobs, **filters):
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        return counts

    monkeypatch.setattr(client, "list_jobs", list_jobs)
    monkeypatch.setattr(client, "job_counts", job_counts)
    return reads
