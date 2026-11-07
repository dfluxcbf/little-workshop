import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _bare_windows_program_names(monkeypatch):
    """Windows programs keep their bare names (`git.exe`) in every test, whether or not this machine has a
    Windows drive: `windows_exe`'s folder lookup is tested on its own, against a scratch drive."""
    from common import windows
    monkeypatch.setattr(windows, "WINDOWS_EXE_DIRS", ())


@pytest.fixture(autouse=True)
def _private_state_home(tmp_path_factory, monkeypatch):
    """Every test keeps little-workshop state (the version pillar's per-project state among it) in a
    directory of its own, never the user's ~/.local/state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
