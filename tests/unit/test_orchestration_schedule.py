"""Pure schedule-time arithmetic — no sqlite, no I/O."""

from datetime import datetime

from orchestration.models import Schedule, ScheduleKind
from orchestration.schedule import first_occurrence, next_occurrence_after


def test_once_first_occurrence_is_just_at():
    at = datetime(2026, 1, 1, 9, 0)
    sched = Schedule(kind=ScheduleKind.ONCE, at=at)
    assert first_occurrence(sched, now=datetime(2025, 12, 1)) == at


def test_once_first_occurrence_can_already_be_in_the_past():
    at = datetime(2020, 1, 1, 9, 0)
    sched = Schedule(kind=ScheduleKind.ONCE, at=at)
    assert first_occurrence(sched, now=datetime(2026, 1, 1)) == at


def test_once_has_no_next_occurrence_ever():
    sched = Schedule(kind=ScheduleKind.ONCE, at=datetime(2026, 1, 1, 9, 0))
    assert next_occurrence_after(sched, after=datetime(2026, 1, 1, 9, 0)) is None
    assert next_occurrence_after(sched, after=datetime(2020, 1, 1)) is None


def test_recurring_first_occurrence_is_the_soonest_matching_weekday():
    # 2026-01-05 is a Monday.
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0], time_of_day="09:00")
    result = first_occurrence(sched, now=datetime(2026, 1, 5, 8, 0))
    assert result == datetime(2026, 1, 5, 9, 0)


def test_recurring_first_occurrence_skips_to_next_week_if_todays_time_passed():
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0], time_of_day="09:00")
    result = first_occurrence(sched, now=datetime(2026, 1, 5, 10, 0))  # Monday, past 09:00
    assert result == datetime(2026, 1, 12, 9, 0)


def test_recurring_multiple_weekdays_picks_the_nearest():
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0, 2, 4], time_of_day="02:00")
    # 2026-01-06 is a Tuesday; next matching day (Mon/Wed/Fri) is Wednesday.
    result = first_occurrence(sched, now=datetime(2026, 1, 6, 15, 0))
    assert result == datetime(2026, 1, 7, 2, 0)


def test_next_occurrence_after_wraps_to_next_week():
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0], time_of_day="09:00")
    last = datetime(2026, 1, 5, 9, 0)
    assert next_occurrence_after(sched, after=last) == datetime(2026, 1, 12, 9, 0)


def test_next_occurrence_after_is_strictly_after_not_equal():
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[0, 1], time_of_day="09:00")
    # Right at Monday 09:00 -- Tuesday 09:00 is next, not Monday again.
    result = next_occurrence_after(sched, after=datetime(2026, 1, 5, 9, 0))
    assert result == datetime(2026, 1, 6, 9, 0)


def test_recurring_with_no_days_never_fires():
    sched = Schedule(kind=ScheduleKind.RECURRING, days_of_week=[], time_of_day="09:00")
    assert first_occurrence(sched, now=datetime(2026, 1, 1)) is None
    assert next_occurrence_after(sched, after=datetime(2026, 1, 1)) is None
