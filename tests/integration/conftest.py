"""pytest's own per-directory hooks: sys.path setup and the `workshop_env`
fixture. Everything reusable that a *test file* needs to import directly
(not auto-injected by pytest) lives in `helpers.py` instead — see that
module's docstring for why `from conftest import X` is the wrong pattern
here, even though it looks natural and worked fine outside Bazel.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import warnings
from pathlib import Path

import pytest

import helpers
from helpers import SRC, WorkshopEnv  # noqa: F401 (re-exported: sys.path setup runs on import)

# Beside a running live install, collect nothing here: these tests would stop its pillars (REQ-000000381,
# helpers.live_pillar_units). Not a skip, which a test run would record as a result.
_in_the_way = helpers.units_in_the_way(os.environ, helpers.live_pillar_units())
collect_ignore_glob = ["test_*.py", "*/test_*.py"] if _in_the_way else []
if _in_the_way:
    warnings.warn(helpers.left_out_reason(_in_the_way), UserWarning, stacklevel=1)


@pytest.fixture
def workshop_env(tmp_path):
    """Unix-domain-socket paths are capped at ~108 bytes — pytest's own
    nested `tmp_path` is often already too long once
    "little-workshop/sockets/<pillar>.sock" is appended, so the XDG
    *state* base gets its own short /tmp directory (docs: M2's original
    finding) while config can stay under `tmp_path`."""
    from common.paths import Paths

    xdg_config = tmp_path / "xdgconfig"
    xdg_state = Path(tempfile.mkdtemp(prefix="lwsit-"))
    paths = Paths(config_home=xdg_config / "little-workshop", state_home=xdg_state / "little-workshop")
    paths.ensure_layout()
    try:
        yield WorkshopEnv(paths=paths, xdg_config=xdg_config, xdg_state=xdg_state)
    finally:
        shutil.rmtree(xdg_state, ignore_errors=True)
