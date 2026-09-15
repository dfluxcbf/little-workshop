import pytest

from common.paths import Paths
from version.resolve import ProjectResolutionError, resolve_project_path


def test_resolve_requires_project_id(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    with pytest.raises(ProjectResolutionError):
        resolve_project_path(paths, None)


def test_resolve_fails_cleanly_when_core_unreachable(tmp_path):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    with pytest.raises(ProjectResolutionError, match="unavailable"):
        resolve_project_path(paths, "little-widget", timeout=0.5)
