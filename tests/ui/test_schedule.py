"""Schedule: a tap on a day lists its jobs and tasks in the panel, each expanding on its own (REQ-000000375).

Only reads: nothing is started, scheduled or stopped on whatever app it is pointed at. The month it
opens may have few items, so the day it taps is the one with the most.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

BUSIEST_DAY = """() => {
  let best = null, most = -1;
  document.querySelectorAll('#schedule-live [data-day]').forEach(cell => {
    const n = cell.querySelector('template').content.querySelectorAll('.cal-entry').length;
    if (n > most) { most = n; best = cell.dataset.day; }
  });
  return [best, most];
}"""


# --- REQ-000000375: A calendar of jobs and tasks ---


def test_the_month_of_today_opens_on_today_and_today_goes_back_to_it(page: Page):
    page.goto("/schedule")
    expect(page.locator("#schedule-live .cal-day.today")).to_have_class(re.compile(r"\bselected\b"))
    expect(page.locator("#cal-panel .cal-sheet-rel")).to_have_text("Today")
    page.locator("#schedule-live .cal-day:not(.today)").first.click()
    expect(page.locator("#cal-panel .cal-sheet-rel")).not_to_have_text("Today")
    page.locator(".cal-nav-today").click()
    expect(page.locator("#cal-panel .cal-sheet-rel")).to_have_text("Today")
    expect(page).to_have_url(re.compile(r"/schedule$"))  # listed in place, not loaded anew


def test_a_tap_on_a_day_lists_its_items_collapsed_and_nothing_on_the_calendar_is_a_link(page: Page):
    page.goto("/schedule?show=all")
    expect(page.locator("#schedule-live a")).to_have_count(0)
    day, count = page.evaluate(BUSIEST_DAY)
    cell = page.locator(f'#schedule-live [data-day="{day}"]')
    cell.click()
    expect(cell).to_have_class(re.compile(r"\bselected\b"))
    entries = page.locator("#cal-panel .cal-entry")
    expect(entries).to_have_count(count)
    if not count:
        expect(page.locator("#cal-panel")).to_contain_text("Nothing ran or is scheduled on this day.")
    for n in range(min(count, 10)):
        expect(entries.nth(n)).to_have_js_property("open", False)


def test_items_in_the_panel_expand_and_collapse_each_on_its_own(page: Page):
    page.goto("/schedule?show=all")
    day, count = page.evaluate(BUSIEST_DAY)
    page.locator(f'#schedule-live [data-day="{day}"]').click()
    entries = page.locator("#cal-panel .cal-entry")
    opened = min(count, 3)
    for n in range(opened):
        entries.nth(n).locator("summary").click()
    for n in range(opened):  # open at the same time, each with its details
        expect(entries.nth(n)).to_have_js_property("open", True)
        expect(entries.nth(n).locator(".cal-entry-body")).not_to_contain_text("Loading")
    if opened:
        entries.first.locator("summary").click()
        expect(entries.first).to_have_js_property("open", False)
        for n in range(1, opened):
            expect(entries.nth(n)).to_have_js_property("open", True)
