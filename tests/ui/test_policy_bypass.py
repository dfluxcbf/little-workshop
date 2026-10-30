"""REQ-000000395: the Dashboard's policy-bypass warning and its button."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


def test_the_bypass_warning_counts_down_and_its_button_switches_it_off(page: Page):
    page.goto("/policies")
    banner = page.locator("#bypass-banner")
    if not banner.count():
        pytest.skip("bypass_policies is FALSE on this app: nothing to warn about (raising it needs `sudo lws`)")
    expect(banner).to_contain_text("bypass_policies is TRUE")
    expect(page.get_by_test_id("bypass-remaining")).to_contain_text(":")
    page.get_by_role("button", name="Set to FALSE now").click()
    expect(page.locator("#bypass-banner")).to_have_count(0)
