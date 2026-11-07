import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _private_state_home(tmp_path_factory, monkeypatch):
    """Every test keeps little-workshop state (the version pillar's per-project state among it) in a
    directory of its own, never the user's ~/.local/state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
