"""New job: one page that starts a job right away or schedules it (REQ-000000263).

Only the form's own behaviour is exercised; nothing is submitted, so a run
never starts or schedules a real job on whatever app it is pointed at.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_when_defaults_to_right_away_with_no_schedule_fields(page: Page):
    page.goto("/jobs/new")
    expect(page.locator("h1")).to_have_text("New job")
    expect(page.locator("#when")).to_have_value("immediate")
    expect(page.locator("#at")).to_be_hidden()
    expect(page.locator("#time_of_day")).to_be_hidden()
    expect(page.locator("#job-submit")).to_have_text("Start job")


def test_choosing_once_shows_the_date_and_time_and_turns_the_button_into_schedule(page: Page):
    page.goto("/jobs/new")
    page.select_option("#when", "once")
    expect(page.locator("#at")).to_be_visible()
    expect(page.locator("#time_of_day")).to_be_hidden()
    expect(page.locator("#job-submit")).to_have_text("Schedule job")


def test_choosing_every_week_shows_weekdays_and_time_and_back_to_right_away_hides_them(page: Page):
    page.goto("/jobs/new")
    page.select_option("#when", "recurring")
    expect(page.get_by_text("Mon", exact=True)).to_be_visible()
    expect(page.locator("#time_of_day")).to_be_visible()
    expect(page.locator("#at")).to_be_hidden()
    page.select_option("#when", "immediate")
    expect(page.locator("#time_of_day")).to_be_hidden()
    expect(page.locator("#job-submit")).to_have_text("Start job")


def test_the_old_schedule_job_page_lands_on_new_job_and_is_off_the_toolbar(page: Page):
    page.goto("/jobs/schedule")
    expect(page).to_have_url(re.compile(r"/jobs/new$"))
    expect(page.locator('nav.tabbar a[href="/jobs/new"]')).to_have_count(1)
    expect(page.locator('nav.tabbar a[href="/jobs/schedule"]')).to_have_count(0)


def test_phone_viewport_keeps_the_when_fields_usable(page: Page):
    page.set_viewport_size({"width": 412, "height": 915})
    page.goto("/jobs/new")
    page.select_option("#when", "recurring")
    expect(page.locator("#time_of_day")).to_be_visible()
    expect(page.locator("#job-submit")).to_be_in_viewport()
