"""tests/integration's fixtures use the live `little-workshop-<pillar>.service` unit names, so the suite
must not even be collected on a host where the live install runs: on 2026-10-05 the weekly Release
job's test run (a bare `pytest` in a worktree) collected it, restarted the live pillars, and left the
release itself running with no worker."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).resolve().parents[1] / "integration"


def _load(name: str, path: Path):
    import sys

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass in it looks its own module up there
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helpers():
    return _load("integration_helpers_under_test", INTEGRATION / "helpers.py")


class _Systemctl:
    def __init__(self, stdout="", error=None):
        self.stdout, self.error, self.calls = stdout, error, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr="")


# --- REQ-000000381: Integration tests never stop the live pillars ---


def test_the_live_units_are_the_active_little_workshop_services(helpers):
    run = _Systemctl("little-workshop-agent.service loaded active running agent\n"
                     "little-workshop-core.service loaded active running core\n")
    assert helpers.live_pillar_units(run) == ["little-workshop-agent.service", "little-workshop-core.service"]
    assert run.calls[0][:3] == ["systemctl", "--user", "list-units"] and "--state=active" in run.calls[0]
    assert helpers.live_pillar_units(_Systemctl("")) == []
    assert helpers.live_pillar_units(_Systemctl(error=FileNotFoundError("no systemctl"))) == []  # no systemd: no live install
    assert helpers.live_pillar_units(_Systemctl(error=subprocess.TimeoutExpired("systemctl", 5))) == []


def test_the_suite_is_left_out_beside_a_live_install_unless_the_run_opts_in(helpers):
    live = ["little-workshop-agent.service"]
    assert helpers.units_in_the_way({}, live) == live
    assert helpers.units_in_the_way({helpers.BESIDE_LIVE_ENV: "1"}, live) == []
    assert helpers.units_in_the_way({}, []) == []
    reason = helpers.left_out_reason(live)
    assert "little-workshop-agent.service" in reason and helpers.BESIDE_LIVE_ENV in reason and "tests/unit" in reason


def test_the_conftest_ignores_every_test_file_when_units_are_in_the_way(monkeypatch):
    import sys

    monkeypatch.syspath_prepend(str(INTEGRATION))
    real = _load("helpers", INTEGRATION / "helpers.py")
    monkeypatch.setitem(sys.modules, "helpers", real)
    monkeypatch.setattr(real, "live_pillar_units", lambda run=None: ["little-workshop-version.service"])
    monkeypatch.delenv(real.BESIDE_LIVE_ENV, raising=False)
    with pytest.warns(UserWarning, match="little-workshop-version.service"):
        conftest = _load("integration_conftest_under_test", INTEGRATION / "conftest.py")
    assert conftest.collect_ignore_glob == ["test_*.py", "*/test_*.py"]

    monkeypatch.setattr(real, "live_pillar_units", lambda run=None: [])
    clear = _load("integration_conftest_clear", INTEGRATION / "conftest.py")
    assert clear.collect_ignore_glob == []
