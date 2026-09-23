"""pytest's own per-directory hooks: sys.path setup and the `workshop_env`
fixture. Everything reusable that a *test file* needs to import directly
(not auto-injected by pytest) lives in `helpers.py` instead — see that
module's docstring for why `from conftest import X` is the wrong pattern
here, even though it looks natural and worked fine outside Bazel.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from helpers import SRC, WorkshopEnv  # noqa: F401 (re-exported: sys.path setup runs on import)


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
