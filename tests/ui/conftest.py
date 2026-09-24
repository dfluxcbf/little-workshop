"""Browser tests for the Dashboard, run against a live app given by `--base-url`.

little-workshop starts the branch's app and passes `--base-url`; a plain
`pytest` with no app to point at skips these instead of failing.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _needs_a_running_app(base_url):
    if not base_url:
        pytest.skip("no --base-url: these tests need a running Dashboard")
