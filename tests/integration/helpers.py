"""Shared integration-test helpers, importable by plain module name from
any tests/integration/*.py file.

Deliberately NOT named `conftest.py`: pytest collects a `conftest.py` per
directory as a *bare* top-level module named "conftest" (no package
`__init__.py` disambiguates it), and this project has two —
tests/unit/conftest.py and tests/integration/conftest.py. Running both
suites in one session (as `//:test`/`//:coverage` do) means whichever one
Python happens to import first wins the `sys.modules["conftest"]` slot;
a plain venv run and Bazel's sandboxed runfiles tree don't always agree
on which that is. `from conftest import X` inside a test file was
therefore genuinely ambiguous — it silently resolved to the *other*
directory's conftest.py under `bazel test //:coverage` and raised
ImportError, real and reproducible, not a one-off flake. `helpers` has
no name collision with anything else on the path, so it's unambiguous
everywhere. conftest.py itself still exists and still does pytest's own
per-directory jobs (fixtures, sys.path setup) — it just delegates its
reusable pieces here instead of being the thing test files import from.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn

from common.paths import Paths

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
INTEGRATION = ROOT / "tests" / "integration"

# These tests start and stop pillars under the live `little-workshop-<pillar>.service` unit names, so
# on a host where the live install runs they would stop it (2026-09-21, and on 2026-10-05 through the
# weekly Release job's test run). They are then not collected at all — a skip would be recorded as a
# result and mark every requirement of the "Unit tests" suite not done — unless the run says it knows.
BESIDE_LIVE_ENV = "LITTLE_WORKSHOP_INTEGRATION_BESIDE_LIVE"


def live_pillar_units(run=subprocess.run) -> list[str]:
    """The live install's units active on this host: the very ones these tests' fixtures stop and start.
    None found (no systemd, no such unit) means there is nothing to stop."""
    try:
        result = run(["systemctl", "--user", "list-units", "little-workshop-*.service", "--state=active", "--no-legend", "--plain"],
                     capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in (result.stdout or "").splitlines() if line.strip()]


def units_in_the_way(environ, units: list[str]) -> list[str]:
    """The live units that keep this suite from being collected; none when the run opts in."""
    return [] if environ.get(BESIDE_LIVE_ENV) == "1" else list(units)


def left_out_reason(units: list[str]) -> str:
    return (f"tests/integration was not collected: the live install is running ({', '.join(units)}), and these tests "
            f"stop and start those very units. Run tests/unit here, or the integration tests on a host without the live "
            f"install; {BESIDE_LIVE_ENV}=1 runs them anyway.")

for path in (SRC, INTEGRATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _systemd_user_available() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() in ("running", "degraded")


def _live_workshop_running() -> bool:
    """The integration tests start real `little-workshop-<pillar>` units and stop
    them on teardown — the same fixed names a live install uses, so running them
    beside a live Core would kill the live pillars (it did, once). Opt in with
    LWS_ALLOW_INTEGRATION_ON_LIVE=1 only on a machine with nothing live to lose."""
    if os.environ.get("LWS_ALLOW_INTEGRATION_ON_LIVE") == "1":
        return False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "little-workshop.service"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == "active"


requires_systemd_user = pytest.mark.skipif(
    not _systemd_user_available() or _live_workshop_running(),
    reason="systemd --user unavailable, or a live little-workshop is running whose units these tests would stop "
           "(set LWS_ALLOW_INTEGRATION_ON_LIVE=1 to override)",
)

# Confirm-gated deploys arm a watchdog by dispatching `lws deployment
# rollback-timeout` via a detached `systemd-run` unit — `lws` being
# resolvable via PATH inside that unit, the same real dependency M5's
# self-redeploy worker has. Skip cleanly, don't fail the suite, when
# that hasn't been set up (`bazel run //:install`) in this environment.
requires_lws_installed = pytest.mark.skipif(
    shutil.which("lws") is None, reason="lws is not installed (run `bazel run //:install`)"
)


def sweep_transient_units(*patterns: str) -> None:
    """Stops every currently-known unit matching any of `patterns` —
    for confirm-gate watchdog timers and deploy/redeploy worker units a
    test dispatched itself, which `systemd.stop(pillar)` alone never
    reaches since they're not registered under any fixed pillar name.
    Established in M5's self-redeploy integration test; pulled up here
    so every new test that arms a confirm-gate can reuse it."""
    for pattern in patterns:
        subprocess.run(
            ["bash", "-c", f"systemctl --user list-units --all --no-legend '{pattern}' "
             "| awk '{print $1}' | xargs -r systemctl --user stop"],
            check=False,
        )


class UvicornThread:
    """Runs a real FastAPI app on a real Unix domain socket, in-process —
    the pattern every M1-M7 integration test already hand-rolled under its
    own name; pulled up here for every new test added during the testing-
    fortification pass so it's defined once. `systemd`-launched pillars
    still use `common.systemd.start`/`stop` directly, never this — this is
    only for the pillar(s) a given test treats as a fixed dependency it
    doesn't care to exercise crash-relaunch/supervision for."""

    def __init__(self, app, socket_path):
        self._config = uvicorn.Config(app, uds=str(socket_path), log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(100):
            if getattr(self._server, "started", False):
                return
            time.sleep(0.05)
        raise RuntimeError("test server did not start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@dataclass
class WorkshopEnv:
    """`paths` plus the raw XDG_CONFIG_HOME/XDG_STATE_HOME a real
    `systemd.start(..., environment=...)`-launched unit needs forwarded to
    resolve to the exact same isolated location — `Paths.from_env()`
    inside the launched subprocess re-derives `little-workshop/...` from
    whatever XDG base it's handed, so the test and the subprocess must be
    handed the identical bases, not just the identical Paths object."""

    paths: Paths
    xdg_config: Path
    xdg_state: Path

    @property
    def env(self) -> dict:
        return {
            "PYTHONPATH": str(SRC),
            "XDG_CONFIG_HOME": str(self.xdg_config),
            "XDG_STATE_HOME": str(self.xdg_state),
        }
