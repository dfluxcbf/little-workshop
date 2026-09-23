"""Pure schedule-time computation for `ScheduledTask` — no I/O, no
sqlite, so the tricky "what's the next occurrence" arithmetic is
directly unit-testable. All datetimes here are naive and in the host's
own local time, per `Schedule`'s own docstring — never UTC, never
timezone-aware.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from orchestration.models import Schedule, ScheduleKind


def _time_of_day_parts(time_of_day: str) -> tuple[int, int]:
    hour, minute = time_of_day.split(":")
    return int(hour), int(minute)


def first_occurrence(schedule: Schedule, *, now: datetime) -> datetime | None:
    """The first time this schedule should ever fire, computed once at
    creation time. For `once`, that's just `at` — even if it's already
    in the past (the caller decides what to do with an immediately-due
    task; this function only computes the schedule's own arithmetic)."""
    if schedule.kind == ScheduleKind.ONCE:
        return schedule.at
    return next_occurrence_after(schedule, after=now - timedelta(seconds=1))


def next_occurrence_after(schedule: Schedule, *, after: datetime) -> datetime | None:
    """The next occurrence strictly after `after`. `once` has no next
    occurrence once its single firing has happened — always None."""
    if schedule.kind == ScheduleKind.ONCE:
        return None

    hour, minute = _time_of_day_parts(schedule.time_of_day)
    days = sorted(schedule.days_of_week)
    if not days:
        return None

    for offset in range(8):  # a full week plus the starting day, inclusive
        candidate_date = after.date() + timedelta(days=offset)
        if candidate_date.weekday() not in days:
            continue
        candidate = datetime(
            candidate_date.year, candidate_date.month, candidate_date.day, hour, minute
        )
        if candidate > after:
            return candidate
    return None  # unreachable when `days` is non-empty
